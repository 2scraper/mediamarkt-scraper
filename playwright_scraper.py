#!/usr/bin/env python3
"""
mediamarkt-scraper — Playwright edition (primary engine)
========================================================

Scrapes MediaMarkt category grids, search results and product detail pages,
from any of the group's ten country sites. Which site is decided by the
URL you pass — there is no --country flag, so a flag and a URL cannot
disagree about which shop a run is reading.

    --mode listing   (default)  category grids (/de/category/...) and search
                                results (/de/search.html?query=...), paginated
    --mode product              one /de/product/... page, with brand, EAN,
                                description and the full image list

Three engines ship in this repo and they must agree on exit codes, run
status, and whether a run crashes or spends money; the shared decisions live
in output_writer.finish_run() and page_flow.py so they cannot drift apart.

What is different about MediaMarkt
----------------------------------
* **A residential exit is the requirement, not the browser.** Measured
  2026-09-09: every datacentre and VPN address tested got HTTP 403 on every
  URL of every country site, and the same request from a German residential
  address returned the full page — to a plain HTTP client, with no
  JavaScript run at all. This is the opposite balance from most sites in this
  family, and the README says so rather than selling a browser nobody needs.
* **The block page carries no challenge.** MediaMarkt answers a refused
  request with its own branded error page under a 403. There is nothing on
  it to solve, so a captcha key buys nothing against it and this engine
  rotates the exit instead of paying. See product_parser.detect_page_state.
* **Nothing lazy-loads.** Eight scroll rounds on a live category page left
  the card count and the document height unchanged (12 and 13648px), so this
  engine does not scroll at all. Every page is server-rendered and `?page=N`
  addresses the next twelve products directly.
* **Two struck-through prices, and one of them is a trap.** Read the WARNING
  at the top of product_parser.py before touching anything price-related.

Usage
-----
    python playwright_scraper.py \\
        --url "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html" \\
        --pages 3 \\
        --format both

    python playwright_scraper.py --mode product \\
        --url "https://www.mediamarkt.de/de/product/_harry-potter-the-complete-collection-dvd-2920911.html"

Requires: pip install -r requirements.txt -r requirements-playwright.txt
          then: playwright install chromium   (only if NOT using --cdp-endpoint)
"""

import argparse
import logging
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urljoin, parse_qsl

from playwright.sync_api import (sync_playwright, Error as PWError,
                                 TimeoutError as PWTimeout)

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            INJECT_TOKEN_JS, RECAPTCHA_DISCOVERY_JS)
from product_parser import (parse_products, parse_product_detail,
                            SELECTORS, detect_bot_challenge, page_url,
                            listing_kind, site_host, is_supported_host,
                            total_results, HOSTS, unsupported_reason)
from output_writer import dedupe_by_key, finish_run
import page_flow
from page_flow import NEXT_PAGE_SELECTOR, MIN_CARD_MATCHES
from proxy_pool import (from_args as proxy_pool_from_args, to_playwright, mask,
                        ROTATE_MODES, ProxyError, ProxyPool)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("playwright_scraper")


def _chrome_ua(chromium_version: str) -> str:
    """Build a desktop-Chrome UA naming the browser's OWN real version.

    Not a hardcoded version number: that drifts the moment a newer Chromium
    ships, and a UA claiming an older Chrome than what the JS engine, WebGL
    strings and TLS ClientHello all actually report is itself a mismatch a
    fingerprinter can key on.
    """
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chromium_version} Safari/537.36")


@dataclass
class PageOutcome:
    """What one page produced.

    Collected per page and merged afterwards rather than folded into shared
    state as the loop goes. Two reasons, and the second is the point:
    dedupe that mutates a running set inside the loop makes the OUTPUT depend
    on the order pages happen to arrive in — fine while that order is fixed,
    wrong the moment pages are fetched concurrently, because which page
    "claims" a duplicate sku (and so which `scraped_at` the row carries)
    would vary between runs of the same command. Merging afterwards in page
    order is deterministic regardless of arrival order.
    """
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    # The page_flow state this page came back as ("content", "blocked",
    # "captcha", "empty"). Carried so the caller can tell an EMPTY page —
    # a hub category, or one page past the end of a listing — from a page
    # that failed. Both produce zero rows and they mean opposite things.
    state: Optional[str] = None
    # What the listing itself said its catalogue size was, from page 1 only.
    # Recorded in the run metadata so a consumer can see what fraction of a
    # category a run actually took.
    total_available: Optional[int] = None

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


ITEM_LINK_SELECTOR = SELECTORS["item_link"]


# ---------------------------------------------------------------------------
# page_flow, bound to Playwright
# ---------------------------------------------------------------------------
# Every decision about WHAT to do with a page — how long to wait, when to
# scroll, when a fresh session is the only fix — lives in page_flow.py so all
# three engines make it identically. What lives here is only HOW to ask this
# particular driver. See page_flow's docstring for why that split exists.
def _driver(page):
    # No scroll primitives here, deliberately. MediaMarkt loads nothing on
    # scroll (page_flow's docstring has the eight-round measurement), so a
    # scroll_to_bottom() this engine never calls would be one more thing the
    # three engines could drift on for no benefit.
    return {
        "count": lambda selector: len(page.query_selector_all(selector)),
        "sleep": page.wait_for_timeout,
        "content": lambda: _content_when_settled(page),
        "current_url": lambda: page.url,
    }


def _ready_selector(args) -> str:
    return page_flow.ready_selector(args.mode)


def _min_matches(args) -> int:
    return page_flow.min_matches(args.mode)


def _classify(page, html: str, status=None) -> str:
    return page_flow.classify(html, status=status, url=page.url)

# Every readiness constant, every pagination selector and every state policy
# lives in page_flow.py, with its measurement beside it. Nothing about WHAT
# to do with a page is duplicated here — this file only knows HOW to ask
# Playwright.


def _plan_page_urls(page, args, page_one_url: str) -> Optional[List[str]]:
    """URLs for pages 2..N, decided once from page 1, or None to chain.

    Following the site's own next-link one page at a time is correct but
    strictly sequential: the address of page 5 is not knowable until page 4
    has been fetched. Constructing the page parameter up front removes that
    chain — which is what makes fetching pages independently (and later,
    concurrently) possible at all.

    It is only safe when the site's own link AGREES with the convention, so
    that is checked rather than assumed: if page 1's next-link is not what
    `page_url()` would build for page 2, pagination is carrying something the
    convention cannot reproduce (a cursor, a token, a filter id) and the
    caller must keep chaining link to link. Returns None in that case.

    On MediaMarkt the check passes and is not a formality: page 1's own
    `<link rel="next">` reads `...?page=2`, exactly what `page_url()` builds,
    verified on category pages 1-3 and on both captured search pages. That is
    what makes `--concurrency` safe here.
    """
    if args.pages < 2:
        return None

    constructed = page_url(page_one_url, 2)
    next_link = page.query_selector(NEXT_PAGE_SELECTOR)
    href = next_link.get_attribute("href") if next_link else None

    if href:
        advertised = _resolve_pagination_url(page_one_url, href)
        if _same_url(advertised, constructed):
            logger.info("Pagination follows the ?%s=N convention (page 2 link "
                        "matches the constructed URL) — planning pages 2-%d up "
                        "front.", "page", args.pages)
        else:
            logger.info("The site's own next-page link (%s) is not what the "
                        "page convention would build (%s) — following its links "
                        "one page at a time instead. Pages cannot be fetched "
                        "independently for this listing.", advertised, constructed)
            return None
    else:
        logger.warning(
            "No pagination link matched %s on page 1 — falling back to the "
            "URL convention. That is EXPECTED on a search URL, which "
            "paginates perfectly well through ?page=N without advertising a "
            "next-link (measured: page 2 shared 0 skus with page 1). On a "
            "category page it is not expected, and if the run comes back with "
            "one page of data this is the first thing to check.",
            NEXT_PAGE_SELECTOR)

    return [page_url(page_one_url, n) for n in range(2, args.pages + 1)]


def _next_url_from_page(page, args, page_num: int) -> str:
    """Next page's URL from the site's own link, falling back to ?page=N.

    Only used when pagination could not be planned up front. A missing link
    must not end the run: pagination resting entirely on DOM selectors is a
    silent-success failure waiting to happen, so the convention backs it up
    and the DATA decides when to stop.
    """
    next_link = page.query_selector(NEXT_PAGE_SELECTOR)
    href = next_link.get_attribute("href") if next_link else None
    if href:
        return _resolve_pagination_url(page.url, href)
    return page_url(page.url, page_num + 1)


def _same_url(a: str, b: str) -> bool:
    """Whether two URLs address the same page.

    Delegates to page_flow rather than reimplementing the comparison. This
    function used to carry its own copy, and the copy went stale the moment
    page_flow learned that MediaMarkt writes its own next-links
    percent-DECODED (".../kühlen-gefrieren-32.html") while a pasted URL is
    encoded (".../k%C3%BChlen-gefrieren-32.html"). The live run that found it
    fell back to sequential fetching on every accented category and said only
    that the links "disagreed" — which is exactly the silent divergence
    page_flow.py exists to prevent, reproduced inside a single engine.
    """
    return page_flow.comparable(a) == page_flow.comparable(b)


# Chromium's own names for "the proxy is the problem, not the site". Matched
# on the error text because Playwright surfaces them as a generic Error.
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",     # nothing listening / refused
    "ERR_TUNNEL_CONNECTION_FAILED",    # CONNECT rejected by the proxy
    "ERR_PROXY_AUTH_UNSUPPORTED",      # auth scheme we cannot satisfy
    "ERR_PROXY_AUTH_REQUESTED",        # credentials missing or wrong
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_PROXY_CERTIFICATE_INVALID",
)


def _proxy_failure(exc) -> str:
    """The Chromium proxy-error name in `exc`, or "" if it is not one.

    Distinguishing this from an ordinary timeout matters because the two want
    opposite responses: a timeout deserves a retry from the same exit, while
    an unusable exit deserves a different exit — retrying it unchanged just
    spends the retry budget on a proxy that is not going to answer.
    """
    text = str(exc)
    for marker in _PROXY_ERROR_MARKERS:
        if marker in text:
            return marker
    return ""


def _launch_local(pw, args, pool):
    """Launch our own Chromium on `pool`'s current exit; return (browser, context, page).

    Factored out of scrape() so a proxy rotation can tear the whole browser
    down and call this again. Swapping the proxy under a live session would
    be cheaper and wrong: cookies a bot manager issued against one exit,
    replayed from another, are a stronger signal than either address alone.
    A rotation therefore means a genuinely fresh browser — new cookie jar,
    new storage — which is what an ordinary user on a different network
    looks like.
    """
    launch_kwargs = {"headless": args.headless}
    proxy = to_playwright(pool.current) if pool else None
    if proxy:
        launch_kwargs["proxy"] = proxy
        logger.info("Using proxy exit %s", mask(pool.current))

    browser = pw.chromium.launch(**launch_kwargs)
    # Only override the UA when we launched our own bundled Chromium.
    # Forcing a UA on a page reached via --cdp-endpoint mismatches the remote
    # browser's real TLS/JS fingerprint on purpose-matched values.
    ctx_kwargs = {"user_agent": _chrome_ua(browser.version), "locale": args.locale}
    init_script = None
    if args.fingerprint:
        # Only meaningful on this branch. Over --cdp-endpoint the Scraping
        # Browser already has its own fingerprint, and layering a second one
        # on top produces a mismatch rather than better cover.
        from fingerprint_client import (get_fingerprint,
                                        playwright_context_kwargs,
                                        playwright_init_script)
        fp = get_fingerprint(args.twocaptcha_key,
                             tags=args.fp_tags, country=args.fp_country)
        ctx_kwargs.update(playwright_context_kwargs(fp))
        init_script = playwright_init_script(fp)
        logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"), fp.get("country"))

    context = browser.new_context(**ctx_kwargs)
    if init_script:
        # Must be installed on the context, before any page script runs.
        context.add_init_script(init_script)
    return browser, context, context.new_page()


class _BrowserSession:
    """One browser + context + page, relaunchable onto a different exit.

    Exists because a rotation replaces all three handles at once, and passing
    three mutable locals through every helper is how one of them ends up
    stale. It also gives a worker thread a single object to own: with
    Playwright's sync API, a browser and everything reachable from it belong
    to the thread that created them, so each worker builds its own.
    """

    def __init__(self, pw, args, pool, remote: bool = False):
        self.pw, self.args, self.pool, self.remote = pw, args, pool, remote
        self.browser = self.context = self.page = None

    def open(self):
        if self.remote:
            self.browser, self.context, self.page = _connect_remote(self.pw, self.args)
        else:
            self.browser, self.context, self.page = _launch_local(
                self.pw, self.args, self.pool)
        return self

    def relaunch(self):
        """Tear the browser down and come back on the pool's current exit.

        On a remote browser this is a no-op — its exit is not ours to change.
        """
        if self.remote:
            return
        try:
            self.browser.close()
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error while closing browser for rotation: %s", e)
        self.open()

    def close(self):
        try:
            if self.remote:
                self.page.close()  # leave the remote browser app running
            else:
                self.browser.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during browser teardown: %s", e)


def _connect_remote(pw, args):
    """Attach to an already-running browser over CDP; return (browser, context, page)."""
    logger.info("Connecting to existing browser over CDP: %s",
                _mask_credentials(args.cdp_endpoint))
    # Explicit timeout. Playwright defaults to 30s here, but stating it makes
    # the contract visible next to the pyppeteer twin, which has no connect
    # timeout at all. A Scraping Browser session that is still held answers
    # with HTTP 500 rather than stalling, so this mostly guards against the
    # endpoint going quiet.
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_endpoint, timeout=30000)
    except (PWError, PWTimeout) as e:
        # Playwright puts the endpoint it tried into the exception text, and
        # the endpoint is a URL with the password in it. Unmasked, that
        # password lands in the terminal, in CI output and in any log the run
        # is piped to — which is the one thing this project promises does not
        # happen ("credentials never reach argv or logs"). The message is
        # rewritten with the credentials masked and the host and port kept,
        # because WHICH endpoint failed is the useful half and is not the
        # secret.
        raise PWError(
            f"could not connect to --cdp-endpoint "
            f"{_mask_credentials(args.cdp_endpoint)}: "
            f"{_mask_credentials(str(e))}\n"
            f"A Scraping Browser profile allows ONE live connection at a "
            f"time, so a 500 here usually means another run still holds this "
            f"`pid`. Wait for it to finish, or use a different pid."
        ) from None
    # Reuse the remote browser's existing context so its
    # fingerprint/session/proxy settings stay intact.
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    # The Scraping Browser API exposes a documented CDP domain
    # (`Captcha.setAutoSolve` / `Captcha.solve`) that clears supported
    # challenges inside the browser: https://2captcha.com/scraper/browser-api/api
    # Tried first when --cdp-endpoint is set; this script's own detect+solve
    # logic still runs as a fallback if the endpoint does not support it.
    # Note it does NOT cover MediaMarkt's 403 refusal, which is not a
    # challenge and has nothing on it to solve — a different exit is the only
    # answer there.
    try:
        cdp_session = context.new_cdp_session(page)
        cdp_session.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
        cdp_session.on("Captcha.detected", lambda *_: logger.info("[Scraping Browser] CAPTCHA detected on page."))
        cdp_session.on("Captcha.waitForSolve", lambda *_: logger.info("[Scraping Browser] CAPTCHA sent to 2captcha for solving."))
        cdp_session.on("Captcha.solveFinished", lambda *_: logger.info("[Scraping Browser] CAPTCHA solved automatically."))
        cdp_session.on("Captcha.solveFailed", lambda *_: logger.warning("[Scraping Browser] CAPTCHA auto-solve failed."))
        logger.info("Scraping Browser API Captcha.setAutoSolve enabled — supported "
                    "challenge types will be solved automatically if this "
                    "--cdp-endpoint is a Scraping Browser API session.")
    except Exception as e:
        logger.info("Captcha.setAutoSolve not available on this --cdp-endpoint (%s) — "
                    "relying on this script's own detect+solve logic instead.", e)
    return browser, context, page


def _resolve_pagination_url(base_url: str, href: str) -> str:
    """Resolve a pagination link's raw href against the page it came from.

    Playwright's get_attribute("href") returns the raw HTML attribute,
    unresolved — unlike the DOM .href property Puppeteer/Selenium read for
    the same purpose in this project, which the browser resolves for you.
    urljoin handles every shape correctly — absolute, protocol-relative,
    absolute-path, and page-relative hrefs alike.
    """
    return urljoin(base_url, href)


# Every `scheme://user:pass@` in a string, however many times it occurs.
# Matching globally rather than once is the point: a Playwright connection
# error repeats the endpoint five times (the message plus a four-line call
# log), so a masker that handled only the first occurrence would print the
# password four times and look like it was working.
_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """`text` with any username:password in an embedded URL replaced.

    Takes arbitrary text, not just a URL, because the strings that most need
    this are exception messages with a URL inside them. The host and port are
    KEPT — which endpoint or exit a run used is the useful half of the line
    and is not the secret.
    """
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


def _content_when_settled(page, attempts: int = 4, pause_ms: int = 700):
    """page.content() that tolerates a page mid-navigation.

    Playwright raises `Page.content: Unable to retrieve content because the
    page is navigating and changing the content` if the document swaps under
    it. MediaMarkt geo-redirects a first, cookie-less visit (a `.de` URL
    reached from a Spanish exit lands on `.es`), and a consent layer can
    navigate too, so a snapshot taken right after goto() can land exactly on
    the swap.

    Retries briefly and returns None if the page won't hold still, so the
    caller can skip a check instead of failing the run.
    """
    for attempt in range(1, attempts + 1):
        try:
            return page.content()
        except PWError as e:
            if "navigating" not in str(e).lower():
                raise
            if attempt == attempts:
                logger.warning("Page kept navigating through %d attempts — "
                               "continuing without a snapshot.", attempts)
                return None
            logger.info("Page is navigating (a geo-redirect or the consent "
                        "layer?) — retrying content() in %dms (%d/%d).",
                        pause_ms, attempt, attempts)
            page.wait_for_timeout(pause_ms)
    return None


def handle_captcha_if_present(page, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Runs after EVERY navigation, for ANY page — not scoped to one URL. The
    static-HTML and runtime reCAPTCHA detectors are run and reconciled
    against each other rather than short-circuited, because they can disagree
    about the variant and the parameters for one are rejected for the other.

    NOTE what this cannot help with. MediaMarkt's actual refusal is a 403
    carrying its own error page, with no challenge on it at all — so a
    2Captcha key does nothing about the state a blocked run is most likely to
    meet, and `detect_page_state` reports that as "blocked" rather than
    "captcha" precisely so no solve is attempted or billed. This path exists
    for the account, newsletter and checkout flows where the site does use
    reCAPTCHA, and because the family's rule is that detection stays broad:
    different geos and scenarios surface different challenges.
    """
    html = _content_when_settled(page)
    if html is None:
        # Couldn't get a stable snapshot — skip detection for this navigation
        # rather than taking the whole run down. The next navigation gets
        # another chance, and the parse below reads its own copy of the DOM.
        return False

    # Detected is not the same as blocking. A challenge on a page whose
    # products are already rendered guards nothing, and counting the anchors
    # is instant — no wait_for_function, no 20s — which is why this check
    # sits here rather than after the readiness wait. Doing it the other way
    # round would cost 20 wasted seconds on a page the captcha genuinely
    # gates, where solving FIRST is what makes the content appear.
    already_rendered = len(page.query_selector_all(_ready_selector(args)))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, page.url)
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: page.evaluate(js), page_url=page.url)
    challenge = reconcile_detections(html_challenge, runtime_challenge)
    if not challenge:
        return False

    if when_blocked and already_rendered > MIN_CARD_MATCHES:
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it. Pass --solve-captcha always to "
                    "solve it anyway.", challenge.kind, challenge.source,
                    already_rendered)
        return False

    logger.warning("%s detected via %s (sitekey=%s, action=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey, challenge.action)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be "
                       "solved — continuing with whatever the page already "
                       "holds.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                               api_version=args.captcha_api,
                               min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing with "
                     "whatever the page holds.", e)
        return False

    page.evaluate(INJECT_TOKEN_JS, token)
    logger.info("Token injected. Reloading page to continue.")
    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded", timeout=60000)
    return True


def _parse_for_mode(html: str, url: str, args) -> List:
    """Rows for this mode, always as a list even when the mode yields one.

    `parse_product_detail` returns a single row or None; wrapping it here
    keeps every caller downstream — dedupe, merge, coverage logging, the
    writers — working on one shape instead of branching on the mode again.
    """
    if args.mode == "product":
        row = parse_product_detail(html, url, category=args.category)
        return [row] if row is not None else []
    return parse_products(html, url, category=args.category)


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    """Fetch and parse one page. Retries, rotations and debug dumps live here.

    Returns a PageOutcome and never raises for an EXPECTED failure — a
    timeout, a 403 refusal, a captcha page, a dead exit are all recorded on the
    outcome instead. What the run should do about them differs between the
    sequential and concurrent paths, so that decision belongs to the caller
    rather than to a raised exception unwinding through it.

    Always goes through `session.page`, never a captured local: a rotation
    replaces the browser, context and page together, and a stale handle is
    exactly the bug _BrowserSession exists to prevent.
    """
    outcome = PageOutcome(page_num=page_num, url=url)

    # How many times a blocked page may be retried from a DIFFERENT exit.
    # Zero without a pool: there is nowhere else to go, and a bare retry from
    # the same address just burns it further.
    block_retries = args.proxy_block_retries if (pool and len(pool) > 1) else 0
    html, state, load_failed = None, "ok", False

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        # Retry a navigation timeout rather than ending the run on it. One
        # network flap on page 12 of 50 should not break the loop.
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                session.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                load_failed = False
                break
            except (PWTimeout, PWError) as e:
                # A dead or misconfigured proxy raises PWError
                # (net::ERR_PROXY_CONNECTION_FAILED), not PWTimeout —
                # catching only the latter lets it escape as a traceback,
                # which is the likeliest failure the first time anyone points
                # --proxy-file at a real list.
                reason = _proxy_failure(e)
                if reason:
                    exit_failed = reason
                    load_failed = True
                    break  # a different exit is the only thing that helps
                load_failed = True
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Timeout loading %s (attempt %d/%d) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, pause)
                    time.sleep(pause)

        if exit_failed and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating to another "
                           "one (%d/%d).", mask(pool.current), exit_failed,
                           block_attempt + 1, block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            continue
        if load_failed:
            break

        if handle_captcha_if_present(session.page, args):
            # A solve navigated the page. Give the destination a moment
            # before judging what came back.
            session.page.wait_for_timeout(1000)

        html = _content_when_settled(session.page) or ""
        state = _classify(session.page, html)

        if not page_flow.should_retry(state):
            # "content" and "empty" are both final answers. An empty page is
            # a CORRECT one — a hub category has no grid, and one page past
            # the end of a listing has no products — so retrying it would
            # spend the user's budget re-confirming the same right answer,
            # and rotating the exit would blame an address for the URL it was
            # given.
            break

        # Blocked or challenged. A different exit is the one thing that
        # plausibly changes the outcome: the ADDRESS is what was scored, not
        # the URL, so retrying it unchanged would only confirm it. Measured
        # 2026-09-09 — the same URL that answers 403 from a datacentre exit
        # answers 200 from a residential one.
        if block_attempt < block_retries:
            logger.warning("Page %d came back as %s from %s — retrying from "
                           "another exit (%d/%d).", page_num, state,
                           mask(pool.current), block_attempt + 1, block_retries)
            pool.advance(f"{state} on page {page_num}")
            session.relaunch()

    if load_failed:
        logger.error("Gave up loading %s after %d attempt(s).", url, args.retries)
        outcome.load_failed = True
        return outcome

    outcome.state = state

    if state == "blocked":
        # Reported here rather than left to the generic challenge check
        # below, because MediaMarkt's block page carries NO vendor marker for
        # that check to find — it is the shop's own error page under a 403.
        # Saying "blocked" without naming a vendor is the honest answer, and
        # saying what actually clears it is more use than a captcha hint.
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        logger.error("MediaMarkt refused this request (its own error page "
                     "under HTTP 403) — saved to %s. There is no challenge on "
                     "that page to solve, so a 2Captcha key does not help; a "
                     "residential exit does. This is exit 3, distinct from a "
                     "genuinely empty result (exit 4).%s", debug_html,
                     f" Tried {block_retries + 1} exit(s)." if block_retries else "")
        outcome.blocked_by = "mediamarkt-403"
        outcome.final_url = session.page.url
        return outcome

    if state == "content":
        # Don't wait for network idle (retail sites never go fully quiet) and
        # don't accept a single selector match as "ready".
        #
        # No scrolling and no session re-rolling here, and both omissions are
        # measured rather than assumed. MediaMarkt server-renders the whole
        # page: eight scroll rounds on a live category page left the card
        # count at 12 and the document height at 13648px (page_flow's
        # docstring has the run), and the same URL fetched twice in one
        # session returns the same markup — there is no per-session variant
        # to re-roll. So the only thing worth waiting for is paint.
        selector, threshold = _ready_selector(args), _min_matches(args)
        content_timeout = page_flow.content_timeout_ms(args.mode)
        try:
            session.page.wait_for_function(
                f"document.querySelectorAll({selector!r}).length > {threshold}",
                timeout=content_timeout)
            session.page.wait_for_timeout(500)
        except PWTimeout:
            # Not an error on its own. A hub category renders no cards and
            # never will, and one page past the end of a listing is the same:
            # both are correct answers that this wait cannot distinguish from
            # a slow paint, so it times out and the parse decides.
            logger.info("No product cards appeared within %.0fs. If this URL "
                        "is a hub category rather than a product grid, that "
                        "is the expected answer and the run will report 0 "
                        "rows (exit 4).", content_timeout / 1000)

        html = _content_when_settled(session.page) or html

    # Dumping on success, not only on failure: a run can return the right
    # NUMBER of rows with a field silently unpopulated, and then the only way
    # to tell a parsing bug from a too-early snapshot is to inspect the exact
    # bytes the parser was given.
    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    # Only when the page is NOT already content. A challenge marker on a page
    # whose products have rendered guards nothing — it is the same
    # "detected is not blocking" rule the captcha default follows, applied to
    # the blocking decision instead of the spending one.
    #
    # The first live run of this scraper failed here: it reported exit 3 on a
    # 1.8 MB category page holding twelve products, because a marker was
    # present. `state` has already been decided by page_flow, which weighs
    # content above markers, so this check now only refines the REASON for a
    # page that was not content anyway.
    vendor = (detect_bot_challenge(html, url=session.page.url)
              if state != "content" else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.page.screenshot(path=f"{args.out}_page{page_num}_debug.png",
                                    full_page=True)
        except Exception as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s%s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(html), debug_html,
                     f" (tried {block_retries + 1} exit(s))" if block_retries else "")
        outcome.blocked_by = vendor
        return outcome

    products = _parse_for_mode(html, session.page.url, args)
    logger.info("Parsed %d row(s) from page %d.", len(products), page_num)

    # MediaMarkt prints its own catalogue size on every listing page ("12
    # von 42930"), which turns "did we get everything?" into arithmetic
    # instead of a guess. Recorded from page 1 only — it is a property of the
    # listing, not of the page — and reported in the sidecar so a consumer
    # can see what fraction of a category a run took.
    if args.mode == "listing" and page_num == 1:
        outcome.total_available = total_results(html, shown=len(products))
        if outcome.total_available and products:
            pages_needed = -(-outcome.total_available // len(products))
            if args.pages > pages_needed:
                logger.info("This listing holds %d product(s), about %d page(s) "
                            "of %d. The run asked for %d, so it will stop when "
                            "the listing runs out.", outcome.total_available,
                            pages_needed, len(products), args.pages)

    if products and args.mode == "listing":
        priced = sum(1 for p in products if p.price is not None)
        # Reported every time, not only when it looks wrong, so a consumer
        # gets the number rather than a threshold someone guessed. On
        # MediaMarkt the healthy figure is 100%: 84 of 84 rows across seven
        # captured listing pages carried a price, because the price is in the
        # structured data rather than withheld per offer. Anything materially
        # below that is a signal, which is why the bar here is high.
        logger.info("Price coverage on page %d: %d/%d (%.0f%%).", page_num,
                    priced, len(products), 100.0 * priced / len(products))
        if priced < len(products):
            logger.warning("%d row(s) on page %d carry no price. Every row of "
                           "every captured page had one, so this is worth a "
                           "look — re-run with --dump-html to check the "
                           "snapshot.", len(products) - priced, page_num)

        # The share of rows whose structured price was CONFIRMED against a
        # rendered tile. This is the number that goes quietly wrong when the
        # tile markup moves: the row count and the prices stay healthy while
        # original_price and lowest_price_30d empty out, so a run can look
        # fine and have lost two columns.
        confirmed = sum(1 for p in products if p.price_source == "jsonld+dom")
        share = 100.0 * confirmed / len(products)
        logger.info("DOM price confirmation on page %d: %d/%d (%.0f%%).",
                    page_num, confirmed, len(products), share)
        if share < 90:
            logger.warning("Only %.0f%% of page %d was confirmed against a "
                           "rendered tile (100%% on every captured page). "
                           "original_price and lowest_price_30d come from the "
                           "tile only, so they are the columns to check.",
                           share, page_num)

    if not products:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        debug_png = f"{args.out}_page{page_num}_debug.png"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.page.screenshot(path=debug_png, full_page=True)
        except Exception as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.warning("0 rows parsed — saved what the browser actually saw to "
                       "%s and %s. Open the .png to see it.", debug_html, debug_png)

    outcome.products = products
    outcome.final_url = session.page.url
    return outcome


def _worker_pool(pool, worker_index: int):
    """A private ProxyPool for one worker, starting at a different exit.

    Each worker gets its OWN pool object holding the same exits rotated to a
    different offset. Two things fall out of that, both wanted:

      * Workers start on distinct exits, which is the point of running
        several — N workers all leaving from one address is just a faster way
        to burn that address.
      * No shared mutable state between threads, so rotation needs no lock.
        A worker that gets blocked can still walk the rest of the pool on its
        own.

    Its exit stays put for the worker's lifetime otherwise: a SESSION must
    not change address mid-flight, and a worker is one session.
    """
    if not pool:
        return None
    proxies = pool.proxies
    offset = worker_index % len(proxies)
    return ProxyPool(proxies[offset:] + proxies[:offset], rotate="per-run")


def _fetch_pages_concurrently(args, pool, specs, concurrency: int):
    """Fetch `specs` [(page_num, url), ...] across `concurrency` workers.

    Each worker owns its own Playwright instance, browser and exit: with the
    sync API a browser belongs to the thread that made it, so sharing one
    across threads is not an option even if it were desirable.
    """
    work = queue.Queue()
    for spec in specs:
        work.put(spec)

    results = []
    results_lock = threading.Lock()
    # Set when a page comes back with no rows at all — the end of the
    # listing. Without it, asking for 50 pages of a 5-page result would fetch
    # 45 empty ones. Workers check it before taking more work, so at most
    # (concurrency - 1) extra pages are in flight when it trips.
    exhausted = threading.Event()

    def worker(index: int):
        name = f"worker-{index + 1}"
        try:
            with sync_playwright() as pw:
                session = _BrowserSession(pw, args, _worker_pool(pool, index)).open()
                try:
                    first = True
                    while not exhausted.is_set():
                        try:
                            page_num, url = work.get_nowait()
                        except queue.Empty:
                            break
                        if not first:
                            time.sleep(args.delay)
                        first = False
                        outcome = _fetch_one_page(session, args, session.pool,
                                                  page_num, url)
                        with results_lock:
                            results.append(outcome)
                        if outcome.ok and not outcome.products:
                            logger.info("[%s] page %d returned no rows — "
                                        "treating that as the end of the listing "
                                        "and stopping dispatch.", name, page_num)
                            exhausted.set()
                finally:
                    session.close()
        except Exception:  # noqa: BLE001 — a dead worker must not hang the run
            logger.exception("[%s] died; its pages will be reported as failed.", name)

    threads = [threading.Thread(target=worker, args=(i,), name=f"page-worker-{i + 1}")
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Anything still queued was never attempted (a worker died, or dispatch
    # stopped at the end of the listing). Not reported as failed pages: they
    # were not tried, and claiming otherwise would overstate the damage.
    unattempted = []
    while True:
        try:
            unattempted.append(work.get_nowait()[0])
        except queue.Empty:
            break
    return results, sorted(unattempted), exhausted.is_set()


def scrape(args) -> int:
    # One entry per page attempted, merged after the loop rather than folded
    # into shared state during it — see PageOutcome for why that ordering
    # matters more than it looks.
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # Both modes are one row per product, so `sku` is the key for both.
    dedupe_key = "sku"
    # Why the loop ended. "completed" means every requested page was fetched;
    # "no_new_products" means the listing itself ran out (also a complete
    # result). "single_page_mode" is complete by construction — a detail page
    # has no page 2. Anything else is an early stop, and the run is only a
    # partial view.
    stop_reason = "single_page_mode" if args.mode != "listing" else "completed"

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit, and layering a second "
                       "proxy on top would contradict it.")
        pool = None

    concurrency = max(1, args.concurrency)
    if concurrency > 1:
        if args.mode != "listing":
            logger.info("--concurrency is ignored in --mode %s: there is one "
                        "page to fetch.", args.mode)
            concurrency = 1
        elif args.cdp_endpoint:
            logger.warning("--concurrency is ignored with --cdp-endpoint: the "
                           "Scraping Browser API allows one live connection per "
                           "profile, and several workers would collide on it "
                           "(profile_locked). Use several pids instead, one run "
                           "each.")
            concurrency = 1
        elif not pool:
            logger.warning("--concurrency %d with no proxy pool: every worker "
                           "leaves from the SAME address, which is a faster way "
                           "to get that address scored than to gather data. "
                           "MediaMarkt already refuses every datacentre "
                           "address outright, so an address that works is one "
                           "worth not burning. Pass --proxy-file to spread "
                           "the load.", concurrency)
        if pool and pool.rotates_per_page():
            logger.info("--proxy-rotate per-page is redundant under "
                        "--concurrency: each worker already holds its own exit "
                        "for its lifetime, which is the same spread without a "
                        "browser relaunch per page.")
        if concurrency > 8:
            logger.warning("--concurrency %d means %d browsers at once "
                           "(~150-300MB each). Make sure the machine has the "
                           "memory for it.", concurrency, concurrency)

    with sync_playwright() as pw:
        session = _BrowserSession(pw, args, pool,
                                  remote=bool(args.cdp_endpoint)).open()
        try:
            # Page 1 is always fetched on its own: its content is what decides
            # whether pages 2..N can be addressed independently at all.
            first = _fetch_one_page(session, args, pool, 1, args.url)
            outcomes.append(first)

            if not first.ok:
                stop_reason = ("page_load_timeout" if first.load_failed
                               else f"blocked_{first.blocked_by}")
                blocked = first.blocked_by is not None
            elif args.mode != "listing":
                pass  # one page is the whole run
            else:
                seen_keys.update(p.sku for p in first.products if p.sku is not None)
                planned = _plan_page_urls(session.page, args, first.final_url)

                if args.pages > 1 and concurrency > 1 and planned is None:
                    logger.warning("--concurrency %d requested, but this "
                                   "listing's pagination cannot be addressed "
                                   "independently (see above) — falling back to "
                                   "one page at a time.", concurrency)
                    concurrency = 1

                if args.pages > 1 and concurrency > 1:
                    # Close the page-1 browser before starting workers: it has
                    # done its job, and holding it open would cost one more
                    # browser than asked for.
                    session.close()
                    specs = [(n, planned[n - 2]) for n in range(2, args.pages + 1)]
                    logger.info("Fetching pages 2-%d across %d workers%s.",
                                args.pages, concurrency,
                                f" over {len(pool)} exit(s)" if pool else "")
                    rest, unattempted, exhausted = _fetch_pages_concurrently(
                        args, pool, specs, concurrency)
                    outcomes.extend(rest)

                    failed = [o for o in rest if not o.ok]
                    if failed:
                        worst = min(failed, key=lambda o: o.page_num)
                        stop_reason = ("page_load_timeout" if worst.load_failed
                                       else f"blocked_{worst.blocked_by}")
                        blocked = any(o.blocked_by for o in rest)
                    elif exhausted:
                        stop_reason = "no_new_products"
                    elif unattempted:
                        # Should not happen without a failure or exhaustion,
                        # but say so rather than reporting a complete run.
                        stop_reason = "pages_unattempted"
                    session = None  # already closed
                else:
                    url = (planned[0] if planned else
                           _next_url_from_page(session.page, args, 1))
                    for page_num in range(2, args.pages + 1):
                        # A new exit per page is what actually spreads a run's
                        # volume, and it costs a browser relaunch: carrying the
                        # session across exits would defeat the point.
                        if pool and pool.rotates_per_page():
                            pool.advance(f"per-page rotation, page {page_num}")
                            session.relaunch()

                        outcome = _fetch_one_page(session, args, pool, page_num, url)
                        outcomes.append(outcome)
                        if not outcome.ok:
                            stop_reason = ("page_load_timeout" if outcome.load_failed
                                           else f"blocked_{outcome.blocked_by}")
                            blocked = outcome.blocked_by is not None
                            break

                        # Whether this page contributed anything not already
                        # seen. Kept as a running check because the condition is
                        # inherently sequential — "new" only means anything
                        # relative to the pages before it. The authoritative
                        # dedupe happens once, after the loop, in page order.
                        fresh_count = sum(1 for p in outcome.products
                                          if p.sku is None or p.sku not in seen_keys)
                        seen_keys.update(p.sku for p in outcome.products
                                         if p.sku is not None)

                        # A page past the first that contributes nothing new
                        # means the end of the results — or that pagination is
                        # looping back on itself. Either way there is nothing
                        # further to fetch, and this is the honest terminating
                        # condition: a property of the DATA, not of a CSS
                        # selector that may have been renamed.
                        if not fresh_count:
                            logger.info("Page %d added no rows not already seen "
                                        "— treating that as the end of the "
                                        "listing.", page_num)
                            stop_reason = "no_new_products"
                            break

                        if page_num < args.pages:
                            url = (planned[page_num - 1] if planned else
                                   _next_url_from_page(session.page, args, page_num))
                            time.sleep(args.delay)
        finally:
            if session is not None:
                session.close()

    # Merge once, in PAGE order — not in the order pages happened to finish.
    # At one page at a time the two are identical, which is the point: this is
    # what keeps the output byte-for-byte the same while removing the
    # dependency on arrival order that concurrency would otherwise introduce.
    all_rows = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_key(oc.products, merged_seen, key=dedupe_key)
        if len(fresh) < len(oc.products):
            # Not necessarily "on an earlier page" — a duplicate can be on
            # this page. MediaMarkt's own pagination was measured NOT to
            # repeat (0 skus shared across three consecutive pages), so a
            # non-zero count here is worth noticing rather than routine.
            logger.info("Page %d: dropped %d duplicate row(s).",
                        oc.page_num, len(oc.products) - len(fresh))
        all_rows.extend(fresh)

    # Completeness as arithmetic, checked over the MERGED result rather than
    # per page — a per-page check cannot see a gap BETWEEN two pages, which
    # is exactly where a short page hides. MediaMarkt states its own
    # catalogue size ("12 von 42930"), so what a full run should have
    # gathered is a number rather than a feeling.
    total_available = next((o.total_available for o in outcomes
                            if o.total_available is not None), None)
    if args.mode == "listing" and all_rows:
        pages_done = len([o for o in outcomes if o.ok])
        per_page = max((len(o.products) for o in outcomes if o.ok), default=0)
        expected = min(per_page * pages_done, total_available or 10 ** 9)
        if per_page and len(all_rows) < expected:
            logger.warning("Merged result holds %d row(s); %d page(s) at %d "
                           "per page should have given %d. The run is short by "
                           "%d — check the per-page counts above for which "
                           "page came back thin.", len(all_rows), pages_done,
                           per_page, expected, expected - len(all_rows))
        if total_available:
            logger.info("This listing holds %d product(s) in total; this run "
                        "took %d (%.1f%%).", total_available, len(all_rows),
                        100.0 * len(all_rows) / total_available)

    ok_pages = [o for o in outcomes if o.ok]
    failed_pages = [o.page_num for o in outcomes if not o.ok]
    final_url = (max(ok_pages, key=lambda o: o.page_num).final_url
                 if ok_pages else args.url)

    return finish_run(all_rows, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=len(ok_pages),
                      pages_failed=failed_pages, mode=args.mode,
                      source=site_host(final_url),
                      start_url=args.url, final_url=final_url)


def parse_args():
    p = argparse.ArgumentParser(description="MediaMarkt scraper (Playwright edition)")
    p.add_argument("--url", default=None,
                   help="MediaMarkt URL. A category grid (/de/category/...), "
                        "search results (/de/search.html?query=...) or a "
                        "product page (/de/product/...) depending on --mode. "
                        "Any of the ten country sites — the hostname decides "
                        "which. Required, unless MEDIAMARKT_URL is set in the "
                        "environment or in .env.")
    p.add_argument("--mode", choices=["listing", "product"],
                   default="listing",
                   help="listing (default): a paginated category grid or "
                        "search results, 12 products per page. product: one "
                        "/product/ page, which adds brand, EAN, description "
                        "and the full image list — the columns a listing row "
                        "cannot carry. There is no --pages for product mode.")
    p.add_argument("--category", default=None,
                   help="Label to tag output rows with. Defaults to the "
                        "category slug from the URL (and to the breadcrumb in "
                        "--mode product), so the column is never empty just "
                        "because the flag was omitted. A search URL has no "
                        "category in its path, so pass one if you want the "
                        "column filled on a search run.")
    p.add_argument("--pages", type=int, default=1,
                   help="Number of listing pages to crawl. Ignored outside "
                        "--mode listing.")
    p.add_argument("--delay", type=float, default=2.0, help="Delay between pages, seconds")
    p.add_argument("--concurrency", type=int, default=1, metavar="N",
                   help="Fetch pages through N parallel workers (default 1 — "
                        "unchanged sequential behaviour). Each worker runs its "
                        "own browser and holds its own proxy exit, so N>1 "
                        "without --proxy-file just sends N times the traffic "
                        "from one address. Ignored with --cdp-endpoint.")
    p.add_argument("--retries", type=int, default=3,
                   help="Attempts per page load before giving up (default 3). "
                        "The pause between attempts doubles each time. A page "
                        "that comes back EMPTY is not retried — see "
                        "page_flow.STATE_POLICY — because an empty hub "
                        "category is a correct answer, not a fault.")
    p.add_argument("--retry-delay", type=float, default=2.0,
                   help="Seconds before the first page-load retry, doubling "
                        "thereafter (default 2.0)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="mediamarkt_products", help="Output file prefix")
    p.add_argument("--locale", default="de-DE",
                   help="Browser locale (default de-DE, matching the site this "
                        "repo is verified against). MediaMarkt keys page "
                        "language off this and the exit IP. It does NOT decide "
                        "the currency: each country site quotes its own, and "
                        "the parser reads it from the page's structured data "
                        "rather than inferring it from anything here.")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL, e.g. http://ACCOUNT:PASSWORD@HOST:9999 "
                        "(2captcha.com/proxy)")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line (# comments and blank "
                        "lines skipped) to rotate across. Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run",
                   help="per-run (default): one exit for the whole run. per-page: "
                        "a new exit for every page — this is what spreads volume, "
                        "and it relaunches the browser each time so the session "
                        "does not follow the IP around.")
    p.add_argument("--proxy-shuffle", action="store_true",
                   help="Shuffle the pool at startup, so concurrent runs do not "
                        "all begin on the first exit in the file.")
    p.add_argument("--proxy-block-retries", type=int, default=2,
                   help="When a page comes back refused (HTTP 403) or behind "
                        "a captcha, retry it from this many OTHER exits before "
                        "giving up (default 2). Needs a pool of more than one; "
                        "ignored otherwise. This is the flag that matters most "
                        "on this site: the refusal is a property of the "
                        "ADDRESS, and a different exit is what clears it.")
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found. Off by "
                        "default so a failed run can't overwrite a good result "
                        "with an empty one; exit code is 4 either way.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a browser fingerprint from 2captcha's Fingerprint "
                        "API and apply it to the launched browser. Needs "
                        "--twocaptcha-key. Ignored with --cdp-endpoint, where the "
                        "Scraping Browser supplies its own.")
    p.add_argument("--fp-tags", default="Windows,Chrome,Desktop",
                   help="Fingerprint filter tags (default: Windows,Chrome,Desktop)")
    p.add_argument("--fp-country", default=None,
                   help="Fingerprint country, ISO 3166-1 alpha-2. Match it to "
                        "your proxy's exit country — a US fingerprint on a "
                        "German IP is a contradiction.")
    p.add_argument("--captcha-api", choices=["v2", "v1"], default="v2",
                   help="Which 2captcha solver API to use. v2 is the current "
                        "JSON API (api.2captcha.com/createTask); v1 is the "
                        "legacy in.php/res.php pair. Applies to both the image "
                        "captcha and reCAPTCHA.")
    p.add_argument("--solve-captcha", choices=["when-blocked", "always"],
                   default="when-blocked",
                   help="when-blocked (default): only pay to solve a challenge "
                        "if the content is not already readable. always: solve "
                        "whenever one is detected. Neither setting touches "
                        "MediaMarkt's HTTP 403 refusal, which carries no "
                        "challenge at all — no solve helps there, and none is "
                        "attempted or billed.")
    p.add_argument("--min-score", type=float, default=0.7,
                   help="reCAPTCHA v3 minimum score to request (0.3, 0.7 or 0.9 "
                        "— the API only accepts these three). Ignored for v2 "
                        "widgets.")
    p.add_argument("--cdp-endpoint", default=None,
                   help="Connect to an already-running browser over CDP instead "
                        "of launching Playwright's bundled Chromium, e.g. "
                        "ws://user:pass@host:port — the Scraping Browser API "
                        "endpoint, or any browser that exposes a CDP URL. "
                        "--proxy and --headless/--headful are ignored when this "
                        "is set.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success as "
                        "well as failure. Useful when the row count is right but "
                        "a column comes back empty — see TROUBLESHOOTING.md.")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    args = p.parse_args()
    # Fill --twocaptcha-key / --cdp-endpoint / --proxy / --url from the
    # environment or .env when the flag was not given. An explicit flag wins.
    env_config.apply(args)
    if not args.url:
        p.error("no --url given, and MEDIAMARKT_URL is not set in the "
                "environment or in .env.")
    if not is_supported_host(args.url):
        # Refused rather than attempted. The parser's selectors, its sku
        # pattern and its pagination convention are all MediaMarkt's, so
        # pointing this at another shop would not fail loudly — it would
        # return zero rows and look like an empty category.
        why = unsupported_reason(args.url)
        if why:
            p.error(f"{site_host(args.url)} {why}. Supported hosts: "
                    f"{', '.join(sorted(HOSTS))}.")
        p.error(f"{site_host(args.url) or args.url!r} is not a MediaMarkt "
                f"site. Supported hosts: {', '.join(sorted(HOSTS))}.")
    if args.mode != "listing" and args.pages != 1:
        # Said out loud rather than silently ignored: a user who passed
        # --pages 5 expects five pages of something.
        logger.warning("--pages %d is ignored in --mode %s: there is one page "
                       "to read. The run status will say single_page_mode.",
                       args.pages, args.mode)
        args.pages = 1
    if args.mode == "listing" and listing_kind(args.url) == "product":
        p.error("--url is a product page but --mode is listing. Use "
                "--mode product for a /product/ URL.")
    if args.mode == "product" and listing_kind(args.url) != "product":
        p.error(f"--mode product needs a URL containing /product/; got "
                f"{args.url}")
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.fingerprint and not args.twocaptcha_key:
        logger.error("--fingerprint needs --twocaptcha-key (the Fingerprint API "
                     "uses the same key, though it's a separate subscription "
                     "from solving).")
        sys.exit(2)
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "Scraping Browser supplies its own fingerprint, and "
                       "stacking a second one on top creates a mismatch rather "
                       "than better cover.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        # Bad usage, not a crash: a typo in a proxy list would otherwise
        # surface as a connection failure on page 1 with nothing naming it.
        logger.error("%s", e)
        sys.exit(2)
