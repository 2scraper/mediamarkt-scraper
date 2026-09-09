# Troubleshooting

Ordered by how often each one is the answer. Every number here was measured
on **2026-09-09** against `mediamarkt.de`; where a symptom has more than one
cause, the section says how to tell them apart rather than listing guesses.

---

## Every page comes back exit 3 (blocked)

**This is almost always the exit address, and on this site that is not a
hedge.** MediaMarkt refuses datacentre and VPN IPs outright. Measured from a
hosting address in Amsterdam, on five hosts, in a real headless Chromium as
well as a plain HTTP client:

```
mediamarkt.de   403        mediamarkt.nl   403        mediaworld.it   403
mediamarkt.es   403        mediamarkt.pl   403
```

Identical bytes for every URL on a host. From a German residential address
the same request returns the full page. So:

- A run from a VPS, a cloud function, a CI runner or most consumer VPNs will
  be blocked on page 1 regardless of every other setting.
- A captcha key does **not** help. The refusal is the shop's own error page
  under a 403, with no challenge on it to solve. This scraper does not send
  it to the solver, and you should not pay for one expecting it to.
- A different *browser* does not help either. It is not browser detection.

**How to confirm it is this and not something else.** Run with
`--dump-html page.html` and look at what arrived:

| What the dump contains | What it is |
|---|---|
| "Ups, hier stimmt gerade etwas nicht" with full site navigation | the German block page |
| A near-empty document whose only visible text is "MediaMarkt" | the Spanish block page |
| A real page with a heading and sub-category links, no product grid | a **hub category** — see below, this is exit 4, not 3 |

**The fix** is a residential exit: `--proxy-file exits.txt`, or
`--cdp-endpoint` pointing at a Scraping Browser session with
`country-de` in it.

---

## Exit 4 (zero products) on a URL that plainly has products in a browser

Two causes, and they are easy to tell apart.

**A hub category.** MediaMarkt has category URLs that are landing pages of
sub-categories with no product grid at all. `/de/category/notebooks-680.html`
is one: HTTP 200, a real page, zero products. The run reports exit 4 because
that is the honest answer — the request was served exactly as asked and there
is nothing on it. Pick a leaf category instead, one that shows a grid and a
page count when you open it yourself.

Note that "no product cards in the markup" is *not* the same as "a hub".
`/de/category/tv-audio-202.html` renders zero `mms-product-card` elements
server-side and still publishes structured data for twelve televisions, all
of which this scraper returns. That is the JSON-LD-first order earning its
place; a DOM-only scraper loses those twelve completely.

**One page past the end.** Asking for page 9 of an 8-page listing gets a
valid page with nothing new on it. In a multi-page run this is not an error
at all: the run stops with `stop_reason: no_new_products`, which is a
**complete** result.

---

## The row count is right but a column is empty

Check `price_source` first. It is in every row for exactly this reason.

| Value | Meaning | If this is unexpected |
|---|---|---|
| `jsonld+dom` | The structured price and the rendered tile agreed. | Nothing to look at — this is 100% of rows on every page measured. |
| `jsonld` | Structured data only; no tile was found to confirm against. | The tile markup has moved. `original_price` and `lowest_price_30d` come from the tile ONLY, so they will be empty. |
| `dom` | No structured data at all; the price was read off the markup. | The JSON-LD is gone. Every column except `url` and `sku` is now weaker. |

Every run prints DOM-confirmation coverage per page and warns below 90%, so a
tile-markup change shows up in the log rather than as a quietly emptier
output.

**Columns that are empty on purpose**, so you do not go looking:

- `brand`, `ean`, `description`, `images` are null on every **listing** row.
  MediaMarkt's tiles carry no brand line and the listing JSON-LD has no
  `brand` key (96 tiles across eight pages, zero). Use `--mode product`,
  where the detail page publishes all four.
- `rating` is null wherever nobody has rated the product. The star widget is
  drawn on every tile and shows "0 von 5" when empty; that is not a score.
  `review_count` is `0` there, because *that* is a fact the page states.
- `lowest_price_30d` is null on most rows: it appears only beside a reduced
  price, where EU law requires it.
- `original_price` is null unless the tile shows a UVP. It is deliberately
  **not** filled from the "Tiefstpreis (30 Tage)" figure — see below.

`--dump-html PATH` writes the exact bytes the parser was given, on success as
well as failure, which is the only way to tell a parsing bug from a snapshot
taken too early.

---

## A product shows a negative or absurd discount

It should not, and if it does, that is a bug worth reporting with the sku.

MediaMarkt renders two struck-through prices in nearly identical markup:

- `UVP 59,99 €` — the manufacturer's recommended price, above the current
  price. This is `original_price`.
- `Tiefstpreis (30 Tage): 299,– €` — the lowest price charged in the last 30
  days, normally **below** the current price. This is `lowest_price_30d`.

A scraper that reads both into `original_price` reports a negative discount
on a product that is not discounted at all. This one keeps them apart, and
`discount_pct` is computed from `price` and `original_price` only. If you see
a discount at or below zero, the two got crossed somewhere.

---

## "Blocked by turnstile" over `--cdp-endpoint`, on a page that clearly loaded

Fixed here, and worth knowing if you write your own detector.

The Scraping Browser API's auto-solve extension injects its own captcha
hunters into every page it loads:

```html
<script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/hunter.js"
        data-ts-input="cf-turnstile-response"></script>
```

so `cf-turnstile` appears in the markup of a perfectly good category grid.
The first live run of this scraper reported exit 3 on a 1.8 MB page holding
the full catalogue for exactly this reason. This repo now strips
`chrome-extension://` and `moz-extension://` scripts before looking for
challenge markers, and never treats a marker as blocking when products have
already rendered.

If you are seeing this from another tool, that is where to look.

---

## HTTP 500 from the Scraping Browser endpoint

A profile (`pid-`) allows **one live connection at a time**. A 500 usually
means another run still holds it. Wait for that run to finish, or use a
different `pid` in the endpoint URL.

This is also why `--concurrency` is refused with `--cdp-endpoint`: N workers
would collide on one profile. Several `pid`s, one run each, is the way.

---

## Selenium: `--cdp-endpoint` or `--proxy` does not work

Both are real limits of the driver, not of this code, and both are refused or
warned about rather than silently failing:

- **An authenticated CDP endpoint is impossible.** Playwright's
  `connect_over_cdp` and pyppeteer's `browserWSEndpoint` take a full
  `ws://user:pass@host:port` and authenticate on the WebSocket upgrade.
  chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
  put a password.
- **An authenticated proxy is impossible.** `--proxy-server=` accepts no
  credentials and there is no equivalent of pyppeteer's `page.authenticate`.
  This repo strips the credentials and warns. On MediaMarkt that means the
  proxy will not authenticate and every page will be a 403 — so use the
  Playwright or pyppeteer engine when your exits need a password.

---

## A run stopped early and reported `partial` (exit 6)

The sidecar says which pages failed, by number:

```json
{"status": "partial", "stop_reason": "blocked_mediamarkt-403",
 "pages_requested": 20, "pages_completed": 7, "pages_failed": [8]}
```

`pages_completed` alone is not enough once pages can be fetched
concurrently — page 8 can fail while 9 and 10 succeed — which is why the list
is there. `diff_runs.py` refuses to compare a partial run against anything,
because its un-fetched pages would read as delisted products.

If `stop_reason` is a block partway through a long run, you are probably
burning one address too fast. Spread it: `--proxy-file` with more exits, or a
larger `--delay`.

---

## `pip check` complains after installing two engines

Expected. playwright and pyppeteer pin incompatible `pyee` versions, and
pyppeteer and selenium collide on `urllib3`. They do run side by side in
practice because neither library touches the incompatible part, but pip may
resolve the conflict by downgrading something you wanted. Use a virtualenv
per engine.

---

## Something else

`python3 smoke_test.py` runs 255 checks with no network, no browser and no
credentials. If it passes and a live run still misbehaves, the problem is in
the fetch rather than the parse — which narrows it to the exit address, the
engine, or the URL. If it fails, the message names the check.

Open an issue with: the exact command (with credentials removed), the log,
the `.meta.json` sidecar, and — if you can — the `--dump-html` output.
