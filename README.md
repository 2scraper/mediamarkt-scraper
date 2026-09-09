# mediamarkt-scraper

[![release](https://img.shields.io/github/v/release/2scraper/mediamarkt-scraper?sort=semver)](https://github.com/2scraper/mediamarkt-scraper/releases)
[![tests](https://github.com/2scraper/mediamarkt-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/mediamarkt-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/mediamarkt-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/mediamarkt-scraper/actions/workflows/canary.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![licence](https://img.shields.io/badge/licence-MIT-lightgrey)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20pyppeteer%20%7C%20CDP-informational)](#engines)
[![needs a residential IP](https://img.shields.io/badge/needs-a%20residential%20IP-orange)](#the-one-thing-you-actually-need)

Scrapes MediaMarkt category grids, search results and product pages across the
group's eleven country sites. JSON or CSV, one row schema for both modes, and
a run-metadata sidecar that says whether the result is complete.

Three engines: **Playwright** (primary), **Selenium**, **pyppeteer**, or a
remote browser over CDP such as the **Scraping Browser API**.

---

## The one thing you actually need

Most scrapers in this family open with what works for free. This one cannot,
and saying so is more useful than a pitch.

**MediaMarkt refuses every datacentre and VPN address.** Measured
**2026-09-09**, from a hosting IP in Amsterdam:

```
GET https://www.mediamarkt.de/de/category/filme-serien-486.html   -> 403
GET https://www.mediamarkt.es/es/category/smartphones-165.html    -> 403
GET https://www.mediamarkt.nl/nl/category/smartphones-486.html    -> 403
GET https://mediamarkt.pl/                                        -> 403
GET https://www.mediaworld.it/                                    -> 403
```

Identical bytes for every URL on a host. A real headless Chromium from the
same address got the same 403 — so this is IP reputation, not browser
detection, and no amount of stealth patching changes it.

**From a German residential address the same request just works** — and it
works without a browser at all:

```
$ python3 playwright_scraper.py \
    --url "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html" \
    --pages 3

Parsed 12 row(s) from page 1.  Price coverage: 12/12 (100%).  DOM confirmation: 12/12 (100%).
Parsed 12 row(s) from page 2.  Price coverage: 12/12 (100%).  DOM confirmation: 12/12 (100%).
Parsed 12 row(s) from page 3.  Price coverage: 12/12 (100%).  DOM confirmation: 12/12 (100%).
This listing holds 2311 product(s) in total; this run took 36 (1.6%).
[+] Saved 36 products -> mediamarkt_products.json
[+] Wrote run metadata -> mediamarkt_products.meta.json (status=complete)
$ echo $?
0
```

The pages are fully server-rendered: a plain `requests.get` through the same
exit returns the same 1.8 MB of HTML with every product in it, no JavaScript
executed. The browser is not what MediaMarkt is checking.

So what do the 2Captcha products buy here?

| Product | What it is for on MediaMarkt |
|---|---|
| **Proxies** (`--proxy-file`) | The requirement, not an optimisation. A residential exit is the difference between 403 on every URL and 200 on every URL. Also the only way to choose which country site you are served. |
| **Scraping Browser API** (`--cdp-endpoint`) | A browser you do not run or patch, with a persistent profile and a chosen exit country, in one endpoint instead of a proxy plus a local Chromium. |
| **Captcha solving** (`--twocaptcha-key`) | **Not for the block page.** MediaMarkt's refusal is its own branded error page under a 403 with no challenge on it — nothing to solve, and this scraper does not try. The key is for the reCAPTCHA the site uses on account and checkout flows, and for fingerprints. |
| **Fingerprints** (`--fingerprint`) | A consistent device identity across runs, matched to the exit country. |

If you already have residential exits from somewhere else, this repo works
with them: `--proxy-file` takes any list.

---

## Install

```bash
git clone https://github.com/2scraper/mediamarkt-scraper
cd mediamarkt-scraper
pip install -r requirements.txt -r requirements-playwright.txt
playwright install chromium
```

**Install exactly one engine.** The three declare mutually unsatisfiable pins
(playwright and pyppeteer disagree on `pyee`, pyppeteer and selenium on
`urllib3`). They do run side by side in practice, because neither library
touches the incompatible part — but `pip check` reports the conflict and pip
may resolve it by downgrading something you wanted. Use a virtualenv per
engine if you need more than one.

Run the offline suite first. It needs no network, no browser and no
credentials:

```bash
python3 smoke_test.py
```

---

## Usage

```bash
# A category grid, three pages
python3 playwright_scraper.py \
  --url "https://www.mediamarkt.de/de/category/grills-116.html" --pages 3

# Search results paginate the same way
python3 playwright_scraper.py \
  --url "https://www.mediamarkt.de/de/search.html?query=usb-c+kabel" --pages 5

# One product page: adds brand, EAN, description and the full image list
python3 playwright_scraper.py --mode product \
  --url "https://www.mediamarkt.de/de/product/_harry-potter-the-complete-collection-dvd-2920911.html"

# Through a pool of residential exits, four pages at a time
python3 playwright_scraper.py \
  --url "https://www.mediamarkt.de/de/category/grills-116.html" \
  --pages 20 --concurrency 4 --proxy-file exits.txt

# Through the Scraping Browser API instead of a local browser
python3 playwright_scraper.py \
  --url "https://www.mediamarkt.de/de/category/grills-116.html" \
  --cdp-endpoint "ws://{login}-zone-scraping_browser-country-de-pid-{profileId}:{password}@cb.2captcha.com:9222"
```

Credentials belong in `.env`, never on a command line — a secret in `argv` is
readable by anything that can run `ps` and lands in your shell history. Copy
`.env.example` to `.env` and run `python3 env_config.py` to see what was
picked up (it prints no secrets).

### Country sites

Eleven, and the list is not guessed: it is exactly what mediamarkt.de
declares in its own `hreflang` set.

| Host | Locale | Currency |
|---|---|---|
| mediamarkt.de | de-DE | EUR |
| mediamarkt.at | de-AT | EUR |
| mediamarkt.ch | de-CH, fr-CH, it-CH | CHF |
| mediamarkt.nl | nl-NL | EUR |
| mediamarkt.be | nl-BE, fr-BE | EUR |
| mediamarkt.lu | fr-LU | EUR |
| mediamarkt.es | es-ES | EUR |
| mediaworld.it | it-IT | EUR |
| mediamarkt.pl | pl-PL | PLN |
| mediamarkt.hu | hu-HU | HUF |
| mediamarkt.com.tr | tr-TR | TRY |

There is no `--country` flag: the hostname in `--url` decides, so a flag and
a URL cannot disagree about which shop a run is reading. A host outside this
list is refused with exit 2 rather than attempted — the selectors, the
article-number pattern and the pagination convention are all MediaMarkt's,
and pointing them at another shop would not fail loudly, it would return zero
rows and read as an empty category.

**Three sites are live-verified: `.de`, `.es` and `.pl`.** All three
returned 12 products a page with 100% of rows confirmed against a rendered
tile, in EUR, EUR and PLN respectively — so the parser handles a second
language and a non-euro currency without a special case. The remaining eight
share the platform and the markup and are expected to work, but "expected" is
not "measured" and this README does not promise what was not run.

**Which exits a site accepts is not uniform, and this is the practical
catch.** From one German residential address on 2026-09-09:

| Accepted (HTTP 200) | Refused (HTTP 403) |
|---|---|
| mediamarkt.de, mediamarkt.es, mediamarkt.pl | mediamarkt.at, mediamarkt.nl, mediamarkt.be, mediamarkt.ch, mediamarkt.lu, mediaworld.it, mediamarkt.com.tr, mediamarkt.hu |

So a residential exit is necessary but not always sufficient: the safe
assumption is that you need an exit the target site accepts, and an exit in
that country is the obvious candidate. That was not tested for the eight
above, so it is stated as an assumption rather than as a result.

Saturn (saturn.de, saturn.at) is a sibling brand on similar markup that is
deliberately **not** in the list, because it is not in MediaMarkt's hreflang
set and has not been checked.

---

## What you get

One row per product, same schema in both modes and in JSON and CSV. See
[`sample_output.json`](sample_output.json) — cut from a real run, not written
by hand.

```json
{
  "source": "mediamarkt.de",
  "scraped_at": "2026-09-09T09:32:33.779503+00:00",
  "url": "https://www.mediamarkt.de/de/product/_gorenje-rk518e2s4-...-3047256.html",
  "sku": "3047256",
  "title": "GORENJE RK518E2S4 Kühlgefrierkombination (E, 269 l, 1800 mm hoch, Silber)",
  "brand": null,
  "price": 279.0,
  "currency": "EUR",
  "original_price": 459.0,
  "discount_pct": 39.2,
  "rating": 4.2,
  "review_count": 5,
  "in_stock": true,
  "image_url": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_180626469",
  "category": "kühlen-gefrieren",
  "price_source": "jsonld+dom",
  "page": 2,
  "position": 4,
  "lowest_price_30d": null,
  "ean": null,
  "description": null,
  "images": null
}
```

The first sixteen columns are this scraper family's shared prefix, in the
same order in every repo, so a consumer written against one reads another
unchanged. `page`, `position`, `lowest_price_30d`, `ean`, `description` and
`images` are MediaMarkt's own and come after it.

### `lowest_price_30d` is not an old price

MediaMarkt renders **two** struck-through prices, and they mean opposite
things:

| On the page | Column | What it is |
|---|---|---|
| `UVP 59,99 €` | `original_price` | The manufacturer's recommended price. Above the current price. |
| `Tiefstpreis (30 Tage): 299,– €` | `lowest_price_30d` | The lowest price charged in the last 30 days, which EU price-indication law requires beside a reduced price. Usually **below** the current price. |

A scraper that treats any strikethrough as a was-price reports
`original_price: 299` against `price: 349` — a negative discount on a product
that is not discounted at all. Both forms are common (22 UVP and 14
Tiefstpreis nodes across the captured pages), so this is not an edge case.
`discount_pct` is computed from `price` and `original_price` only, and is
`null` whenever there is no UVP.

### `price_source` says how much to trust the price

`jsonld+dom` means the structured price and the rendered tile agreed —
100% of rows on every page measured so far. `jsonld` means no tile was found
to confirm it against. `dom` means the structured data was missing entirely
and the price was read off the markup.

`diff_runs.py` reports a price difference that comes with a `price_source`
difference as `source_changed` rather than `changed`, and `--fail-on-change`
ignores it: that says something about our own two snapshots, not about what
MediaMarkt charges.

---

## Traps that look like bugs

Each of these is the site behaving normally. A reader who hits one unwarned
concludes the tool is broken.

**A category URL that returns zero products.** MediaMarkt has *hub*
categories — landing pages that list sub-categories and carry no grid.
`/de/category/notebooks-680.html` is one: HTTP 200, a real page, no products.
That is exit 4 (`no products`), not exit 3 (`blocked`), and no proxy or
captcha key changes it. Pick a leaf category — one with an actual product
grid.

**`brand` is null on every listing row.** Measured, not missed: MediaMarkt's
tile markup carries no brand line and the listing's JSON-LD publishes no
`brand` key (checked across 96 tiles on eight pages). It IS the leading token
of the title on most products and *not* on all of them — "OK. OFK 411",
"PLAION PICTURES" — so splitting the title would produce a column that is
sometimes wrong, which is worse than one that is honestly empty. Use
`--mode product` where you need it: the detail page publishes `brand`
properly, along with the EAN.

**`rating` is null on plenty of rows.** MediaMarkt draws the star widget on
every tile, and an unrated product gets "0 von 5 Sternen" with a count of 0.
That is the widget's empty state, not a score, so the row reports
`rating: null` and `review_count: 0`. (An earlier version of this scraper
read it as `0.0` and filled the column on 84 of 84 rows — a column that
looked complete and said a brand-new release was rated zero stars.)

**Twelve products a page, not twenty.** That is the page size. The site
offers a "12 weitere Produkte anzeigen" button, but `?page=N` addresses every
page directly, so this scraper paginates rather than clicking. Nothing
lazy-loads: eight scroll rounds on a live category page left the card count
at 12 and the document height unchanged at 13648px.

**A run that asked for 50 pages stops early.** Each listing prints its own
catalogue size ("12 von 2311"), and the run stops when a page adds no product
it has not already seen. `stop_reason: no_new_products` in the sidecar is a
*complete* result, not a truncated one.

---

## Engines

All three produce the same rows, the same exit codes and the same run status;
the decisions that determine them live in `page_flow.py` and
`output_writer.finish_run()` so they cannot drift apart. Playwright is the
primary engine and the only one with `--concurrency`.

Known limits, stated here rather than left to be discovered:

- **Selenium cannot use an authenticated remote CDP endpoint.** Playwright's
  `connect_over_cdp` and pyppeteer's `browserWSEndpoint` take a full
  `ws://user:pass@host:port` and authenticate on the WebSocket upgrade;
  chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
  put a password. It is not a generic "connect to CDP" option.
- **Selenium cannot authenticate a proxy at all.** `--proxy-server=` accepts
  no credentials. This repo strips them and warns rather than letting you
  believe a `user:pass` URL is doing something — which matters more here than
  elsewhere, since an unauthenticated proxy means a 403 on every page.
- **pyppeteer is effectively unmaintained** and its own README points at
  Playwright.

### Concurrency

`--concurrency N` fetches pages through N parallel workers. It is safe here
because pagination is *addressable*: page 1's own `<link rel="next">` reads
`?page=2`, exactly what the URL convention would build, so page 5's address
is knowable without fetching page 4. The scraper checks that agreement on
every run and falls back to sequential chaining if the site ever starts
issuing a cursor it cannot reproduce.

A worker owns one browser and one exit for its lifetime, and each starts on a
*different* exit, so no thread needs a lock. Page 1 is always fetched alone.

Two refusals worth knowing: `--concurrency` with no proxy pool warns rather
than refuses (N workers then send N× the traffic from one address, and on
this site an address that works is one worth not burning), and it is refused
outright with `--cdp-endpoint`, because a Scraping Browser profile allows one
live connection — several `pid`s, one run each, is the way.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Complete |
| `1` | Crash |
| `2` | Bad usage (including a URL that is not a MediaMarkt host) |
| `3` | Blocked — the 403 refusal, or a challenge |
| `4` | Zero products — including a hub category, which is a correct answer |
| `5` | Remote API error |
| `6` | Partial — some pages fetched, then stopped early |

**A run that finds nothing writes nothing.** Last night's good output is not
replaced with `[]`; `--allow-empty` is the opt-out. A consumer cannot tell an
empty category from a failed run, and the failure destroys the last known
good data. A failed run writes no sidecar either, because a `"failed"`
sidecar beside good data would contradict it.

`<out>.meta.json` records `status`, `stop_reason`, `mode`, `source` and
**which** pages failed by number — a count stops being a description once a
page can fail while later ones succeed.

---

## Comparing two runs

```bash
python3 diff_runs.py --old monday.json --new tuesday.json --fail-on-change
```

Reports added, removed and changed products by `sku`. It refuses to compare
two runs that are not both `complete`, because a partial run's un-fetched
pages otherwise read as delisted products, and it refuses a pair whose modes
or sources differ.

---

## Troubleshooting

**Every page comes back exit 3.** Check the exit address first: this site
refuses datacentre IPs outright, so a run from a VPS, a CI runner or most
VPNs will be blocked on page 1 regardless of settings. `--dump-html` writes
the page the parser was given; the German block page says "Ups, hier stimmt
gerade etwas nicht" and the Spanish one is almost blank.

**Exit 4 on a URL that plainly has products in a browser.** You are probably
on a hub category — see the traps above — or one page past the end of the
listing.

**"Blocked by turnstile" over `--cdp-endpoint`, on a page that clearly
loaded.** Fixed in this repo, and worth knowing about if you write your own
detector: the Scraping Browser API's auto-solve extension injects its own
captcha hunters into every page it loads, so `cf-turnstile` appears in the
markup of a perfectly good category grid. This scraper strips
`chrome-extension://` scripts before looking for challenge markers, and never
treats a marker as blocking when products have already rendered.

**HTTP 500 from the Scraping Browser endpoint.** A profile allows one live
connection at a time; another run is probably still holding that `pid`.

More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Measurements in this README

Everything above was measured rather than estimated. The numbers come from
live runs on **2026-09-09** from a German residential exit against
`mediamarkt.de`, `mediamarkt.es` and `mediamarkt.pl`, and from twenty-odd
page captures taken the same day. The offline suite pins the field values
from those captures — including a Polish listing, which is a second language,
a non-euro currency and one of the two hosts that answer without a `www.`
prefix — so a change in the site's markup fails a test rather than quietly
emptying a column.

`smoke_test.py`: **269 checks**, no network, no browser, no credentials.

---

## Legal

For research, price monitoring and comparison. You are responsible for
complying with MediaMarkt's terms, with `robots.txt`, and with the data
protection law that applies to you. This repo reads publicly rendered product
listings; it does not attempt to reach anything behind an account.

MIT licensed — see [LICENSE](LICENSE).

Built by [2Captcha](https://2captcha.com).
