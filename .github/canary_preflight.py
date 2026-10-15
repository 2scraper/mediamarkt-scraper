#!/usr/bin/env python3
"""
Check the canary's proxy BEFORE the browser run, and name what is wrong.

Why this exists: from 2026-09-10 the daily canary failed on every run with
the same three lines, `Timeout loading ... (attempt 1/3)`, `2/3`, `Gave up`,
then exit 5. The scraper cannot say more than that: Chromium reports a proxy
that accepts a connection and never delivers anything as a plain navigation
timeout. This script asks the same proxy the same question with short
timeouts and turns the answer into one of a few named causes, so a red run
says "the proxy secret is dead" instead of "unexpected exit code".

It reads the proxy from MEDIAMARKT_PROXY and never prints it: only the host
and port (which exit, not the secret) and the two-letter region code in the
login, if there is one.

Exit codes:
    0  the proxy delivered a page from the site (the browser run goes ahead)
    1  the proxy is unusable, and the ::error:: line says how
    2  MEDIAMARKT_PROXY is not set

A 403 from the site through the proxy is reported as a warning and exits 0:
the browser run is the real test of whether the exit is accepted, and a
plain HTTP client is a weaker witness than a browser.
"""

import os
import re
import socket
import sys
from urllib.parse import urlparse

TARGET = os.environ.get(
    "CANARY_URL",
    "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html")
CONNECT_TIMEOUT = float(os.environ.get("PREFLIGHT_CONNECT_TIMEOUT", "15"))
READ_TIMEOUT = float(os.environ.get("PREFLIGHT_READ_TIMEOUT", "45"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def error(msg):
    print(f"::error::{msg}")


def where(proxy):
    """host:port of the proxy, which is safe to print."""
    p = urlparse(proxy)
    return f"{p.hostname}:{p.port}"


def region_in_login(proxy):
    """The exit country the login asks for, e.g. 'de', or None."""
    user = urlparse(proxy).username or ""
    m = re.search(r"(?:region|country)-([a-zA-Z]{2})\b", user)
    return m.group(1).lower() if m else None


def main():
    proxy = (os.environ.get("MEDIAMARKT_PROXY") or "").strip()
    if not proxy:
        error("MEDIAMARKT_PROXY is not set. Add a residential German exit as "
              "that repository secret.")
        return 2
    if "://" not in proxy:
        proxy = "http://" + proxy
    p = urlparse(proxy)
    if not p.hostname or not p.port:
        error("MEDIAMARKT_PROXY is not a URL of the form "
              "scheme://user:password@host:port (value not printed).")
        return 1

    region = region_in_login(proxy)
    print(f"proxy {where(proxy)}; credentials "
          f"{'present' if p.username else 'ABSENT'}; region in login: "
          f"{region or 'none'}")
    if region and region != "de":
        print(f"::warning::The proxy login asks for region '{region}'. This "
              f"canary scrapes mediamarkt.de, which refuses most non-German "
              f"exits; the login should ask for 'de'.")

    # 1. Is anything listening at all?
    try:
        socket.create_connection((p.hostname, p.port), timeout=CONNECT_TIMEOUT).close()
    except OSError as e:
        error(f"Cannot open a TCP connection to the proxy {where(proxy)} "
              f"({type(e).__name__}). The host or port in MEDIAMARKT_PROXY is "
              f"wrong, or the gateway is down.")
        return 1

    import requests

    # 2. Does it carry a request to the site?
    try:
        r = requests.get(TARGET, proxies={"http": proxy, "https": proxy},
                         headers={"User-Agent": UA,
                                  "Accept-Language": "de-DE,de;q=0.9"},
                         timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                         allow_redirects=True)
    except requests.exceptions.ProxyError as e:
        text = str(e)
        if "407" in text:
            error(f"The proxy {where(proxy)} rejected the credentials (HTTP "
                  f"407). The login or password in MEDIAMARKT_PROXY is wrong "
                  f"or has expired: replace the secret.")
        else:
            error(f"The proxy {where(proxy)} accepted the connection but would "
                  f"not open a tunnel to the site ({_short(text)}). This is "
                  f"what an expired, suspended or out-of-traffic proxy "
                  f"account looks like: replace the MEDIAMARKT_PROXY secret.")
        return 1
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as e:
        error(f"The proxy {where(proxy)} accepted a TCP connection but the "
              f"page did not arrive within {CONNECT_TIMEOUT:.0f}+"
              f"{READ_TIMEOUT:.0f} s ({type(e).__name__}). The browser run "
              f"would time out the same way (exit 5). A gateway that answers "
              f"and then delivers nothing is what an expired or out-of-traffic "
              f"proxy account looks like: replace the MEDIAMARKT_PROXY secret "
              f"with a working residential German exit.")
        return 1
    except requests.exceptions.RequestException as e:
        error(f"Requesting the site through the proxy {where(proxy)} failed: "
              f"{type(e).__name__}: {_short(str(e))}")
        return 1

    final_host = urlparse(r.url).hostname or ""
    print(f"site answered HTTP {r.status_code}, {len(r.content)} bytes, "
          f"final host {final_host}")
    if r.status_code == 403:
        print("::warning::MediaMarkt refused this plain HTTP request through the "
              "proxy (403). If the browser run below is refused too (exit 3), "
              "the exit is not a residential German address.")
    elif r.status_code >= 400:
        print(f"::warning::The site answered HTTP {r.status_code} through the "
              f"proxy.")
    elif "mediamarkt.de" not in final_host:
        print(f"::warning::The request was redirected to {final_host}: the exit "
              f"is not in Germany, and the canary's source check will fail.")
    return 0


def _short(text):
    """An exception message without anything that could be a credential."""
    text = re.sub(r"[^\s/:@]+:[^\s/@]+@", "***@", text)
    return text[:200]


if __name__ == "__main__":
    sys.exit(main())
