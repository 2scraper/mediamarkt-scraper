# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) as closely
as a CLI toolkit can. In practice that means: **a patch release fixes things**
— it does not promise that every flag and every default is frozen. Where a
patch changes behaviour an existing user would notice, the release notes lead
with it, so nobody discovers it from a bill or from a diff.

---

## [0.1.2] — 2026-09-09

All ten supported country sites are now live-verified, each from a
residential exit in its own country. Doing that turned up one more silent
parsing bug and one site that does not belong in the list at all.

### Fixed

- **A discount badge was read as a price on the Turkish site.** Turkish puts
  the percent sign BEFORE its number and the currency symbol before its own —
  `-%10,34 ₺25.999,–` — so the pattern's trailing-symbol form matched
  "10,34 ₺": the badge's number wearing the next price's symbol. A 25,999 TRY
  air conditioner parsed as costing 10.34. It reached the output only as an
  unconfirmed row, because the structured price disagreed with the tile and
  the parser kept the structured one — the guard worked, the parse did not.
  Percentages are now stripped BEFORE matching, in both word orders (German
  writes `-16%`, Turkish `-%10,34`), because merely rejecting the match still
  consumed the currency symbol and lost the real price with it.

### Changed

- **`mediamarkt.lu` is no longer a supported host.** It is in MediaMarkt's
  own hreflang set and it is a real MediaMarkt shop, but it does not run on
  this platform: fetched from a Luxembourg exit it answers 200 with a full
  French storefront containing zero `/category/` paths, zero `/product/`
  paths, zero product cards, and JSON-LD carrying only `Organization` and
  `WebSite`. It is a Shopify store. Every selector here would find nothing,
  so a run would have reported an empty category rather than an unsupported
  site.
- **A refused host now says WHY** when the answer is more than "not ours".
  `mediamarkt.lu` and the Saturn brands each get their own reason instead of
  "is not a MediaMarkt site", which was false for all three and sent the
  reader looking for a typo.
- **The exit country has to match the site.** Measured rather than assumed
  this time: one German residential address was accepted by `.de`, `.es` and
  `.pl` and refused with 403 by the other seven — each of which then answered
  normally from an exit in its own country. v0.1.1 stated this as an
  assumption; it is now a result, and the README and TROUBLESHOOTING say so.
- `smoke_test.py`: 284 checks, up from 269. The new Turkish fixture carries a
  prefixed currency, a percent sign before its number and an instalment line
  in one price block.

### Verified

Ten of ten sites, 2026-09-09, each from a local residential exit: twelve
products a page with every row confirmed against its rendered tile, in EUR
(de, at, nl, be, es, it), CHF (ch), PLN (pl), HUF (hu) and TRY (tr).

---

## [0.1.1] — 2026-09-09

Two silent bugs, both found by live-verifying a SECOND country site rather
than by reading the code. Neither could show on `mediamarkt.de`, and neither
produces an error: rows, titles and prices stay correct throughout, because
they come from the structured data. Only the columns that depend on reading
the rendered page disappear.

### Fixed

- **`mediamarkt.pl` and `mediamarkt.lu` lost their DOM-only columns
  entirely.** Both answer without a `www.` prefix — their own hreflang
  entries say so — while the parser rebuilt every product URL as
  `https://www.{host}{path}`. The reconstructed address never matched the
  page's own, so the join between a structured row and its rendered tile
  failed on every row: `original_price` and `lowest_price_30d` always null,
  `price_source` always `jsonld`. Measured on a live Polish listing before
  the fix: 12 rows, 12 priced, **0 confirmed**; after: 12 of 12. URLs are now
  resolved against the page's own address, and the tile join ignores `www.`,
  percent-encoding and trailing tracking parameters.
- **A product title was read as the catalogue counter.** Polish writes "of"
  as a bare `z`, and the real title "ELECTROLUX LVM8E08Z 44l" contains
  "8Z 44" — read as "8 of 44", so a listing reported a catalogue of 44
  against its own printed 85. Only a text node that IS the count is accepted
  now, and the engines pass the row count they actually parsed so the read is
  checked rather than scanned for.

### Changed

- **Three country sites are now live-verified** — `.de`, `.es` and `.pl` —
  each returning 12 products a page at 100% DOM confirmation, in EUR, EUR and
  PLN. The README no longer claims only one.
- **Documented that a residential exit is necessary but not always
  sufficient.** One German address was accepted by `.de`, `.es` and `.pl` and
  refused with 403 by the other eight sites. An exit in the target country is
  the obvious answer and is stated as an assumption, because it was not
  tested for those eight.
- `smoke_test.py`: 269 checks, up from 255. The new Polish fixture is a
  second language, a non-euro currency and the non-www host in one, and it
  carries the decoy title beside a genuine counter so both regressions are
  pinned rather than described.

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

[0.1.2]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.2
[0.1.1]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.1
[0.1.0]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.0
