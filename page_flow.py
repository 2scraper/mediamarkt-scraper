"""
page_flow.py
------------
MediaMarkt's page-state policy, shared by all three engines.

Why this module exists, when part of this family keeps each engine
self-contained: MediaMarkt answers a request in four different ways and three
of them want a different response.

    content    parse it
    blocked    HTTP 403 behind the shop's own error page — rotate the exit.
               Not solvable: there is no challenge on it to solve.
    captcha    a challenge widget — this is the paid path
    empty      a real page with no products on it (a hub category, an
               exhausted listing) — report it as such, do not retry it and
               do not go looking for a proxy problem

Three copies of that triage would drift, and the drift would be silent — an
engine that reports a hub category as blocked exits 3 where its twin exits 4
on the same URL. The family already shares `output_writer.finish_run()` for
exactly this reason ("all three must agree on exit codes, run status, and
whether a run crashes or spends money"); this is the same argument applied to
the decisions that come before it.

What this module deliberately does NOT contain
----------------------------------------------
No scrolling, no lazy-load hydration, no session re-rolling. Those exist in
the sibling repo for this family because that site needs them, and porting
them here would have been dead code that looks load-bearing. Measured on
2026-09-09 over a real browser on a German residential exit, on
/de/category/kühlen-gefrieren-32.html:

    round 0: cards=12  links=24  height=13648
    round 1: cards=12  links=24  height=13648
    ... eight rounds, scrolling to document.body.scrollHeight each time ...
    round 7: cards=12  links=24  height=13648

Nothing grows. MediaMarkt server-renders exactly one page of twelve products
and offers a "12 weitere Produkte anzeigen" BUTTON rather than an infinite
scroll — and since `?page=N` addresses every page directly (see
product_parser.page_url), that button never needs pressing. A run therefore
never spends time scrolling a page that will not change.

The functions here are either pure or driven through small callables, so each
engine passes its own driver's primitives and keeps its browser plumbing to
itself:

    count(selector) -> int                how many elements match
    content() -> Optional[str]            current HTML, None if unavailable
    current_url() -> str                  the URL the browser is on
    sleep(ms) -> None                     the driver's own wait

Deliberately no `evaluate(js)`: passing JavaScript from here would decide its
dialect for every driver, and they disagree — Playwright and pyppeteer take
`() => expr`, while Selenium's execute_script takes `return expr;`. Naming
the OPERATION instead keeps this module free of any driver's flavour.

Every value here is measured; the numbers are in the comments and in the
README, and the measurements are dated because MediaMarkt's markup will move.
"""

import logging
from typing import Optional

from product_parser import detect_page_state, page_url

logger = logging.getLogger("page_flow")


# What "the page has painted" means, per mode.
#
# The listing form matches the CARD and not `a[href*="/product/"]`, and that
# is not a style preference: the same measurement above counted 24 product
# links against 12 cards, because each tile links twice (once from the image,
# once from the title). Counting links would report the grid as twice the
# size it is, which matters the moment a threshold is compared against it.
READY_SELECTOR_LISTING = '[data-test="mms-product-card"]'
READY_SELECTOR_PRODUCT = '[data-test="mms-product-price"], [data-test^="mms-strike-price-type-"]'

# Ordered most-durable first, per the family rule: a standards-based signal
# before a build artefact. Unlike the sibling repo, `link[rel='next']` is
# genuinely PRESENT here — MediaMarkt puts it in the document head on every
# listing page, verified on category pages 1-3 and on both search pages — and
# it agrees exactly with what `page_url()` constructs. So all three
# pagination layers are real on this site and the standards-based one leads
# for once because it works, not merely because it would be nicer if it did.
NEXT_PAGE_SELECTOR = "link[rel='next'], a[rel='next'], [data-test='mms-pagination-next']"

# How many cards must appear before a LISTING counts as loaded rather than as
# a lucky single match. Must be > 1: waiting for one resolves on an unrelated
# element long before the grid paints.
#
# Five, against a measured floor of twelve on every listing page captured —
# six category pages and two search pages, all exactly 12. Five leaves room
# for a short final page without waiting out the timeout on it.
MIN_CARD_MATCHES = 5

# A listing gets the full 20s: a grid can genuinely be slow to paint from a
# distant exit. A detail page gets 8s, because its content is either in the
# markup that arrived or it is not.
CONTENT_TIMEOUT_MS = {"listing": 20000, "product": 8000}

# Query parameters MediaMarkt hangs on its own links that do not select
# content — search-session and click-attribution ids. They are stripped
# before the site's next-link is compared with what `page_url()` builds,
# because otherwise a strict comparison would declare a disagreement on every
# run and every run would refuse `--concurrency` for no reason.
TRACKING_PARAMS = {"queryMeta", "queryInitial", "searchFeatures", "ga_query",
                   "queryHash", "queryRequestId", "rbe", "gclid", "wt_mc",
                   "utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content"}


def ready_selector(mode: str) -> str:
    return READY_SELECTOR_PRODUCT if mode == "product" else READY_SELECTOR_LISTING


def min_matches(mode: str) -> int:
    """How many readiness anchors mean "loaded", for this mode.

    A listing needs several. A detail page has exactly one price block, so
    requiring more than one would time out on every successful fetch — the
    threshold has to follow the mode or it silently inverts.
    """
    return MIN_CARD_MATCHES if mode == "listing" else 0


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS.get(mode, 20000)


def classify(html: Optional[str], status: Optional[int] = None,
             url: Optional[str] = None) -> str:
    """The page's state, as the engines see it.

    A thin wrapper over `product_parser.detect_page_state` so that every
    engine reaches the policy through one name, and so that a future change
    to how a state is decided lands in one place rather than three.
    """
    return detect_page_state(html or "", status=status, url=url)


# What each state means for the run. Kept as data rather than as three copies
# of an if-chain, so an engine cannot quietly disagree with its twins about
# whether a page is worth retrying or worth paying for.
#
#   retry     fetch it again, possibly from another exit
#   solve     hand it to the captcha solver, if one is configured
#   blocked   count it towards the blocked-page tally that decides exit 3
STATE_POLICY = {
    "content": {"retry": False, "solve": False, "blocked": False},
    # Retried, because the block is a property of the EXIT rather than of the
    # URL: the same request from a residential address returns 200. A retry
    # from the same address is nearly free and a rotation is what actually
    # clears it, so this is the one state where `--proxy-block-retries` earns
    # its keep.
    "blocked": {"retry": True, "solve": False, "blocked": True},
    "captcha": {"retry": True, "solve": True, "blocked": True},
    # NOT retried. An empty page is a correct answer to the question that was
    # asked — a hub category has no products, and page 9 of an 8-page listing
    # has none either. Retrying it would spend the user's budget on getting
    # the same right answer again.
    "empty": {"retry": False, "solve": False, "blocked": False},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("retry", False)


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("solve", False)


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("blocked", False)


def comparable(url: str) -> str:
    """`url` reduced to the parts that decide WHICH PAGE it addresses.

    Two normalisations, and the second one is not cosmetic:

      * tracking parameters are dropped, so a next-link carrying a search
        session id still compares equal to the page it points at;
      * the path is percent-DECODED, because MediaMarkt and the caller
        disagree about encoding on any category whose name is not ASCII. The
        site's own next-link on the fridges category reads
        ".../kühlen-gefrieren-32.html?page=2" while the URL a user pastes
        from the address bar reads ".../k%C3%BChlen-gefrieren-32.html". They
        are the same page. Comparing them as raw strings said they were not,
        which would have made every accented category — a large share of a
        German, Spanish, Turkish or Hungarian catalogue — silently refuse
        `--concurrency` and fall back to sequential fetching, with nothing in
        the log to say why.
    """
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
    parts = urlparse(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.split("[")[0] not in TRACKING_PARAMS]
    return urlunparse(parts._replace(path=unquote(parts.path),
                                     query=urlencode(sorted(kept)),
                                     fragment=""))


def pagination_is_addressable(page1_url: str, site_next_url: Optional[str]
                              ) -> bool:
    """Whether page N can be fetched without first fetching page N-1.

    Concurrency depends entirely on this. Constructing `?page=N` removes the
    strictly sequential chain of following the site's own next-link — but
    only when page 1's next-link AGREES with what the convention would build.
    If MediaMarkt ever starts issuing a cursor or a signed token this
    function cannot reproduce, the honest answer is to chain link-to-link and
    say the listing cannot be fetched independently, rather than to fetch a
    set of URLs that quietly return the wrong pages.

    A missing next-link is not a disagreement: a single-page listing has none
    and there is nothing to contradict. Returns True there, and the caller's
    own "this page added no new skus" condition ends the run.

    Search pages are the reason that default has to be True rather than a
    cautious False. MediaMarkt publishes NO next-link on /de/search.html at
    all, and `?page=2` on the same URL nonetheless returns a completely
    different set of twelve products (0 skus shared with page 1, measured
    2026-09-09). Refusing to address search pages independently because the
    site does not advertise that it can would cost the concurrency on the
    page kind most likely to be scraped in bulk.
    """
    if not site_next_url:
        return True
    return comparable(site_next_url) == comparable(page_url(page1_url, 2))
