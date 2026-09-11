# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) as closely
as a CLI toolkit can. In practice that means: **a patch release fixes things**
— it does not promise that every flag and every default is frozen. Where a
patch changes behaviour an existing user would notice, the release notes lead
with it, so nobody discovers it from a bill or from a diff.

---

## [0.1.8] — 2026-09-11

### Fixed

- **`fingerprint_client.py` could not read the key from `.env`.** `--key`
  defaulted to `os.environ.get("TWOCAPTCHA_KEY")` and only that, so a key put
  in `.env` — exactly as §3, the README and `.env.example` instruct — worked
  for every engine and failed HERE with "No API key". A documented mechanism
  not applied on one path, which is the shape of half the defects §16 lists.

  It now reads through `env_config.env_value`, calling `load_env()` itself
  because this is a standalone entry point that no engine has necessarily run
  first. Going through the loader rather than `os.environ` is measured rather
  than stylistic: with `TWOCAPTCHA_KEY=your_2captcha_api_key_here` exported,
  the old path sent the placeholder to the API and reported "Fingerprint API
  rejected the key (401) — note this is a separate subscription", sending the
  reader off to check a subscription they never needed; the loader says
  "still set to the placeholder from .env.example" instead.

  Found on a sibling repo's first live `--fingerprint` run, then checked
  across the family before patching, per §16: five repos had it and one had
  already fixed it. Pinned by a check verified to fail on the old code —
  including that the help string does not interpolate its default, which is
  one substring away from printing a live credential to anyone who types
  `--help`.

---

## [0.1.7] — 2026-09-11

### Fixed

- **`--fingerprint` dropped `deviceScaleFactor`, so the identity
  contradicted itself.** `playwright_context_kwargs` mapped the user agent,
  the locale, the timezone and the screen onto the browser context and
  ignored the scale factor the fingerprint API returns beside them. Measured
  2026-09-11 against the live API and a live browser: a fingerprint stating
  `deviceScaleFactor: 1.25` produced a browser reporting
  `window.devicePixelRatio === 1` — the paid identity saying one thing and
  the browser another, on every run, silently, on an axis any fingerprinter
  reads for free. Playwright takes it as its own context option, so the fix
  is to pass it; verified in a live browser both ways and pinned in the
  offline suite.

  Found while auditing a new sibling repo against the family notes. All five
  repos in this family had it.

---

## [0.1.6] — 2026-09-09

The canary's first run with a real proxy secret crashed, and printed a live
proxy login and password into its own CI log doing it. Three defects, all in
`proxy_pool.py`, all triggered by one wrong value.

### Fixed

- **A malformed proxy URL crashed the run (exit 1) instead of being refused
  (exit 2).** `parse_proxy_line` validated the scheme and the host but never
  the port — and `urlparse` computes a port lazily, raising `ValueError` only
  when something finally asks. So a bad entry sailed through validation and
  blew up several calls later inside `to_playwright`, as an uncaught
  traceback with no message saying what was wrong.
  The value that caused it is the mistake a new user makes: a line from a
  proxy LIST FILE (`scheme://host:port:login:password`) pasted where a proxy
  URL (`http://login:password@host:port`) belongs. The refusal now says
  exactly that.
- **`mask()` raised on the values that most needed masking.** It read
  `parsed.port`, so the one function standing between a password and a log
  blew up on a malformed URL — and the caller printed the raw string
  instead. That is how the credential reached the CI log. `mask()` is now
  total: it never raises, and anything it cannot take apart is redacted whole
  rather than echoed. Every `ProxyError` message now reports `mask(line)`.
- **A pool of N identical entries claimed to be N exits.** A copied proxy
  list is often one address repeated; the pool reported "exit 2/50" on every
  rotation while every one of them left from the same place, and the
  single-exit warning never fired because it counted entries. Duplicates are
  now dropped, order preserved, and the collapse is logged rather than done
  silently.

### Removed

- A test asserting that `"http://host:port:login:pass"` "is understood". It
  checked only that `parse_proxy_line` did not reject the line — which it
  did not, returning it unchanged — so the check passed while the value was
  never usable. A test that asserts a function did not complain is not a test
  that its answer was right. Replaced with one that pins the refusal, the
  message, and the absence of the credential from it.

`smoke_test.py`: 339 checks, up from 330.

---

## [0.1.5] — 2026-09-09

`--fingerprint` was applying almost none of the fingerprint. Found by reading
what the API actually returns, after a key rotation made it worth re-running
the path.

### Fixed

- **`--fingerprint` never set a user agent.** The UA was read from
  `userAgent.value`, a key the API returns in NEITHER response format — it is
  `userAgent.userAgent` in `chromium` and `data.ua` in `raw`. So the flag
  silently left the browser on its own UA while replacing the screen and the
  locale around it: a German fingerprint's identity wearing a local
  Chromium's user agent, which is exactly the mismatch the flag exists to
  prevent. Nothing errored, and the success log printed an empty string
  where the UA should have been.
- **The locale contradicted the fingerprint.** It was built as
  `f"en-{country}"` — "en-DE" for a German fingerprint. The response carries
  `intl.contentLocale`, which for that fingerprint is "de-DE". An
  English-speaking visitor in Germany is possible; it is not what the
  fingerprint describes, and a locale disagreeing with the rest of the
  identity is a signal in itself.
- **The timezone was not applied at all**, though the response states it
  (`intl.timeZone`) and Playwright can set it. A fingerprint claiming
  Europe/Berlin while the browser reports UTC contradicts itself in a way any
  script can read.
- **The window size was guessed** (`screen height - 120`) when the response
  states its own `outerWidth`/`outerHeight`.

Verified against a live browser: with a German Windows fingerprint applied,
the page now reports that UA, `de-DE`, `Europe/Berlin` and a window smaller
than the screen — all matching the fingerprint rather than the host.

### Added

- Tests for both of the above, plus the key-redaction added in 0.1.4, so
  neither can regress. The fingerprint fixture is cut from a real
  `format=chromium` response.
- A check that every kwarg `playwright_context_kwargs` produces is one
  `new_context` accepts — an unknown key is a TypeError at launch, on the
  paid path, at runtime.

`smoke_test.py`: 330 checks, up from 311.

---

## [0.1.4] — 2026-09-09

The paths that needed a 2Captcha key, run for the first time. Both worked;
both had a defect the first real call exposed, and one of them was a
credential leak.

### Fixed

- **The API key was printed to the terminal.** `fingerprint_client.py` sends
  the key as a QUERY parameter, and `requests` puts the full URL — query
  string included — into the text of `HTTPError` and of every connection
  error. A 400 from the fingerprint endpoint therefore printed a live key.
  Every error surfaced from that module and from `captcha_solver.py`'s v1
  polling call (the other place the key rides in a URL) is now redacted
  before it is raised or logged; the endpoint and status survive, because
  which call failed is the useful half and is not the secret.
- **`scraper_api_client.py` reported a block as an empty result.** It threw
  away the upstream HTTP status and looked only for a challenge marker — and
  MediaMarkt's 403 page carries none — so a refused request came back exit 4
  ("zero products") instead of exit 3 ("blocked"). A pipeline branching on
  the exit code would have read a block as an empty category. It now
  classifies the upstream response with the same `detect_page_state` the
  three browser engines reach through `page_flow`.
- **The documented `--tags` example never worked.** `"Windows,Chrome,Desktop"`
  returns HTTP 400 from the fingerprint API every time. Measured against the
  live endpoint: `tags` takes ONE OS-family value — `Windows`,
  `Microsoft Windows` and `Android` are accepted; `Chrome`, `Desktop`,
  `Mobile` and `Unknown` are rejected, and no combination is accepted with
  any separator. The plural name and a fingerprint's own multi-valued
  `data.tags` are what make the list form look plausible. The help text now
  says which values work, and a 400 names the likely cause.

### Verified

- **`--fingerprint`, live**: fetched, cached and applied to the browser
  context (fingerprint 3088631, DE). MediaMarkt still answered 403 from a
  datacentre address, which is the point — the fingerprint is not what that
  check is about.
- **`scraper_api_client.py`, live**, and the result is worth knowing:

      without --cdp-url   upstream 403,    13,922 bytes, 0 products -> exit 3
      with    --cdp-url   upstream 200, 1,693,678 bytes, 12 products -> exit 0
                          12/12 confirmed against a rendered tile, EUR

  The Scraper API's own exit is refused by this site like every other
  datacentre address, so `--cdp-url` is not optional here. At $0.0005 a task
  it is the cheapest way to read this site once a residential session is in
  the path.
- **The concurrent dispatch machinery**, which no live run in this
  environment can reach — page 1 is always fetched alone and decides whether
  the rest may be addressed, so a blocked page 1 means the workers never
  start. Now driven directly with the browser stubbed out: every queued page
  fetched exactly once across workers, outcomes restorable to page order, the
  end-of-listing event stopping dispatch (4 fetches against 49 queued pages),
  unattempted pages reported rather than counted as failed, and a worker that
  raises neither hanging the run nor losing its siblings' pages.
- `--concurrency` is correctly refused with `--cdp-endpoint`, and
  `--fingerprint` correctly ignored with it.

### Still not run

A live `--concurrency` fan-out and `--proxy-file` rotation against the real
site. Not a credential problem: Chromium cannot reach an HTTP proxy from the
environment this was built in at all (a raw CONNECT to the proxy port hangs
while `requests` through the same proxy succeeds), so no proxy would change
it. The decision logic and the dispatch machinery are both covered offline;
what remains unverified is real browsers on real exits.

`smoke_test.py`: 311 checks, up from 301.

---

## [0.1.3] — 2026-09-09

Three defects found by running the things nothing had run yet: the second
engine against a live page, and the Docker image's file list against the
entrypoint's imports. All three were invisible to a green CI.

### Fixed

- **The pyppeteer engine died with `NameError` on its first real page.**
  `detect_page_state` was called on a line reached only while fetching, after
  the import of that name had been removed in favour of `page_flow.classify`.
  The module imported cleanly, `--help` worked, `compileall` passed, the
  whole offline suite passed and CI was green — byte-compiling proves a file
  parses, not that its names resolve, and the paths where they do not are
  exactly the ones an offline suite never executes. `smoke_test.py` now walks
  every module's AST for names that are never imported, defined or assigned.
- **The Docker image was broken on every invocation, `--help` included.** The
  Dockerfile COPYs an explicit list of modules — right, so the image does not
  carry the test suite or a stray `.env` — and the list had fallen behind:
  `proxy_pool.py` was missing while `playwright_scraper.py` imports it at
  module level. Nothing in the repo would have noticed, because CI never
  builds the image. `smoke_test.py` now checks the COPY list against the
  entrypoint's transitive imports, which needs no Docker to run.
- A stale `--out /out/headphones` example in the Dockerfile's header,
  inherited from a sibling repo.

### Verified

- **pyppeteer live**, over the Scraping Browser: 24 rows across 2 pages, 24
  of 24 confirmed against a rendered tile, and the same 24 skus the
  Playwright engine returned for the same category. The one price that
  differed between the two runs had genuinely changed on the site in the
  eighty minutes between them (119.99 -> 125.00, confirmed by re-fetching the
  product), so the engines agree.
- **Selenium's two documented refusals**: a credentialled `--cdp-endpoint`
  exits 2 with the credential masked, and a `user:pass` proxy is stripped
  without the password reaching the output.
- **The canary workflow, dispatched manually for the first time.** With no
  `MEDIAMARKT_PROXY` secret it takes the skip path and completes green in 12
  seconds with a notice explaining why — which is what it was designed to do
  and had never been observed doing.
- **The canary's own assertion block**, extracted from the workflow and run
  against a real 36-row output: passes.
- `pytest` locally, as well as through CI.

`smoke_test.py`: 301 checks, up from 284.

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

[0.1.8]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.8
[0.1.7]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.7
[0.1.6]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.6
[0.1.5]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.5
[0.1.4]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.4
[0.1.3]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.3
[0.1.2]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.2
[0.1.1]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.1
[0.1.0]: https://github.com/2scraper/mediamarkt-scraper/releases/tag/v0.1.0
