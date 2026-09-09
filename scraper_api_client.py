#!/usr/bin/env python3
"""
mediamarkt-scraper — 2captcha Scraper API edition (fourth engine)
=================================================================

A fourth way to run this scraper. Unlike playwright_scraper.py /
puppeteer_scraper.py / selenium_scraper.py, this one manages **no browser and
no CDP session of its own**: it POSTs a URL to 2captcha's separate **Scraper
API** (https://scraper.2captcha.com — a different product from the Scraping
Browser API the other three reach through --cdp-endpoint), gets HTML back over
plain HTTPS, and feeds it to this project's product_parser.

Why you would want it: no Chromium to install, no CDP plumbing, runs from a
tiny container or a lambda.

WHAT THIS SITE NEEDS — READ THIS FIRST
--------------------------------------
MediaMarkt is an unusually good fit for a browserless path, and the reason is
measured rather than assumed.

**Nothing on these pages needs JavaScript.** Every product is in the HTML
that arrives: a plain `requests.get` through a residential exit returned 1.8
MB holding all twelve tiles, their prices, their ratings and the page's
`<link rel="next">`, with no browser in the path at all. That is how this
project's offline fixtures were cut, and a live browser rendering the same
URL produced the same twelve cards — eight scroll rounds changed nothing.
There is no hydration to wait for and no challenge script to run.

**What the request DOES need is a residential exit.** MediaMarkt answers a
datacentre address with HTTP 403 and its own branded error page, on every URL
of every country site (measured 2026-09-09 on five hosts). No amount of
browser fidelity changes that, which is exactly why a browserless path loses
nothing here: the browser was never what was being checked.

So this engine's success depends entirely on where the Scraper API's own
request leaves from. It fails loudly either way — a 403 page exits 3 rather
than being written as a result.

**Untested against mediamarkt.de.** No Scraper API subscription was available
while this repo was built, so unlike the three browser engines (each of which
has live numbers in the README) this one has none. Treat it as unproven.

Listing pages only. `--mode product` lives in the browser engines.

API surface used (per https://2captcha.com/scraper/scraper-api/api)
------------------------------------------------------------------
  POST https://scraper.2captcha.com/tasks/sync
    Authorization: Bearer <API_KEY>
    Content-Type: application/json
    {"task_type": "scrape", "url": ..., "data_format": "raw",
     "format": "json", "timeout": 1..120,
     "waitFor": "<JSON *string*, not an object>",
     "cdpurl": "ws://user:pass@host:port"   # optional
    }
  -> 200 {"status": 200, "headers": {...}, "body": "<!DOCTYPE html>..."}

Note `waitFor` must be a JSON *string* (double-encoded), and the param is
spelled `cdpurl` (all lowercase) while `waitFor` is camelCase — that is the
API's own inconsistency, not a typo here.

Usage
-----
    # plain HTTP, no browser anywhere
    python3 scraper_api_client.py \
        --key "$TWOCAPTCHA_KEY" \
        --url "https://www.mediamarkt.de/de/category/grills-116.html"

    # routed through a Scraping Browser API session, which is one way to get
    # challenge on the way
    python3 scraper_api_client.py \
        --key "$TWOCAPTCHA_KEY" \
        --url "https://www.mediamarkt.de/de/category/grills-116.html" \
        --cdp-url "ws://user:pass@cb.2captcha.com:9222" \
        --wait-text 's-search-result' --timeout 90

Requires: pip install -r requirements.txt
          (no playwright/selenium/pyppeteer needed for this engine)
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Optional

import requests

from product_parser import parse_products, detect_bot_challenge, BOT_CHALLENGE_MARKERS
from output_writer import save
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scraper_api_client")

API_BASE = "https://scraper.2captcha.com"
SYNC_ENDPOINT = f"{API_BASE}/tasks/sync"

# The API caps `timeout` at 120s and rejects bodies over 10,000 bytes.
MAX_API_TIMEOUT = 120

# Exit codes. Kept distinct from 2 (bad usage) on purpose: a remote API failing
# is not the operator passing wrong arguments, and a harness that lumps them
# together sends you looking in the wrong place. Run 7 reported `exit=2` for an
# HTTP 422 from the API — which reads as "you called it wrong".
EXIT_API_ERROR = 5

def _mask_credentials(url: str) -> str:
    """Never print a username:password embedded in a ws://... or http://... URL."""
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return url
    scheme, rest = url[:scheme_sep + 3], url[scheme_sep + 3:]
    _, _, host_part = rest.partition("@")
    return f"{scheme}***:***@{host_part}"


def _build_wait_for(args) -> Optional[str]:
    """`waitFor` must be a JSON STRING (double-encoded), per the API docs.
    Passing a nested object is silently wrong.

    Default (no flag): wait for the DOM. On a challenge-protected page
    that resolves instantly against the challenge page itself — which is
    exactly the trap documented in this module's docstring, so
    --wait-text/--wait-element exist to wait on something only the real
    page can contain."""
    if args.wait_text:
        return json.dumps({"text": args.wait_text})
    if args.wait_element:
        return json.dumps({"element": args.wait_element, "checkVisible": True})
    if args.wait_state:
        return json.dumps({"state": args.wait_state})
    return None


def fetch_html(args) -> str:
    payload = {
        "task_type": "scrape",
        "url": args.url,
        "data_format": "raw",   # we want HTML; product_parser does the rest
        "format": "json",       # so we get {"status", "headers", "body"}
        "timeout": min(args.timeout, MAX_API_TIMEOUT),
    }

    wait_for = _build_wait_for(args)
    if wait_for:
        payload["waitFor"] = wait_for
        logger.info("waitFor: %s", wait_for)

    if args.cdp_url:
        payload["cdpurl"] = args.cdp_url
        logger.info("Routing through an existing browser session: %s",
                    _mask_credentials(args.cdp_url))

    logger.info("POST %s (url=%s)", SYNC_ENDPOINT, args.url)
    resp = requests.post(
        SYNC_ENDPOINT,
        headers={"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"},
        json=payload,
        # Give the HTTP call more headroom than the API-side task timeout,
        # otherwise a task that legitimately runs the full 120s looks like
        # a client-side network failure.
        timeout=min(args.timeout, MAX_API_TIMEOUT) + 30,
    )

    # The API returns its own per-task metadata (price, timings, status)
    # in an x-debug header — worth logging, it's the only place the real
    # cost of the call shows up.
    debug = resp.headers.get("x-debug")
    if debug:
        logger.info("x-debug: %s", debug)

    if resp.status_code != 200:
        # 422 = task ran but errored (this is what a bad/unreachable
        # cdpurl produces: "CDP connect failed (user cdpurl) after N
        # attempts"); 402 = out of balance; 408 = sync wait exceeded.
        raise RuntimeError(
            f"Scraper API returned HTTP {resp.status_code}: {resp.text[:500]}"
        )

    body = resp.json()
    html = body.get("body") or ""
    upstream_status = body.get("status")
    logger.info("Upstream page status %s, %d bytes of HTML.", upstream_status, len(html))
    return html


def main() -> int:
    args = parse_args()

    if not args.key:
        logger.error("No 2captcha API key. Pass --key, or better, export TWOCAPTCHA_KEY.")
        return 2

    # A challenge page is not necessarily final (see _run_once), so a
    # single attempt is not evidence. Each retry is a fresh billable task —
    # $0.0005 at the observed rate — so the default is deliberately low.
    attempts = max(1, args.retries + 1)
    for attempt in range(1, attempts + 1):
        rc = _run_once(args, attempt, attempts)
        if rc != 3 or attempt == attempts:
            return rc
        logger.info("Challenge page on attempt %d/%d — retrying in %ds.",
                    attempt, attempts, args.retry_delay)
        time.sleep(args.retry_delay)
    return rc


def _run_once(args, attempt: int = 1, attempts: int = 1) -> int:
    if attempts > 1:
        logger.info("Attempt %d/%d", attempt, attempts)

    try:
        html = fetch_html(args)
    except requests.RequestException as e:
        logger.error("Network error talking to the Scraper API: %s", e)
        return EXIT_API_ERROR
    except RuntimeError as e:
        # HTTP 4xx/5xx from the API, including the 422 that a busy or
        # unreachable cdpurl produces.
        logger.error("%s", e)
        return EXIT_API_ERROR

    if args.dump_html:
        with open(args.dump_html, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Raw HTML written to %s", args.dump_html)

    vendor = detect_bot_challenge(html)
    if vendor:
        logger.error(
            "The Scraper API returned a %s bot-challenge page (%d bytes), not real content.",
            vendor, len(html),
        )
        logger.error("A challenge page is not a final answer — retry before concluding "
                     "anything (--retries). This site needs a rendered browser in the "
                     "path: pass --cdp-url, or use playwright_scraper.py / "
                     "puppeteer_scraper.py directly.")
        return 3

    products = parse_products(html, args.url, category=args.category)
    logger.info("Parsed %d products.", len(products))

    if not products:
        dump = f"{args.out}_scraperapi_debug.html"
        with open(dump, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning("0 products parsed — saved the raw response to %s so you can see "
                       "what actually came back.", dump)
        return 4

    return save(products, args.out, args.format, allow_empty=args.allow_empty)


def parse_args():
    p = argparse.ArgumentParser(
        description="MediaMarkt scraper — 2captcha Scraper API edition (no "
                    "local browser). These pages need no JavaScript, so this "
                    "path loses nothing — but the request still has to leave "
                    "from a residential address. NOTE: untested against "
                    "mediamarkt.de; see this file's docstring.")
    # NOT required: prefer the TWOCAPTCHA_KEY env var. A key passed on the
    # command line is visible to anyone who can run `ps`, and it lands in
    # shell history and in any log that echoes the command line.
    p.add_argument("--key", default=os.environ.get("TWOCAPTCHA_KEY"),
                   help="2captcha.com API key (sent as a Bearer token). "
                        "Defaults to $TWOCAPTCHA_KEY, which is the safer way to pass it.")
    p.add_argument("--url", default=None,
                   help="MediaMarkt listing URL (/de/category/... or "
                        "/de/search.html?query=...). Required, unless "
                        "MEDIAMARKT_URL is set in the environment or in .env.")
    p.add_argument("--category", default=None, help="Label to tag output rows with. Defaults to the category segment of the URL, so the column is never empty just because the flag was omitted.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="mediamarkt_products_scraperapi", help="Output file prefix")
    p.add_argument("--timeout", type=int, default=60,
                   help=f"API-side task timeout in seconds (1-{MAX_API_TIMEOUT}, default 60)")
    p.add_argument("--cdp-url", default=None,
                   help="Route the fetch through an existing browser session over CDP "
                        "(sent as the API's `cdpurl` param), e.g. ws://user:pass@host:port")
    wait = p.add_mutually_exclusive_group()
    wait.add_argument("--wait-text", default=None,
                      help="Wait until this string appears on the page, e.g. '$'. Use this "
                           "on protected sites — a DOM/load wait is satisfied instantly by "
                           "the challenge page itself.")
    wait.add_argument("--wait-element", default=None,
                      help="Wait until this CSS selector is visible, e.g. 'a[href*=\"-item-\"]'")
    wait.add_argument("--wait-state", choices=["load", "domcontentloaded"], default=None,
                      help="Wait for a page load state instead of specific content")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 products were parsed. Off by "
                        "default so a failed fetch can't overwrite a good result.")
    p.add_argument("--retries", type=int, default=1,
                   help="Extra attempts if a bot-challenge page comes back. One retry is "
                        "usually worth it. Each attempt is a separate billable task, so "
                        "this defaults to 1.")
    p.add_argument("--retry-delay", type=int, default=10,
                   help="Seconds between retries (default 10)")
    p.add_argument("--dump-html", default=None,
                   help="Also write the raw returned HTML to this path (always, even on success)")
    args = p.parse_args()
    # This client uses --key and --cdp-url rather than --twocaptcha-key and
    # --cdp-endpoint, so the env mapping is spelled out instead of defaulted.
    env_config.apply(args, keys={
        "TWOCAPTCHA_KEY": "key",
        "MEDIAMARKT_CDP_ENDPOINT": "cdp_url",
        "MEDIAMARKT_URL": "url",
    })
    if not args.url:
        p.error("no --url given, and MEDIAMARKT_URL is not set in the environment "
                "or in .env.")
    return args


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
