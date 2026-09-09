# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) as closely
as a CLI toolkit can. In practice that means: **a patch release fixes things**
— it does not promise that every flag and every default is frozen. Where a
patch changes behaviour an existing user would notice, the release notes lead
with it, so nobody discovers it from a bill or from a diff.

---

## [0.1.0] — 2026-09-09

First release of the rewritten scraper. This replaces a much earlier
three-script version that shared nothing with the rest of this scraper family;
nothing from it survives, and the notes below describe the repo as it now
stands rather than a diff against it.

### Added

- **Two modes.** `--mode listing` (default) reads a paginated category grid or
  search results; `--mode product` reads one product page and adds `brand`,
  `ean` (the GTIN-13), `description` and the full image list — the columns a
  listing row cannot carry. Both produce the same `Product` row, so
  `diff_runs.py` can compare them.
- **Three engines** — Playwright (primary), Selenium and pyppeteer — plus a
  remote browser over CDP via `--cdp-endpoint`, and a browserless
  `scraper_api_client.py`. All produce the same rows, exit codes and run
  status; the decisions that determine them live in `page_flow.py` and
  `output_writer.finish_run()` so they cannot drift apart.
- **Eleven country sites**, taken from mediamarkt.de's own `hreflang`
  declaration rather than guessed. The hostname in `--url` decides which, so
  there is no `--country` flag that could disagree with it. A host outside the
  list is refused with exit 2.
- **`--concurrency N`.** Safe here because pagination is addressable: page 1's
  own `<link rel="next">` is exactly what the `?page=N` convention builds, and
  the scraper verifies that agreement on every run before planning ahead.
- **`lowest_price_30d`**, a column of its own for MediaMarkt's EU Omnibus
  "Tiefstpreis (30 Tage)" disclosure. See the warning below.
- **`price_source`** on every row (`jsonld+dom` / `jsonld` / `dom`), and
  per-page DOM-confirmation logging that warns below 90%.
- **Catalogue arithmetic.** Each listing prints its own size ("12 von 2311"),
  which the run records and uses to say what fraction it took, rather than
  guessing whether it got everything.
- `smoke_test.py`: 255 offline checks, no network, no browser, no credentials.
  Every field assertion pins a VALUE read off a real capture, not a
  not-null check.

### Site behaviour worth knowing before the first run

- **A residential exit is required, not recommended.** Measured 2026-09-09
  from a hosting address, on five hosts: HTTP 403 for every URL, in a real
  headless Chromium as well as a plain HTTP client. The same requests from a
  German residential address returned the full page — and returned it without
  JavaScript, because MediaMarkt server-renders everything. The browser is
  not what is being checked.
- **A 2Captcha key does nothing about that block.** The refusal is the shop's
  own branded error page under a 403, with no challenge on it. This scraper
  reports it as `blocked` rather than `captcha` precisely so that no solve is
  attempted or billed. The key is for the reCAPTCHA the site uses on account
  and checkout flows, and for `--fingerprint`.
- **Hub categories legitimately have no products.**
  `/de/category/notebooks-680.html` answers 200 with a real page and no grid.
  That is exit 4, not exit 3, and an empty page is never retried.

### The trap this parser exists to avoid

MediaMarkt renders **two** struck-through prices in nearly identical markup:
`mms-strike-price-type-rrp` is the manufacturer's recommended price and is a
genuine old price, while `mms-strike-price-type-lop` is the lowest price of
the preceding 30 days and is normally **below** the current one. Reading both
into `original_price` produces negative discounts on products that are not
discounted at all. Only the first goes there; the second has its own column.
Both are common — 22 and 14 nodes respectively across the captured pages.

### Fixed during the first live runs

Recorded because each was invisible to a reading of the code and only a real
run surfaced it.

- **`rating` was `0.0` on every unrated product.** MediaMarkt draws the star
  widget on every tile and shows "0 von 5 Sternen" when nobody has rated the
  product. Read literally, that filled the column on 84 of 84 rows of a test
  run — a column that looked complete and claimed a new release was rated
  zero stars. It is now `null`, with `review_count: 0` beside it.
- **A challenge marker was treated as a block on a page full of products.**
  The Scraping Browser API's auto-solve extension injects `cf-turnstile` into
  every page it loads, so the first live run exited 3 on a 1.8 MB category
  page holding the full catalogue. Extension-injected scripts are now
  stripped before markers are looked for, and a marker never blocks a page
  whose products have rendered.
- **Accented categories silently lost `--concurrency`.** MediaMarkt writes
  its own next-links percent-decoded (`.../kühlen-gefrieren-32.html`) while a
  pasted URL is encoded. Compared as raw strings the two disagreed, so every
  category with a non-ASCII name fell back to sequential fetching with
  nothing in the log to explain it.
- **A detail page's price could be read as its cents.** The price is split
  across three nodes (`24,` / `99` / `€`); reading the container's text gives
  "24, 99 €", out of which a general price pattern matches `99`. It is now
  reassembled from the named parts, scoped to the page's own price container
  — a detail page also carries a "similar products" carousel whose tiles have
  prices and strike prices of their own.
- **The fallback path returned no prices at all.** Each MediaMarkt tile links
  to its product twice (image and title), so a tile scope that stops at "more
  than one product link" never left the anchor. It now counts distinct
  article numbers.
- **A Playwright connection error printed the endpoint password** five times
  over, in the message and its call log. Credentials are now masked
  everywhere they can appear, with the host and port kept.
- **Two country sites silently lost their DOM-only columns.** `mediamarkt.pl`
  and `mediamarkt.lu` answer without a `www.` prefix — their own hreflang
  entries say so — while the parser rebuilt every product URL as
  `https://www.{host}{path}`. The reconstructed URL never matched the page's
  own, so the join between a structured row and its rendered tile failed on
  every row of those sites: `original_price` and `lowest_price_30d` empty,
  `price_source` always `jsonld`, and nothing in the log to say why.
  Measured on a live Polish listing before the fix: 12 rows, 12 priced, 0
  confirmed. URLs are now resolved against the page's own address, and the
  tile join is keyed on a form that ignores `www.`, percent-encoding and
  trailing tracking parameters.
- **A product title was read as the catalogue counter.** Polish writes "of"
  as a bare `z`, and the title "ELECTROLUX LVM8E08Z 44l" contains "8Z 44" —
  which a loose search read as "8 of 44", making a live listing report a
  catalogue of 44 against its own printed 85. The counter is its own element,
  so only a text node that IS the count is accepted, and the caller now
  passes the number of rows it actually parsed so the read is checked rather
  than scanned for.

### Removed

- The previous version's `--categories`, `--details`, `--output-dir`,
  `--delay-min` / `--delay-max` and hard-coded category map. The map was
  wrong — `smartphones` pointed at category 486, which is *Filme & Serien* —
  and a flag that silently multiplies a run's request count (`--details`) is
  now the explicit `--mode product`.
- `--proxy-host` / `--proxy-port` / `--proxy-user` / `--proxy-pass`.
  Credentials on a command line are readable by anything that can run `ps`.
  Use `.env` and `--proxy-file`.
- The GPL-3 licence file that the previous version shipped while its README
  claimed MIT. The repo is MIT, matching the rest of this family, and the
  README and the licence now agree.

[0.1.0]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.0
