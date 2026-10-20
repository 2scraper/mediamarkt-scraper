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


# Appended to the "refused" log line by every engine. A refusal body is identical
# for a datacentre address and for a residential one in the wrong country, so
# the response cannot tell the two apart — the README measured one German
# residential address accepted by .de/.es/.pl and refused by the other seven.
BLOCK_COUNTRY_HINT = (" If the exit is already residential, its COUNTRY must be "
                      "the site's own: a German address is accepted by .de, .es "
                      "and .pl and refused by the other seven (see README).")


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


# ---------------------------------------------------------------------------
# --retries
# ---------------------------------------------------------------------------
import argparse as _argparse  # noqa: E402  (kept beside its only user)


def attempts(value) -> int:
    """argparse `type=` for --retries. Rejects anything below 1.

    WHY THIS IS A USAGE ERROR AND NOT A CLAMP. The page-load loop is
    `for attempt in range(1, retries + 1)`, so `--retries 0` makes its body
    unreachable: nothing is fetched, and the engine then judges whatever the
    browser holds — `about:blank`, 39 bytes of
    `<html><head></head><body></body></html>` — and reports a block or an
    empty result in a few dozen milliseconds. That is a confident, fast,
    plausible answer to a question nobody asked.

    Measured on 2026-10-13 against sleepnumber-scraper, from an address the
    site genuinely does refuse, so the conclusion even looked right:

        --retries 0   exit 3, 39 bytes, 36 ms    <- never left the process
        --retries 1   exit 3, 923 bytes, 116 ms  <- the real CloudFront 403

    A network round trip does not finish in 36 ms. Three third-party audits of
    the 2scraper scrapers have now run `--retries 0`, read that output, and
    reported a working scraper as broken or a site as unreachable.

    The flag counts ATTEMPTS, not re-tries, so a caller who writes 0 almost
    certainly means "do not retry", which is 1. Clamping would hide the
    mistake; saying so ends the ambiguity.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise _argparse.ArgumentTypeError(
            f"--retries must be an integer, not {value!r}")
    if number < 1:
        raise _argparse.ArgumentTypeError(
            f"--retries must be at least 1, not {number}. It counts ATTEMPTS, "
            f"not re-tries, so --retries 1 means 'try once and do not retry'. "
            f"With 0 the load loop never runs, nothing is fetched, and the "
            f"engine reports the browser's blank page as a result.")
    return number


def _bounded_int(flag: str, minimum: int, why: str):
    """An argparse `type=` that rejects integers below `minimum`, naming the flag."""
    def parse(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise _argparse.ArgumentTypeError(
                f"{flag} must be an integer, not {value!r}")
        if number < minimum:
            raise _argparse.ArgumentTypeError(
                f"{flag} must be at least {minimum}, not {number}. {why}")
        return number
    parse.__name__ = flag.lstrip("-").replace("-", "_")
    return parse


def _non_negative_float(flag: str):
    """An argparse `type=` for a delay in seconds: 0 or more, naming the flag."""
    def parse(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _argparse.ArgumentTypeError(
                f"{flag} must be a number of seconds, not {value!r}")
        if not number >= 0:  # also rejects NaN
            raise _argparse.ArgumentTypeError(
                f"{flag} must be 0 or more seconds, not {value}. "
                f"time.sleep() raises on a negative value.")
        return number
    parse.__name__ = flag.lstrip("-").replace("-", "_")
    return parse


# A negative --pages planned no page at all and wrote nothing; a negative
# --concurrency or --proxy-block-retries made `range()` empty; a negative
# delay crashed in time.sleep() after page 1 had been fetched. Each is a typo
# that deserves a usage error (exit 2) naming the flag, in every engine.
pages_arg = _bounded_int("--pages", 1, "It is the number of listing pages to fetch.")
concurrency_arg = _bounded_int("--concurrency", 1, "It is the number of workers.")
block_retries_arg = _bounded_int(
    "--proxy-block-retries", 0,
    "It counts EXTRA exits to try after a refusal; 0 means do not rotate.")
delay_arg = _non_negative_float("--delay")
# scraper_api_client.py counts EXTRA attempts (each one billable), so 0 is
# legal there and means "no retry"; only a negative value is a typo.
extra_retries_arg = _bounded_int(
    "--retries", 0, "Here it counts EXTRA attempts after the first; 0 means none.")
api_retry_delay_arg = _bounded_int("--retry-delay", 0, "It is a wait in seconds.")
retry_delay_arg = _non_negative_float("--retry-delay")


# ---------------------------------------------------------------------------
# Paid captcha solves: one allowance per page
# ---------------------------------------------------------------------------
# A solve is a purchase. Every engine checks the captcha once per fetch
# attempt, and a refused page is fetched again from another exit up to
# --proxy-block-retries times, so without an allowance one page could buy a
# solve on every attempt: measured before this existed, a pool of 4 exits
# with --proxy-block-retries 3 bought 4 solves for one page.
SOLVES_PER_PAGE = 1


class SolveBudget:
    """How many paid solves one page may still buy.

    Created once per page, BEFORE the block-retry loop, and passed to every
    place that can buy a solve. It is not reset by a retry or by a rotation:
    a fresh exit is a reason to fetch again, not a fresh allowance. The check
    happens before the purchase, so a refused `take()` costs nothing.
    """

    def __init__(self, limit: int = SOLVES_PER_PAGE):
        self.limit = limit
        self.spent = 0

    def take(self) -> bool:
        if self.spent >= self.limit:
            return False
        self.spent += 1
        return True


# ---------------------------------------------------------------------------
# Traffic savers behind a paid proxy (the helpers are in proxy_pool.py)
# ---------------------------------------------------------------------------
# One HEAD for the start host's robots.txt, through the proxy, before a local
# browser is launched. Short on purpose: a slow answer is not a verdict, and
# the browser run then goes ahead exactly as it did before.
PROXY_PRECHECK_TIMEOUT = 20.0


def proxy_precheck(args, proxy_url: Optional[str]) -> Optional[int]:
    """Pre-check `proxy_url`; return the exit code to stop with, or None.

    A gateway 407 or 402 prints why (naming MEDIAMARKT_PROXY, never its
    value), writes `<out>.latest_attempt.meta.json` with status `failed` and
    stop_reason `proxy_auth_failed`, leaves the data and `<out>.meta.json`
    untouched, and returns exit 5. Not sent over --cdp-endpoint (that browser
    has its own exit) or without a proxy. Anything else — a 200, a 403 from
    the site, a timeout — returns None and the run carries on unchanged.
    """
    import sys
    import output_writer
    import proxy_pool
    from product_parser import site_host
    if not proxy_url or getattr(args, "cdp_endpoint", None):
        return None
    try:
        proxy_pool.precheck_proxy(proxy_url, proxy_pool.robots_url(args.url),
                                  timeout=PROXY_PRECHECK_TIMEOUT)
    except proxy_pool.ProxyPrecheckError as exc:
        host = site_host(args.url) or output_writer.SOURCE_DEFAULT
        print(proxy_pool.precheck_message(exc, "MEDIAMARKT_PROXY", host),
              file=sys.stderr)
        try:
            output_writer.write_failed_attempt(
                args.out, stop_reason=proxy_pool.PROXY_PRECHECK_STOP_REASON,
                mode=args.mode, start_url=args.url, pages_requested=args.pages,
                transport_error="proxy pre-check: %s" % exc, source=host)
        except OSError as write_exc:
            # The verdict stands without its record: still exit 5, not a crash.
            print("[!] could not write the attempt metadata: %s" % write_exc,
                  file=sys.stderr)
        return output_writer.EXIT_FETCH_FAILED
    return None


def add_media_flags(parser) -> None:
    """`--load-media` (documented) and the hidden `--force-block-media`."""
    parser.add_argument(
        "--load-media", action="store_true",
        help="Load images, video and web fonts. By default they are aborted "
             "while a local browser goes through --proxy / --proxy-file (no "
             "parser here reads them, and a proxy bills per byte); nothing is "
             "blocked with --cdp-endpoint or with no proxy.")
    # Turns blocking on without a proxy, so its effect on parsing can be
    # measured for free. Not a user feature.
    parser.add_argument("--force-block-media", action="store_true",
                        help=_argparse.SUPPRESS)
