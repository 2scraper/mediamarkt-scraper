"""
product_parser.py
-----------------
Extracts rows from MediaMarkt category grids, search results and product
detail pages. This module is where essentially all of the site knowledge in
this repo lives; the engines carry about a dozen constants and nothing else.

Where the data comes from
-------------------------
MediaMarkt publishes schema.org JSON-LD on every page kind that carries
products, so this repo uses the family's normal order: structured data first,
CSS/URL patterns only as a fallback. Measured on live captures taken
2026-09-09 from a German residential exit:

    /de/category/filme-serien-486.html      2 ld+json  (ItemList, BreadcrumbList)
    /de/search.html?query=usb-c+kabel+2m    1 ld+json  (ItemList)
    /de/product/..._-2920911.html           3 ld+json  (BuyAction, Language,
                                                        BreadcrumbList)

and on the listing pages the ItemList is COMPLETE for the page: 12 entries
against 12 `[data-test="mms-product-card"]` tiles, on all six captured
category pages and both search pages. So the fallback path is genuinely a
fallback here, not a second half of the job.

The detail page's Product is nested inside a `BuyAction` as its `object`,
which is legal schema.org and is the shape a naive `@type == "Product"` scan
misses entirely — the same class of trap as products living under `@graph`
instead of `itemListElement`. Read through the wrapper (`_products_in_ld`
does) or a detail run returns nothing while the page plainly has everything.

WARNING — the two strikethrough prices
--------------------------------------
This is the single most dangerous thing to get wrong on this site, and a
parser that treats "strikethrough == was-price" produces garbage rather than
an error. MediaMarkt renders TWO different struck-through prices with nearly
identical markup:

    data-test="mms-strike-price-type-rrp"   "UVP 59,99 €"
        The manufacturer's recommended price. Higher than the current price.
        This IS the original price.

    data-test="mms-strike-price-type-lop"   "Tiefstpreis (30 Tage): 299,– €"
        The LOWEST price charged in the preceding 30 days, which EU price
        indication law (the Omnibus directive) requires beside a reduced
        price. It is normally LOWER than the price being charged now.

Both appear in quantity — 22 rrp and 14 lop nodes across the captured pages —
and on a tile carrying `lop` the naive read gives original_price=299 next to
price=349, i.e. a negative discount on a product that is not discounted at
all. So `original_price` is read from `rrp` ONLY, and `lop` gets its own
column (`lowest_price_30d`) where it is useful rather than harmful.

Two more things inside the price node that are not the price
------------------------------------------------------------
`[data-test="mms-price"]` also contains an instalment line ("Bezahle in 18
Raten à 19,39 €") and a VAT/shipping note. Reading the first price out of the
node's whole text therefore risks returning a monthly instalment as the
product's price. Every read here is scoped to the price node with the strike
and additional-info subtrees removed first (`_current_price_node`), which is
why this file never calls `_prices_in` on the text of a whole tile except on
the documented-as-weaker URL fallback path.

Prices are also written in a form the family's number parser did not know:
"299,– €" uses an en dash where the cents would be. It appears 128 times
across the captures, so it is the ordinary way this site writes a round
price, not an edge case.

Field coverage, measured, so a null is not read as a bug
--------------------------------------------------------
96 tiles across six captured category pages and two search pages, .de:

    sku / title / url / price / currency / image    96/96
    original_price (UVP)                            22/96
    lowest_price_30d                                14/96
    brand                                            0/96   <- see Product

`rating` is null on every product with no reviews yet, which on a
fast-moving catalogue is a large minority of them — that is the site having
no rating, not this parser losing one. `review_count` is 0 rather than null
on those rows, because "nobody has reviewed this" is a fact the page states,
unlike the average it cannot compute.

`brand` is null on EVERY listing row. See `Product` in output_writer.py for
why guessing it from the title was rejected. It is populated by
--mode product.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote

from bs4 import BeautifulSoup

from output_writer import Product, SOURCE_DEFAULT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The country sites
# ---------------------------------------------------------------------------
# Not a guessed list: these are exactly the hosts mediamarkt.de declares in
# its own `<link rel="alternate" hreflang=...>` set, read off a live capture
# on 2026-09-09. Using the site's own declaration means the table is right by
# construction and can be re-derived with one grep when the group opens or
# closes a market.
#
# MediaWorld is the same company and the same platform under the Italian
# brand, so it is in the table. Saturn (saturn.de, saturn.at) is a sibling
# brand on similar markup but is NOT in mediamarkt.de's hreflang set and has
# not been verified here, so it is deliberately absent rather than assumed.
#
# The currency is a FALLBACK only. Every structured price on this site names
# its own currency in `offers.priceCurrency`, and that always wins. This table
# is consulted when a DOM-only read produced a bare symbol.
HOSTS: Dict[str, str] = {
    "mediamarkt.de": "EUR",
    "mediamarkt.at": "EUR",
    "mediamarkt.es": "EUR",
    "mediaworld.it": "EUR",
    "mediamarkt.nl": "EUR",
    "mediamarkt.be": "EUR",
    "mediamarkt.lu": "EUR",
    "mediamarkt.pl": "PLN",
    "mediamarkt.ch": "CHF",
    "mediamarkt.com.tr": "TRY",
    "mediamarkt.hu": "HUF",
}


def site_host(url: str) -> str:
    """The bare hostname of `url` with a leading "www." removed.

    Returns "" for anything unparseable, so a caller can tell "not one of
    ours" from "mediamarkt.de" without a try/except at every call site.
    """
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_supported_host(url: str) -> bool:
    return site_host(url) in HOSTS


def host_currency(url: str) -> Optional[str]:
    return HOSTS.get(site_host(url))


SELECTORS = {
    # The fallback anchor, and the engines' "has the grid painted?" probe.
    # A MediaMarkt product URL always contains "/product/" and ends in the
    # article number; anchoring on the URL rather than a class is the family
    # rule, and it matters more than usual here because this site's classes
    # are build hashes ("sc-59b6826e-0 kGZxQX", "mms-ui-gEYBWy") that change
    # on every deploy.
    "item_link": 'a[href*="/product/"]',
    # The tile. `data-test` attributes are MediaMarkt's own test hooks: they
    # are semantic, stable across the captured locales, and the only
    # non-hashed handle the markup offers.
    "product_card": '[data-test="mms-product-card"]',
    "title": '[data-test="product-title"]',
    "price_block": '[data-test="mms-price"]',
    # Read the WARNING at the top of this module before touching either.
    "strike_rrp": '[data-test="mms-strike-price-type-rrp"]',
    "strike_lop": '[data-test="mms-strike-price-type-lop"]',
    "strike_any": '[data-test^="mms-strike-price-type-"]',
    # The VAT note and the instalment line live in here. Excluded from every
    # price read; see `_current_price_node`.
    "price_extra": '[data-test^="additional-info"]',
    "rating": '[data-test="mms-customer-rating"]',
    "rating_count": '[data-test="mms-customer-rating-count"]',
    "delivery": '[data-test^="mms-cofr-delivery_"]',
    # Detail page. Its price is NOT the tile's price block: MediaMarkt
    # renders it split across three nodes (see _detail_dom_price), and the
    # container below is the ONLY correctly-scoped handle on it.
    "detail_price": '[data-test="mms-product-price"]',
    "detail_price_whole": '[data-test="branded-price-whole-value"]',
    "detail_price_decimal": '[data-test="branded-price-decimal-value"]',
    "detail_price_currency": '[data-test="branded-price-currency"]',
}


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
_CURRENCY_SYMBOLS = {
    "€": "EUR", "£": "GBP", "$": "USD", "₺": "TRY", "zł": "PLN", "Ft": "HUF",
}

# Symbols whose meaning depends on which country site rendered them. Listed
# separately so a match can be DECLINED rather than guessed when the host is
# unknown.
_HOST_RESOLVED_SYMBOLS = {"$"}

# Matched longest-first so a prefix is not swallowed by a bare symbol.
_PREFIXED_SYMBOLS = {"CHF": "CHF", "TL": "TRY"}

# An explicit allowlist, not a bare [A-Z]{3}: the latter matches any three
# capitals next to a number, so an energy label ("C 149 kWh") or a model name
# would start producing phantom prices. Every entry is a real ISO 4217 code.
_CURRENCY_CODES = frozenset("""
    EUR PLN CHF TRY HUF USD GBP SEK NOK DKK CZK RON BGN
""".split())

# Space characters used as a THOUSANDS separator. A rendered page uses a
# no-break variant so the number does not wrap: plain space, NBSP (U+00A0),
# narrow NBSP (U+202F) and thin space (U+2009) all appear. Polish and
# Hungarian group with a space, so missing these does not merely mis-group a
# number on those two sites — it fails to match the price at all.
_GROUP_SPACES = "    "

# Amount, in any of the three grouping conventions:
#   1,234.56 / 1.234,56 / 1 234,56 / 125 / 125.00
# The space-grouped form deliberately requires FULL groups of exactly three
# digits, so a stray "5 200" out of two unrelated numbers cannot merge.
_AMOUNT = (r"\d{1,3}(?:[" + _GROUP_SPACES + r"]\d{3})+(?:[.,]\d{1,2})?"
           r"|[\d.,]+(?:[.,]\d{1,2})?")
_PREFIXED_RE = "|".join(re.escape(s) for s in
                        sorted(_PREFIXED_SYMBOLS, key=len, reverse=True))
_BARE_RE = "|".join(re.escape(s) for s in sorted(
    set(_CURRENCY_SYMBOLS) | _HOST_RESOLVED_SYMBOLS, key=len, reverse=True))
_SPACE = "[" + _GROUP_SPACES + "]?"
_PRICE_RE = re.compile(
    r"(?:(" + _PREFIXED_RE + r"|" + _BARE_RE + r")" + _SPACE + r"(" + _AMOUNT + r")"
    r"|(" + _AMOUNT + r")" + _SPACE + r"(" + _BARE_RE + r")"
    r"|\b([A-Z]{3})" + _SPACE + r"(" + _AMOUNT + r")"
    r"|\b(" + _AMOUNT + r")" + _SPACE + r"([A-Z]{3})\b)"
)

# "299,– €" — a round price written with a dash where the cents go. German
# and Dutch retail both write it; MediaMarkt uses it as its NORMAL form for a
# whole-euro price (128 occurrences across the captured pages, against 33
# written "299,00"). Without this substitution the amount pattern stops at
# "299," and _normalize_amount then reads a trailing separator, so the price
# came back wrong or not at all.
#
# The lookahead matters: only a dash that is NOT followed by a digit is a
# cents placeholder, so a range or a hyphenated model number cannot merge.
_DASH_DECIMAL_RE = re.compile(r"(\d),[–—-](?!\d)")

# The article number, recovered from the product URL. MediaMarkt's slugs are
# full of other numbers — model designations, capacities, dimensions
# ("_koenic-kfk-631-1-c-in-...-c-149-kwh-1850-mm-hoch-inox-2882323.html") —
# so this anchors on the END of the path and takes the last group only.
# Verified against the detail page's own `sku` field, which agrees.
_SKU_IN_URL_RE = re.compile(r"-(\d{5,})\.html(?:$|[?#])")


def _normalize_amount(raw: str) -> Optional[float]:
    """Parse a price amount written in either decimal convention.

    When BOTH separators appear, 'whichever comes last is the decimal point'
    disambiguates on its own. When only one appears, that is ambiguous
    between a thousands grouping and a decimal point — and no currency this
    parser recognises has a 3-digit subunit. So a single separator followed
    by exactly 3 digits is a thousands grouping; anything else is a decimal.
    """
    for space in _GROUP_SPACES:
        raw = raw.replace(space, "")

    last_dot, last_comma = raw.rfind("."), raw.rfind(",")
    if last_dot != -1 and last_comma != -1:
        norm = (raw.replace(",", "") if last_dot > last_comma
                else raw.replace(".", "").replace(",", "."))
    else:
        sep_pos = max(last_dot, last_comma)
        trailing = raw[sep_pos + 1:] if sep_pos != -1 else ""
        if len(trailing) == 3 and trailing.isdigit():
            norm = raw.replace(".", "").replace(",", "")
        else:
            norm = raw.replace(",", ".")
    try:
        return float(norm)
    except ValueError:
        return None


def _prices_in(text: str, host_cur: Optional[str] = None
               ) -> Tuple[List[float], Optional[str]]:
    """Return ([amounts], currency_code_or_None) for all prices in `text`.

    `host_cur` resolves a symbol that cannot name itself using the country
    site whose page rendered it. That is still a guess, but a much better
    one: if the page printed a local symbol at all, the visitor is being
    served that market's own currency. When the host is unknown, such a
    symbol yields a price with currency None rather than a plausible wrong
    code.
    """
    amounts, currency = [], None
    for m in _PRICE_RE.finditer(_DASH_DECIMAL_RE.sub(r"\1,00", text)):
        sym = m.group(1) or m.group(4)
        code = m.group(5) or m.group(8)
        if code and code not in _CURRENCY_CODES:
            # Three capitals next to a number that are not a real currency —
            # an energy class, a spec, a model name. Not a price.
            continue
        raw = m.group(2) or m.group(3) or m.group(6) or m.group(7)
        if currency is None:
            if code:
                currency = code
            elif sym in _PREFIXED_SYMBOLS:
                currency = _PREFIXED_SYMBOLS[sym]
            elif sym in _HOST_RESOLVED_SYMBOLS:
                currency = host_cur
            else:
                currency = _CURRENCY_SYMBOLS.get(sym)
        amount = _normalize_amount(raw)
        if amount is not None:
            amounts.append(amount)
    return amounts, currency


def _first_price(node, host_cur: Optional[str] = None
                 ) -> Tuple[Optional[float], Optional[str]]:
    """(amount, currency) from a price node's text, or (None, None)."""
    if node is None:
        return None, None
    amounts, currency = _prices_in(node.get_text(" ", strip=True), host_cur)
    return (amounts[0] if amounts else None), currency


# ---------------------------------------------------------------------------
# Page state: what came back, and what to do about it
# ---------------------------------------------------------------------------
# MediaMarkt answers a request in four ways and three of them are not
# content. Collapsing them loses the distinction between "we are blocked",
# "this URL has no products on it" and "the catalogue ran out", which are the
# three things a consumer most needs to tell apart.
#
#   blocked    HTTP 403 carrying the retailer's own branded error page.
#              Measured 2026-09-09: identical bytes for every URL on the
#              host, from a datacentre exit, on .de/.es/.nl/.pl and
#              mediaworld.it alike. -> rotate the exit. A captcha solve buys
#              nothing here, because there is no challenge to solve.
#   captcha    a challenge widget. Detection stays broad (family policy: geo
#              and scenario change which vendor appears) even though none was
#              observed on the captured pages.
#   empty      HTTP 200, a real page, no products on it. A hub category
#              (/de/category/notebooks-680.html) is a landing page of
#              sub-categories and legitimately has none.
#              -> EXIT_NO_PRODUCTS, not EXIT_BLOCKED.
#   content    a grid, or a detail page.
#
# Note that "hub category" is not the same as "no product cards in the
# markup". /de/category/tv-audio-202.html renders ZERO
# `mms-product-card` elements server-side and still publishes an ItemList of
# 12 real televisions, which this parser returns and a DOM-only parser would
# lose completely. That is the JSON-LD-first order earning its place, and it
# is why "empty" is decided on what was PARSED rather than on what selectors
# matched.
#
# THE STATUS CODE IS THE PRIMARY SIGNAL, and that is a deliberate inversion of
# how the rest of this family detects a block. MediaMarkt's block page carries
# no vendor marker at all — no Akamai reference id, no "Request unsuccessful",
# nothing — because it is the shop's own error page served with a 403. Worse,
# it is DIFFERENT PER LOCALE: the German one is the full site chrome wrapped
# around "Ups, hier stimmt gerade etwas nicht", while the Spanish one is a
# bare inline-styled page whose entire visible text is "MediaMarkt". Text
# markers alone would have caught the first and missed the second, so they
# are the secondary signal and the status is the first.
#
# The structural signal below carries more weight than either, and unlike the
# text it is locale-independent: every page MediaMarkt actually serves pulls
# its images from its own asset host, and neither block page references it
# once. Measured across the captures — 3 to 7 matching lines on every real
# page (home, hub, grid, detail), 0 on the German block page and 0 on the
# Spanish one.
_CDN_MARKER = "assets.mmsrg.com"

BLOCK_TEXT_MARKERS = (
    "Ups, hier stimmt gerade etwas nicht",
    "auf einen technischen F",           # "technischen Fehler", umlaut-safe
    "Manutenzione sito",
    "Aggiornamento in corso",
)

# Kept broad on purpose: which vendor appears depends on the exit country and
# on what the address has been doing, so a narrow list is how a challenge gets
# reported as an empty page. None of these was observed on the captured pages;
# they cost nothing to keep and are the family's standing policy.
BOT_CHALLENGE_MARKERS = {
    "recaptcha": ("www.google.com/recaptcha", "grecaptcha", "g-recaptcha"),
    "hcaptcha": ("hcaptcha.com", "h-captcha"),
    "turnstile": ("challenges.cloudflare.com", "cf-turnstile"),
    "datadome": ("captcha-delivery.com", "datadome"),
    "akamai": ("_abck", "ak_bmsc", "AkamaiGHost"),
}

# Presence of a product grid, checked as raw text rather than by parsing:
# `detect_page_state` runs on every fetch, including ones that turn out to be
# a 461 KB block page, and building a soup for that is wasted work.
_CARD_MARKER = 'data-test="mms-product-card"'


# Anything a browser EXTENSION injected is not the site talking, and has to
# come out before the markers above are looked for.
#
# This is not hypothetical tidiness. The 2Captcha Scraping Browser API ships
# an auto-solve extension that injects its own captcha hunters into every
# page it loads:
#
#     <script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/
#                  content/captcha/turnstile/hunter.js"
#             data-ts-input="cf-turnstile-response"></script>
#
# So `cf-turnstile` appears in the markup of every page fetched over
# `--cdp-endpoint`, including a perfectly good category grid with twelve
# products on it. The first live run of this scraper reported exit 3
# ("Blocked by turnstile") on a 1.8 MB page holding the full catalogue for
# exactly this reason — the solver's own tooling was mistaken for the site's
# defences. Anyone pointing a marker-based detector at a remote browser will
# hit this.
_EXTENSION_TAG_RE = re.compile(
    r"<script[^>]*\b(?:chrome|moz)-extension://[^>]*>(?:.*?</script>)?",
    re.IGNORECASE | re.DOTALL)


def detect_bot_challenge(html: str, url: Optional[str] = None) -> Optional[str]:
    """Name the challenge vendor the SITE put on the page, or None.

    Broad by design about vendors, narrow about provenance: a caller decides
    what to DO about a challenge, but a marker injected by the local browser
    is not one to begin with. See _EXTENSION_TAG_RE.
    """
    if not html:
        return None
    cleaned = _EXTENSION_TAG_RE.sub("", html)
    for vendor, markers in BOT_CHALLENGE_MARKERS.items():
        if any(m in cleaned for m in markers):
            return vendor
    return None


def detect_page_state(html: str, status: Optional[int] = None,
                      url: Optional[str] = None) -> str:
    """One of "blocked", "captcha", "empty", "content".

    `status` is optional because not every engine path can see it — a page
    fetched over CDP reports one, a page read back out of a `--dump-html`
    file does not — but when it IS available it decides the blocked case on
    its own. See the note above on why the text markers cannot carry that
    weight here.
    """
    if status == 403:
        return "blocked"
    if not html:
        return "blocked" if status and status >= 400 else "empty"
    if any(m in html for m in BLOCK_TEXT_MARKERS):
        return "blocked"
    if _CARD_MARKER in html or "/product/" in html:
        # Content wins over a challenge marker that is merely PRESENT: a
        # rendered grid with a captcha script somewhere on the page is a page
        # we can read, and reporting it as a challenge would spend a solve on
        # a page that needs none. This is the same "detected is not blocking"
        # rule the captcha default follows.
        return "content"
    if detect_bot_challenge(html, url):
        return "captcha"
    if _CDN_MARKER not in html:
        # Not content, not a challenge, and not built out of MediaMarkt's own
        # assets — so it is not a page the shop served us. This is what
        # catches the Spanish block page, whose entire visible text is the
        # word "MediaMarkt" and which no text marker can distinguish from a
        # thin but genuine page.
        return "blocked"
    return "empty"


# ---------------------------------------------------------------------------
# URLs: which kind of page, which page number, which category
# ---------------------------------------------------------------------------
def listing_kind(url: str) -> str:
    """"category", "search", "product" or "other"."""
    path = urlparse(url or "").path
    if "/product/" in path:
        return "product"
    if "/category/" in path:
        return "category"
    if "/search.html" in path or "/specials/" in path:
        return "search"
    return "other"


# `?page=N` is MediaMarkt's own pagination parameter and it is used by BOTH
# listing kinds — unlike some sites in this family, where search and grid
# pages disagree. Verified two ways on 2026-09-09, which is what makes it safe
# to construct page URLs up front and therefore safe to fetch them
# concurrently:
#
#   1. The document head of page 1 carries
#      <link rel="next" href=".../filme-serien-486.html?page=2">, i.e. the
#      site's own next-link AGREES with what this convention builds. No
#      cursor, no token, nothing this function cannot reproduce.
#   2. Three consecutive category pages and two search pages returned 12
#      distinct products each, 0 skus shared between any pair.
PAGE_PARAM = "page"


def page_url(url: str, page_num: int) -> str:
    """`url` with its page parameter set to `page_num`.

    Existing query parameters are preserved and the page parameter is
    REPLACED rather than appended — a search URL already carries `query`, and
    doubling `page` would leave the site to pick one.
    """
    parts = urlparse(url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k != PAGE_PARAM]
    if page_num > 1:
        params.append((PAGE_PARAM, str(page_num)))
    return urlunparse(parts._replace(query=urlencode(params)))


def page_number_from_url(url: str) -> Optional[int]:
    """The page number `url` addresses; 1 when it carries no page parameter."""
    if not url:
        return None
    for k, v in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if k == PAGE_PARAM:
            try:
                return int(v)
            except ValueError:
                return None
    return 1


# Path segments that are routing, not a category name. `category` and
# `product` are MediaMarkt's route prefixes; the two-letter entries are the
# locale segment that .de, .nl, .be, .ch and .es put in front of everything.
_NOT_A_CATEGORY = {"category", "product", "search.html", "specials", "shop",
                   "de", "en", "es", "nl", "fr", "it", "pl", "hu", "tr", "lu"}


def category_from_url(url: str) -> Optional[str]:
    """A human-readable category label out of a listing URL, or None.

    "/de/category/k%C3%BChlen-gefrieren-32.html" -> "kühlen-gefrieren"

    The trailing id is dropped so the label survives MediaMarkt renumbering a
    category, and the `.html` suffix goes with it. A search URL has no
    category in its path, so this returns None rather than inventing one from
    the query — the query is the caller's own input and belongs in
    `--category` if the caller wants it recorded.
    """
    if not url:
        return None
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    for seg in reversed(parts):
        if seg.lower() in _NOT_A_CATEGORY:
            continue
        seg = re.sub(r"\.html?$", "", seg)
        seg = re.sub(r"-\d+$", "", seg)
        if seg and not seg.isdigit():
            return seg
    return None


def _canonical_product_url(host: str, href: str) -> str:
    """An absolute product URL from a possibly-relative href."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if not href.startswith("/"):
        href = "/" + href
    return "https://www.{}{}".format(host, href)


def sku_from_url(url: Optional[str]) -> Optional[str]:
    """MediaMarkt's article number out of a product URL, or None."""
    if not url:
        return None
    m = _SKU_IN_URL_RE.search(url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Small field helpers
# ---------------------------------------------------------------------------
_INVISIBLE_RE = re.compile("[​‌‍﻿⁠]")

# MediaMarkt states the rating in the star widget's aria-label, in the local
# language: "Durchschnittliche Produktbewertung: 4.6 von 5 Sternen". The
# visible stars are SVGs with no text, so this label is the only DOM copy of
# the number. The JSON-LD carries it too and more precisely (4.5966 against
# the label's 4.6), which is why structured data wins where both exist.
_RATING_RE = re.compile(r"([\d.,]+)\s*(?:von|out of|sur|su|de|van|z)\s*5",
                        re.IGNORECASE)


def _clean_text(value):
    """Collapse whitespace and drop zero-width characters."""
    if not value:
        return None
    return " ".join(_INVISIBLE_RE.sub("", str(value)).split()) or None


def _to_float(text) -> Optional[float]:
    if text is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(text))
    if not m:
        return None
    try:
        return float(m.group().replace(",", "."))
    except ValueError:
        return None


def _int_from(text) -> Optional[int]:
    """An integer out of a grouped number, in any locale's grouping.

    "87,349" / "87.349" / "87 349" all mean the same count. Every separator
    is dropped rather than interpreted: a review count has no decimal part,
    so there is nothing to disambiguate.
    """
    if text is None:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _rating_from(node) -> Optional[float]:
    """Stars out of five, from the widget's accessible label."""
    if node is None:
        return None
    labels = []
    own = node.get("aria-label") if hasattr(node, "get") else None
    if own:
        labels.append(own)
    if hasattr(node, "select"):
        labels += [el.get("aria-label", "") for el in node.select("[aria-label]")]
    for text in labels:
        if not text:
            continue
        m = _RATING_RE.search(text)
        if m:
            value = _to_float(m.group(1))
            # A rating is out of five by definition; anything else means the
            # pattern matched something that was not a rating.
            #
            # Zero is excluded deliberately, and this is not a rounding
            # guard. MediaMarkt renders the star widget on EVERY tile, and an
            # unrated product gets "Durchschnittliche Produktbewertung: 0 von
            # 5 Sternen" with a count of 0 — the widget's empty state, not a
            # score. Reading it as 0.0 filled `rating` on 84 of 84 rows of a
            # test run with a column that looked complete and told a consumer
            # that a brand-new release is rated zero stars. An average of 0
            # is not expressible on a 1-5 scale, so it always means "no
            # rating yet", which is a null.
            if value is not None and 0 < value <= 5:
                return value
    return None


def _discount_from(price: Optional[float], original_price: Optional[float]
                   ) -> Optional[float]:
    """Percentage off, computed from the two prices rather than read.

    MediaMarkt prints a "-16%" badge beside a reduced price. It is rendered
    from a different field and rounds differently (-16% against an actual
    -16.7%), so the arithmetic on two numbers this parser read itself is the
    trustworthy source and nothing is read from the badge.

    Returns None rather than 0 or a negative number when the "original" is
    not above the price: that combination means the two figures are not what
    they were taken for, and a 0 would hide it.
    """
    if original_price and price is not None and original_price > price:
        return round((1 - price / original_price) * 100, 1)
    return None


# ---------------------------------------------------------------------------
# How many products the listing says it has
# ---------------------------------------------------------------------------
# Every listing page prints "12 von 42930" ("12 of 42930") under the grid.
# That makes completeness ARITHMETIC rather than a guess, the same way a
# published rank does on a best-seller grid elsewhere in this family: a run
# that asked for 5 pages of a 3-page category should stop, and a merged
# output holding fewer rows than the pages it fetched lost something.
#
# Localised, so the connecting word varies; the two numbers around it do not.
_TOTAL_RE = re.compile(
    r"(\d[\d.,    ]*)\s*(?:von|of|de|di|van|z|közül)\s+"
    r"(\d[\d.,    ]*\d)", re.IGNORECASE)


def total_results(html: str) -> Optional[int]:
    """The catalogue size the listing page reports, or None.

    Read from the page's own "shown of total" line. Reported in the run
    metadata so a consumer can see what fraction of a category a run took.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=_TOTAL_RE):
        m = _TOTAL_RE.search(str(node))
        if not m:
            continue
        shown, total = _int_from(m.group(1)), _int_from(m.group(2))
        # The line reads "<shown> of <total>", so a first number larger than
        # the second is some other pair that happened to sit around the same
        # word — a date range, a warranty term.
        if shown is not None and total is not None and 0 < shown <= total:
            return total
    return None


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------
def _ld_blocks(soup) -> List[dict]:
    """Every parseable ld+json block on the page.

    A block that does not parse is skipped with a warning rather than
    aborting the page: one malformed script must not cost the other eleven
    products.
    """
    out: List[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except ValueError as exc:
            logger.warning("skipping unparseable ld+json block: %s", exc)
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _ld_image(value) -> Optional[str]:
    """The first usable image URL out of any of schema.org's four shapes.

    `image` is legally a string, a list of strings, an ImageObject, or a list
    of ImageObjects. MediaMarkt uses the first on listings and the second on
    detail pages; the other two are handled because they are legal and cost
    three lines.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("url") or value.get("contentUrl") or None
    if isinstance(value, list):
        for item in value:
            found = _ld_image(item)
            if found:
                return found
    return None


def _ld_offer(node: dict) -> dict:
    """The offer dict out of `node`, whatever shape `offers` is in.

    `"offers": null` is explicit and legal, and a `.get("offers", {})`
    default does NOT apply to a key that is present and null — that is an
    AttributeError one line later. `offers` may also be a list, and a list
    may hold things that are not dicts.
    """
    offers = node.get("offers")
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list):
        for item in offers:
            if isinstance(item, dict):
                return item
    return {}


def _ld_rating(node: dict) -> Tuple[Optional[float], Optional[int]]:
    """(rating, count) from an aggregateRating, or (None, None).

    Handles the explicit-null case, and reads BOTH count spellings:
    MediaMarkt writes `reviewCount` in a listing's ItemList and `ratingCount`
    on a detail page. Both are legal schema.org and mean the same thing here,
    so a parser that knows only one reports a null count on one of the two
    modes while the pages plainly show it.
    """
    agg = node.get("aggregateRating")
    if not isinstance(agg, dict):
        return None, None
    # Same reasoning as _rating_from: 0 is the empty state, not a score.
    rating = _to_float(agg.get("ratingValue"))
    if rating is not None and not 0 < rating <= 5:
        rating = None
    count = _int_from(agg.get("reviewCount"))
    if count is None:
        count = _int_from(agg.get("ratingCount"))
    return rating, count


def _ld_in_stock(offer: dict) -> Optional[bool]:
    """True/False from `offers.availability`, or None if it says nothing."""
    avail = offer.get("availability")
    if not isinstance(avail, str) or not avail:
        return None
    tail = avail.rsplit("/", 1)[-1].lower()
    if tail in ("instock", "onlineonly", "instoreonly", "limitedavailability",
                "presale"):
        return True
    if tail in ("outofstock", "soldout", "discontinued", "backorder"):
        return False
    return None


def _products_in_ld(blocks) -> List[dict]:
    """Every Product node in `blocks`, through each wrapper that is legal.

    Four shapes, all of which this site or the standard produces:
      * ItemList.itemListElement[].item      — listing pages
      * BuyAction.object                     — detail pages (!)
      * @graph[]                             — not used here, handled anyway
      * a bare Product                       — ditto

    The BuyAction case is the one that matters: a scan for
    `@type == "Product"` at the top level finds NOTHING on a MediaMarkt
    detail page, and would report a fully-populated page as having no
    product at all.
    """
    found: List[dict] = []
    seen_ids = set()

    def visit(node, depth=0):
        # Bounded so a self-referential document cannot spin. Six levels is
        # three more than the deepest shape above needs.
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                visit(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if id(node) in seen_ids:
            return
        seen_ids.add(id(node))
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" in types:
            found.append(node)
            return
        for key in ("itemListElement", "@graph", "object", "item", "mainEntity"):
            if key in node:
                visit(node[key], depth + 1)

    visit(blocks)
    return found


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------
# How far to widen from a product link when looking for its tile, on the
# fallback path only. The primary path never needs this — the card element is
# the scope — but the fallback starts from a link and has to find the box
# around it. Capped so a malformed document cannot walk to <body>.
_MAX_TILE_WIDEN = 8


def _distinct_skus(node) -> int:
    """How many DIFFERENT products the links under `node` point at."""
    return len({sku_from_url(a.get("href"))
                for a in node.select(SELECTORS["item_link"])
                if sku_from_url(a.get("href"))})


def _tile_scope(anchor):
    """The outermost ancestor of `anchor` that still covers exactly ONE product.

    Counts DISTINCT article numbers, not links, and that distinction is the
    whole function. A MediaMarkt tile links to its product TWICE — once from
    the image, once from the title — so the obvious "stop when this ancestor
    holds more than one product link" never leaves the anchor itself: the
    card is already at two links. The fallback path then found no price node
    at all and returned a row with every column null but `url` and `sku`,
    while reporting itself a success.

    Stopping one level too LATE is the opposite failure and the more
    dangerous one: every tile then reports its neighbours' prices, and a link
    that matches the URL shape by coincidence — a comparison widget, a
    "customers also viewed" strip — steals a real product's data. Counting
    distinct skus is what allows the walk to pass the two links of one tile
    and still stop at the edge of the next.

    Capped at _MAX_TILE_WIDEN so a malformed document cannot walk to <body>.
    """
    best, node = anchor, anchor
    for _ in range(_MAX_TILE_WIDEN):
        node = node.parent
        if node is None or not hasattr(node, "select"):
            break
        if _distinct_skus(node) != 1:
            break
        best = node
    return best


def _current_price_node(tile):
    """A copy of the tile's price block holding ONLY the current price.

    The block also contains the struck-through comparison price, the VAT and
    shipping note, and an instalment line ("Bezahle in 18 Raten à 19,39 €").
    Reading the first price out of the block as it stands returns whichever
    of those comes first in the markup — on a tile with a 30-day-low notice
    that is the comparison price, and elsewhere it can be a monthly
    instalment.

    So the subtrees that are not the price are removed from a COPY first.
    Working on a copy matters: the caller still needs the strike nodes in the
    real tile to read `original_price` and `lowest_price_30d`.
    """
    if tile is None:
        return None
    block = tile.select_one(SELECTORS["price_block"])
    if block is None:
        return None
    clone = BeautifulSoup(str(block), "html.parser")
    for junk in (clone.select(SELECTORS["strike_any"])
                 + clone.select(SELECTORS["price_extra"])):
        junk.decompose()
    return clone


def _strike_price(tile, which: str, host_cur: Optional[str]) -> Optional[float]:
    """The `rrp` or `lop` struck-through price on this tile, or None.

    READ THE WARNING AT THE TOP OF THIS MODULE. `which` is not a detail:
    "rrp" is an original price and "lop" is a 30-day low that is usually
    BELOW the current price, and the two are told apart only by this
    attribute.
    """
    if tile is None:
        return None
    node = tile.select_one(SELECTORS["strike_" + which])
    if node is None:
        return None
    # The label ("UVP", "Tiefstpreis (30 Tage):") sits inside the same node
    # and carries no number, so it does not interfere; the node renders the
    # amount twice, once for sighted users ("299,– €") and once for screen
    # readers ("299,00€"), and either parses to the same figure.
    amount, _ = _first_price(node, host_cur)
    return amount


def _tile_in_stock(tile) -> Optional[bool]:
    """Delivery availability from the tile's own delivery hook.

    MediaMarkt encodes the state in the attribute VALUE
    (`mms-cofr-delivery_AVAILABLE`, `..._PARTIALLY_AVAILABLE`) rather than in
    text, so this reads the same on every locale. Only the states observed
    across the captures are mapped; anything else returns None rather than
    being guessed into a boolean.
    """
    if tile is None:
        return None
    node = tile.select_one(SELECTORS["delivery"])
    if node is None:
        return None
    state = (node.get("data-test") or "").split("_", 1)[-1].upper()
    if state in ("AVAILABLE", "PARTIALLY_AVAILABLE"):
        return True
    if state in ("UNAVAILABLE", "SOLD_OUT", "NOT_AVAILABLE"):
        return False
    return None


def _detail_dom_price(soup, host_cur: Optional[str]
                      ) -> Tuple[Optional[float], Optional[str]]:
    """The detail page's OWN price, reassembled from its three nodes.

    A detail page does not render the tile's price block. It splits the price
    across separate elements:

        [data-test="branded-price-whole-value"]    "24,"
        [data-test="branded-price-decimal-value"]  "99"
        [data-test="branded-price-currency"]       "€"

    which matters twice over, and both are failures this function exists to
    prevent rather than hypotheticals:

      * Reading the CONTAINER's text gives "24, 99 €". The general price
        pattern matches "99 €" out of that and returns 99.0 for a product
        costing 24.99 — an order-of-magnitude error on a page where every
        other field is right, and one that a coverage check would call a
        success.
      * Searching the whole document for a price node instead finds the
        "similar products" carousel: one captured detail page carries NINE
        `mms-price` blocks, eight of them belonging to other products. A
        confirmation read from those confirms a neighbour's price.

    So the read is scoped to the page's own price container and assembled
    from the named parts. Returns (None, None) rather than a guess when the
    parts are not all there — the structured price is already in hand, and a
    wrong confirmation is worse than an unconfirmed row.
    """
    area = soup.select_one(SELECTORS["detail_price"])
    if area is None:
        return None, None
    whole = area.select_one(SELECTORS["detail_price_whole"])
    decimal = area.select_one(SELECTORS["detail_price_decimal"])
    if whole is None or decimal is None:
        return None, None
    whole_text = _clean_text(whole.get_text(strip=True)) or ""
    decimal_text = _clean_text(decimal.get_text(strip=True)) or ""
    digits_whole = re.sub(r"[^\d.,]", "", whole_text).rstrip(".,")
    # The cents node holds a DASH on a round price — the split form of the
    # same "349,– €" convention the tile uses, and it is the common case on
    # large appliances, where MediaMarkt prices in whole euros. Reading it as
    # "no digits, give up" left every such product unconfirmed
    # (price_source "jsonld" instead of "jsonld+dom") and, worse, would have
    # left it unpriced on any page where the structured data was missing.
    if decimal_text and not any(ch.isdigit() for ch in decimal_text):
        digits_decimal = "00" if _DASH_DECIMAL_RE.search("0," + decimal_text) else ""
    else:
        digits_decimal = re.sub(r"[^\d]", "", decimal_text)
    if not digits_whole or not digits_decimal:
        return None, None
    amount = _normalize_amount(digits_whole + "." + digits_decimal)
    symbol = area.select_one(SELECTORS["detail_price_currency"])
    currency = None
    if symbol is not None:
        _, currency = _prices_in(
            "1 " + (_clean_text(symbol.get_text(strip=True)) or ""), host_cur)
    return amount, currency or host_cur


def _tile_by_url(soup, host: str) -> Dict[str, object]:
    """Index the page's product cards by the absolute URL each one links to.

    The JSON-LD gives every product on the page; the tiles give the two
    prices JSON-LD does not publish. Joining them on the product URL is what
    makes `price_source == "jsonld+dom"` mean something.
    """
    index: Dict[str, object] = {}
    for card in soup.select(SELECTORS["product_card"]):
        link = card.select_one(SELECTORS["item_link"])
        href = link.get("href") if link else None
        if not href:
            continue
        index.setdefault(_canonical_product_url(host, href), card)
    return index


# ---------------------------------------------------------------------------
# Listing pages
# ---------------------------------------------------------------------------
def parse_products(html: str, base_url: str, category: Optional[str] = None
                   ) -> List[Product]:
    """Rows from one category grid or search results page, in page order.

    JSON-LD first, tiles as an overlay for the two prices it does not carry,
    and the URL-pattern fallback only if the structured path yielded nothing.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    host = site_host(base_url) or SOURCE_DEFAULT
    host_cur = HOSTS.get(host)
    label = category or category_from_url(base_url)
    page_no = page_number_from_url(base_url)

    nodes = _products_in_ld(_ld_blocks(soup))
    if not nodes:
        return _parse_url_fallback(soup, host, label, page_no, host_cur)

    tiles = _tile_by_url(soup, host)
    rows: List[Product] = []
    confirmed = 0
    for node in nodes:
        offer = _ld_offer(node)
        # The product's own URL, which on some schema.org emitters lives on
        # the offer rather than the product. Taking `node.url` alone would
        # leave every row pointing at the listing page while title and price
        # all looked right.
        url = _clean_text(node.get("url")) or _clean_text(offer.get("url"))
        if not url:
            continue
        url = _canonical_product_url(host, url)
        rating, review_count = _ld_rating(node)

        row = Product(
            source=host,
            url=url,
            sku=sku_from_url(url),
            title=_clean_text(node.get("name")),
            price=_to_float(offer.get("price")),
            currency=_clean_text(offer.get("priceCurrency")),
            rating=rating,
            review_count=review_count,
            in_stock=_ld_in_stock(offer),
            image_url=_ld_image(node.get("image")),
            category=label,
            price_source="jsonld" if offer.get("price") is not None else None,
            page=page_no,
            position=len(rows) + 1,
        )

        tile = tiles.get(url)
        if tile is not None:
            _overlay_from_tile(row, tile, host_cur)
            confirmed += 1
        rows.append(row)

    if rows:
        share = confirmed / len(rows)
        # Below this the two views of the page disagree about what is on it,
        # which is worth saying out loud: it is the signal that the tile
        # markup moved, and that the columns coming only from the DOM
        # (original_price, lowest_price_30d) are quietly emptying out while
        # the row count stays healthy.
        if share < 0.9:
            logger.warning(
                "only %d of %d rows on %s were confirmed against a rendered "
                "tile (%.0f%%); original_price and lowest_price_30d come from "
                "the tile only", confirmed, len(rows), base_url, share * 100)
    return rows


def _overlay_from_tile(row: Product, tile, host_cur: Optional[str]) -> None:
    """Fill in what the tile knows and the structured data does not.

    The structured price is NEVER overwritten from the DOM — it is a fact
    published by the site, the DOM read is an interpretation of rendered
    text, and the family rule is that the fact wins. The tile is used to
    CONFIRM it and to supply the two comparison prices JSON-LD omits.
    """
    tile_price, tile_currency = _first_price(_current_price_node(tile), host_cur)

    if row.price is not None and tile_price is not None:
        if abs(tile_price - row.price) < 0.005:
            row.price_source = "jsonld+dom"
        else:
            # The two views disagree about which product this is, or about
            # what it costs. Leaving the row alone is the safe half of the
            # trade: overwriting a correct row is worse than leaving one
            # unconfirmed, and the sku makes it findable.
            logger.warning("sku %s: structured price %s but tile shows %s; "
                           "keeping the structured value", row.sku,
                           row.price, tile_price)
    elif row.price is None and tile_price is not None:
        row.price = tile_price
        row.currency = row.currency or tile_currency
        row.price_source = "dom"

    row.original_price = _strike_price(tile, "rrp", host_cur)
    row.lowest_price_30d = _strike_price(tile, "lop", host_cur)
    row.discount_pct = _discount_from(row.price, row.original_price)

    if row.in_stock is None:
        row.in_stock = _tile_in_stock(tile)
    if row.rating is None:
        row.rating = _rating_from(tile.select_one(SELECTORS["rating"])) \
            or _rating_from(tile)
    if row.review_count is None:
        count_node = tile.select_one(SELECTORS["rating_count"])
        if count_node is not None:
            row.review_count = _int_from(count_node.get_text(" ", strip=True))
    if not row.title:
        title_node = tile.select_one(SELECTORS["title"])
        if title_node is not None:
            row.title = _clean_text(title_node.get_text(" ", strip=True))


def _parse_url_fallback(soup, host: str, label: Optional[str],
                        page_no: Optional[int], host_cur: Optional[str]
                        ) -> List[Product]:
    """Rows built from product links alone, when no structured data was found.

    The weaker path, and the only one in this file that may read a price out
    of a whole tile's text rather than a price node. It runs when MediaMarkt
    serves a grid without its ItemList — which none of the captured pages
    did, so this is a guard against a future change rather than something the
    site does today.
    """
    rows: List[Product] = []
    seen = set()
    for anchor in soup.select(SELECTORS["item_link"]):
        href = anchor.get("href")
        sku = sku_from_url(href)
        # No article number means this is not a product link — a "compare"
        # control, or a teaser that happens to sit under /product/.
        if not sku or sku in seen:
            continue
        seen.add(sku)
        tile = _tile_scope(anchor)
        price, currency = _first_price(_current_price_node(tile), host_cur)
        if price is None:
            price, currency = _first_price(tile, host_cur)
        title_node = tile.select_one(SELECTORS["title"])
        img = tile.select_one("img")
        row = Product(
            source=host,
            url=_canonical_product_url(host, href),
            sku=sku,
            title=_clean_text(title_node.get_text(" ", strip=True)) if title_node
            else _clean_text(anchor.get_text(" ", strip=True)),
            price=price,
            currency=currency,
            original_price=_strike_price(tile, "rrp", host_cur),
            lowest_price_30d=_strike_price(tile, "lop", host_cur),
            rating=_rating_from(tile),
            in_stock=_tile_in_stock(tile),
            image_url=(img.get("src") or img.get("data-src")) if img else None,
            category=label,
            price_source="dom" if price is not None else None,
            page=page_no,
            position=len(rows) + 1,
        )
        row.discount_pct = _discount_from(row.price, row.original_price)
        rows.append(row)
    if rows:
        logger.warning("no JSON-LD on this page; fell back to %d product links. "
                       "Expect lower coverage on every column except url and sku",
                       len(rows))
    return rows


# ---------------------------------------------------------------------------
# Detail pages
# ---------------------------------------------------------------------------
def parse_product_detail(html: str, base_url: str, category: Optional[str] = None
                         ) -> Optional[Product]:
    """One row from a /product/ page, or None if the page carries no product.

    Structured-first like the listing path and for the same reason, but the
    detail page's JSON-LD is much richer: it adds `brand`, an explicit `sku`,
    `gtin13` (the EAN), a description, the full image list and an explicit
    availability. Those are the columns that make --mode product worth its
    extra request per product.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    host = site_host(base_url) or SOURCE_DEFAULT
    host_cur = HOSTS.get(host)

    nodes = _products_in_ld(_ld_blocks(soup))
    node = nodes[0] if nodes else {}
    offer = _ld_offer(node)

    url = _clean_text(node.get("url")) or _clean_text(offer.get("url")) or base_url
    url = _canonical_product_url(host, url)
    # The URL's article number and the structured `sku` agree on every
    # captured page. The structured one is preferred because it is the site
    # stating a fact rather than this parser reading a slug; the URL is the
    # fallback for a page that omits it.
    sku = _clean_text(node.get("sku")) or sku_from_url(url) or sku_from_url(base_url)

    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    elif isinstance(brand, list):
        brand = next((b.get("name") if isinstance(b, dict) else b
                      for b in brand), None)

    rating, review_count = _ld_rating(node)

    images = node.get("image")
    image_list = None
    if isinstance(images, list):
        image_list = [i for i in (_ld_image(x) for x in images) if i] or None

    row = Product(
        source=host,
        url=url,
        sku=sku,
        title=_clean_text(node.get("name")),
        brand=_clean_text(brand),
        price=_to_float(offer.get("price")),
        currency=_clean_text(offer.get("priceCurrency")),
        rating=rating,
        review_count=review_count,
        in_stock=_ld_in_stock(offer),
        image_url=_ld_image(images),
        category=category or _breadcrumb_category(soup),
        price_source="jsonld" if offer.get("price") is not None else None,
        ean=_clean_text(node.get("gtin13")) or _clean_text(node.get("gtin")),
        description=_clean_text(node.get("description")),
        images=image_list,
    )

    # The comparison prices, scoped to the page's own price container. NOT
    # to the whole document: a detail page carries a "similar products"
    # carousel whose tiles have strike prices of their own, and an unscoped
    # read would attach a neighbour's UVP to this product. Same WARNING as
    # everywhere else — `rrp` is an original price, `lop` is a 30-day low.
    price_area = soup.select_one(SELECTORS["detail_price"])
    if price_area is not None:
        row.original_price = _strike_price(price_area, "rrp", host_cur)
        row.lowest_price_30d = _strike_price(price_area, "lop", host_cur)
    row.discount_pct = _discount_from(row.price, row.original_price)

    dom_price, dom_currency = _detail_dom_price(soup, host_cur)
    if row.price is None and dom_price is not None:
        row.price = dom_price
        row.currency = row.currency or dom_currency
        row.price_source = "dom"
    elif (row.price is not None and dom_price is not None
          and abs(dom_price - row.price) < 0.005):
        row.price_source = "jsonld+dom"
    elif row.price is not None and dom_price is not None:
        logger.warning("sku %s: structured price %s but the page shows %s; "
                       "keeping the structured value", row.sku, row.price,
                       dom_price)

    if not row.title and not row.sku:
        # Neither a name nor an id: whatever this page is, it is not a
        # product. Returning the row anyway would put a null-only line into
        # the output and count as a success.
        return None
    return row


def _breadcrumb_category(soup) -> Optional[str]:
    """The deepest category name from the page's BreadcrumbList.

    The last crumb is the product itself, so the one before it is its
    category. Returns None rather than falling back to the product name.
    """
    for block in _ld_blocks(soup):
        types = block.get("@type")
        types = types if isinstance(types, list) else [types]
        if "BreadcrumbList" not in types:
            continue
        items = block.get("itemListElement")
        if not isinstance(items, list) or len(items) < 2:
            continue
        crumb = items[-2]
        if isinstance(crumb, dict):
            name = crumb.get("name")
            if isinstance(name, str) and name.strip():
                return _clean_text(name)
    return None
