"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

Two modes, one row shape
------------------------
    --mode listing   category grids and search results -> Product
    --mode product   one /product/... detail page      -> Product, with the
                     trailing detail-only fields populated

Both modes yield the SAME class, because on MediaMarkt a detail page is not a
different kind of object from a tile — it is the same product described more
fully. So there is no second dataclass here (another repo in this family
needs one for reviews; this one does not), and `diff_runs.py` can compare a
listing run
against a product run without either side being an artefact.

`Product` keeps the family's field order exactly, with the MediaMarkt-specific
columns appended after `price_source`, so a consumer written against another
repo in this family still reads the first sixteen columns unchanged.

Everything below is row-class-agnostic: pass `row_cls` so an empty CSV still
gets the right header for the mode that produced it.
"""

import contextlib
import csv
import json
import os
import tempfile
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The hostname a row came from. MediaMarkt is one platform across ten
# country sites (plus MediaWorld in Italy, which is the same company and the
# same markup under a different brand), and which one produced a row is not
# derivable from the sku: the same article number exists on several sites at
# different prices and in different currencies. So `source` carries the
# actual hostname of the URL that was scraped ("mediamarkt.at"), and this is
# only the default for a row built without one.
SOURCE_DEFAULT = "mediamarkt.de"


@dataclass
class Product:
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    # MediaMarkt's article number. It is not published as a field in the
    # listing's JSON-LD, but it is the last number in every product URL
    # ("..._harry-potter-the-complete-collection-dvd-2920911.html" -> 2920911)
    # and it IS a field on a detail page, where the two agree. So a listing
    # row recovers it from the URL and a detail row reads it.
    sku: Optional[str] = None
    title: Optional[str] = None
    # Null on every listing row, and that is measured rather than missed:
    # MediaMarkt's tile markup carries no brand line, and the listing JSON-LD
    # publishes no `brand` key (checked on 6 captured category pages and 2
    # search pages, 96 tiles). The brand is the leading token of the title on
    # most tiles and NOT on all of them ("OK. OFK 411", "PLAION PICTURES"),
    # so splitting the title would produce a plausible column that is
    # sometimes wrong — worse than null. It IS populated by --mode product,
    # from the detail page's `brand.name`.
    brand: Optional[str] = None
    price: Optional[float] = None
    # No guessed default: a row whose currency could not be established says
    # None rather than claiming EUR. Four of the ten country sites quote
    # something else — PLN, CHF, HUF and TRY, all four confirmed on live
    # pages — so a defaulted "EUR" would be wrong on two fifths of the
    # platform.
    currency: Optional[str] = None
    # The manufacturer's recommended price (UVP / PVPR / adviesprijs), read
    # from the tile's `mms-strike-price-type-rrp` node. See the WARNING in
    # product_parser.py: MediaMarkt renders a SECOND strikethrough price that
    # is not this and must never be read into this column.
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    in_stock: Optional[bool] = None
    image_url: Optional[str] = None
    category: Optional[str] = None
    # Where `price` came from, because the same column can hold figures of
    # different confidence and nothing else says which:
    #   "jsonld"     — the page's own schema.org data, and no tile was found
    #                  to confirm it against.
    #   "jsonld+dom" — the structured price AND the rendered tile agree. The
    #                  trustworthy read, and the one the canary thresholds.
    #   "dom"        — the URL-pattern fallback ran; no structured data for
    #                  this row at all.
    # diff_runs.py reports a price change that comes with a price_source
    # change as `source_changed`, not `changed`: that says something about
    # our own two snapshots, not about MediaMarkt.
    price_source: Optional[str] = None

    # ---- MediaMarkt-specific, appended so the family prefix above is stable ----
    # Which listing page this row came from (1-based), and its position in
    # that page as the site ordered it. Without `page`, `position` is
    # ambiguous: it restarts at 1 on every page, so a row from page 3 would
    # claim the same position as one from page 1. Both null outside
    # --mode listing, where there is no page.
    page: Optional[int] = None
    position: Optional[int] = None
    # The lowest price MediaMarkt charged for this article in the preceding
    # 30 days, which EU price-indication law (the Omnibus directive) makes it
    # print beside a reduced price. It has its own column and NOT
    # `original_price` because it is not one: it is frequently LOWER than the
    # current price, so folding it in would produce negative discounts. See
    # the WARNING in product_parser.py — this is the single most dangerous
    # thing to get wrong on this site.
    lowest_price_30d: Optional[float] = None
    # ---- populated by --mode product only; null on a listing run ----
    # The EAN, from the detail page's `gtin13`. This is the field that makes
    # a MediaMarkt run joinable against another retailer's data, so it is
    # worth the extra request per product that --mode product costs.
    ean: Optional[str] = None
    description: Optional[str] = None
    images: Optional[List[str]] = None


# Row classes by --mode, so an engine maps its mode to a schema in one place.
# Both modes are Product here; the mapping exists so adding a mode later is a
# one-line change rather than a search for every place that assumed Product.
ROW_CLASS_BY_MODE = {"listing": Product, "product": Product}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py. Both of this repo's modes qualify.
UNIQUE_BY_SKU_MODES = ("listing", "product")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a stale or repeating next-page link then re-parses a page without
    duplicating its rows into the final output. MediaMarkt's own pagination
    does not repeat rows — three consecutive category pages captured on
    2026-09-09 shared 0 skus out of 36 — so this is a guard against a
    re-fetch, not against the site.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    Both of this repo's modes are one row per `sku`, so `key` is never
    overridden here — the parameter exists because the rest of the family
    shares this function and one of them needs it.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


# ===========================================================================
# Replacing a file without destroying the one that is already there
# ===========================================================================
@contextlib.contextmanager
def _atomic(path: str, newline: Optional[str] = None):
    """Write to a temporary file beside `path`, then rename over it.

    `open(path, "w")` truncates before a single byte is written, so a crash,
    a kill, a full disk or a Ctrl-C halfway through `json.dump` leaves a
    SHORTER file where a complete one was. That breaks exactly the promise
    `save()` keeps by refusing to overwrite good output with an empty
    result: the previous good run is destroyed by the ATTEMPT to replace it
    rather than by its outcome. Reproduced on a sibling repo before the fix
    — a 2,084-byte good `out.json` came back 0 bytes and invalid JSON.

    The sidecar is the part that matters most. `<out>.meta.json` is the file
    a consumer branches on, so a truncated one beside good rows reads as a
    broken run over data that is fine.

    Three details, each of which is the reason for a line here:

    * the temp file goes in the target's OWN directory, because `os.replace`
      is atomic only within one filesystem and a temp file under /tmp can be
      on another, where the rename degrades to an interruptible copy;
    * `fsync` before the rename is what makes the bytes durable rather than
      merely visible, so a power loss cannot leave a renamed empty file;
    * `newline` is passed through because the csv module requires
      `newline=""` and that requirement has to survive the move into the
      temp file, or every row gains a stray CR on Windows.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline=newline, dir=directory,
        prefix=os.path.basename(path) + ".", suffix=".tmp", delete=False)
    try:
        with handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        # `NamedTemporaryFile` creates at 0600 and `os.replace` keeps the
        # temp file's mode, so without this every output comes out readable
        # by its owner alone — a regression from `open(path, "w")`, which
        # honours the umask, and one that only appears once the write became
        # atomic. Measured across the family on 2026-10-08 by CALLING each
        # repo's sidecar writer: of 43 repos, 8 produce a 0600 sidecar, and
        # they are exactly the ones that took `_atomic` without this.
        #
        # An existing target keeps its OWN mode, because someone may have
        # tightened it on purpose and a save should not undo that. A new one
        # gets exactly what `open()` would have given it. Lifted from
        # hackernews-scraper, which fixed this first — the first version
        # written here used a flat `0o644 & ~umask`, which quietly narrows
        # 0664 to 0644 under umask 002 and clobbers a deliberately tightened
        # file.
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(handle.name, mode)
        os.replace(handle.name, path)
    except BaseException:
        # Including KeyboardInterrupt and SystemExit: an interrupted run
        # must not leave `out.json.ab12cd.tmp` behind beside the good file.
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


# A spreadsheet treats a cell beginning with one of these as a FORMULA
# rather than as text, and every string in these rows was written by
# the site or by a seller — a title, a category, a badge, a delivery note. `=HYPERLINK(...)`, `+cmd|...` and `@SUM(...)` are the classic
# shapes; the tab and the two newlines are here because some readers strip
# a leading one and then execute whatever followed it.
CSV_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")

# Excel, Google Sheets and LibreOffice all read a leading apostrophe as
# "the rest of this cell is text" and do not display it. A consumer parsing
# the file with the `csv` module DOES see it, which is why the count goes
# into the sidecar rather than staying silent.
CSV_FORMULA_ESCAPE = "'"


def _csv_escape(v: Any):
    """Neutralise a formula-shaped cell. Returns (value, was_escaped).

    Only STRINGS are touched. Escaping a number would turn `-5` into text
    and break every sum a consumer writes over the column, and this schema
    has numbers a consumer sums: `price`, `original_price`, `discount_pct`.

    CSV only, on purpose. The JSON output keeps the site's bytes exactly as
    served, so the two files deliberately differ, and `csv_cells_escaped`
    in the sidecar is what declares that divergence rather than leaving it
    to be discovered as a stray apostrophe.
    """
    if isinstance(v, str) and v.startswith(CSV_FORMULA_LEADS):
        return CSV_FORMULA_ESCAPE + v, True
    return v, False


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with _atomic(path) as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Product) -> int:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    escaped = 0
    with _atomic(path, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            row = {}
            for k, v in asdict(r).items():
                # AFTER the list serialiser, not before it. A list is not a
                # string, so `_csv_escape` would skip it — and the joined
                # cell it becomes can start with a formula character
                # ("@SUM(1) | Free Delivery"), which is the case a naive
                # ordering misses.
                value, was_escaped = _csv_escape(_csv_value(v))
                row[k] = value
                escaped += was_escaped
            writer.writerow(row)
    return escaped


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On MediaMarkt this code specifically does NOT cover a hub category — a URL
# like /de/category/tv-audio-202.html that answers 200 with a real page and
# no product grid, because it is a landing page of sub-categories rather than
# a listing. That is EXIT_NO_PRODUCTS: the request was served exactly as
# asked and simply has no products on it. Reporting it as blocked would send
# a user hunting for a proxy problem that does not exist.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
EXIT_PARTIAL = 6


# Exit code for a run that never GOT its pages: a navigation timeout, a dead
# or unauthenticated proxy, a DNS failure, or an edge answering with
# something that is not the page that was asked for.
#
# Distinct from EXIT_NO_PRODUCTS because those are opposite facts. Exit 4 is
# a statement about the CATALOGUE — "we asked, and the answer was nothing" —
# so handing it to a run that never reached the site tells a pipeline the
# listing is empty when nothing was read at all.
#
# 5 rather than a new number, and 5 rather than EXIT_PARTIAL:
#
#   * this family's contract already reserves 5 for a transport failure
#     (scraper_api_client has used it for a remote API error since it was
#     written), so this needs no new code and no per-repo table for a caller
#     driving more than one of these scrapers;
#   * EXIT_PARTIAL (6) means "some rows were gathered and the output is
#     incomplete". A run holding nothing writes no output at all, so a
#     consumer that reads the file on a 6 finds either nothing or the
#     PREVIOUS run's good data, which `save` deliberately does not
#     overwrite. Exit 5 promises no file.
#
# Deliberately NOT applied when rows WERE gathered: a timeout on page 7 of
# 10 is a partial run (exit 6, output written), which is already right. This
# decides only what a run holding nothing reports.
EXIT_FETCH_FAILED = 5


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with _atomic(path) as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are recorded because neither is implied by the repo:
    the same output prefix can hold a listing run or a product run, from any
    of ten country sites in five currencies, and a consumer that guesses
    wrong compares prices that were never comparable. diff_runs.py refuses a
    pair whose modes or sources differ.

    `pages_failed` lists the pages that did not yield data, by number.
    `pages_completed` alone was enough only while pages were fetched strictly
    in order, where "3 of 10 completed" could only mean 1-2-3: a count is not
    a description once pages can be fetched independently and page 3 can fail
    while 4 and 5 succeed. Recording the numbers keeps the sidecar honest
    about WHICH part of the catalogue is missing, not just how much.

    `extra` carries facts about the run that are not about any single row,
    such as `csv_cells_escaped`. It is merged at the top level, and the run
    fields above win a name collision, so a caller cannot overwrite `status`.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Product,
         stats: Optional[dict] = None) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 products -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 products — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        escaped = write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.csv")
        # Reported through `stats` rather than as a return value, because
        # this function's return IS the exit code. The caller threads it into
        # the sidecar, where a consumer comparing the CSV against the JSON
        # can see that the two diverge and by how much.
        if stats is not None:
            stats["csv_cells_escaped"] = escaped
        if escaped:
            print(f"[!] {escaped} CSV cell(s) began with a formula character "
                  f"and were prefixed with {CSV_FORMULA_ESCAPE!r} so a "
                  f"spreadsheet reads them as text. The JSON output is "
                  f"unchanged — see csv_cells_escaped in the sidecar.")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" is kept for the
# engines that still stop on a missing next-link: the first is a property of
# the DATA (a page contributed nothing not already seen, so the listing is
# over), while the second is a property of a CSS SELECTOR and is therefore
# the weaker signal — a renamed attribute looks identical to a short
# catalogue. MediaMarkt does publish `link[rel=next]` in the document head
# AND numbers its pages with `?page=N`, and the two agree, so all three
# layers are real here. See playwright_scraper.py.
#
# "single_page_mode" is complete by construction: --mode product reads one
# page because one page is all there is.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    # Completeness is decided by the reason AND by the evidence. A named
    # list of stop reasons cannot cover a failure recorded somewhere else,
    # and `pages_failed` is somewhere else: a run whose loop ended for a
    # COMPLETE reason while individual pages failed reported exit 0 and
    # `status: complete` with a non-empty `pages_failed` in the same
    # sidecar — a file that contradicts itself, and a pipeline branching
    # on `status` reading a short run as a whole one.
    #
    # Found by a third-party audit of a sibling repo and measured across
    # the family by CALLING each `finish_run` rather than grepping for the
    # fix: 28 of 32 repos behaved this way. Same shape as the exit-code
    # unification this file already carries — a rule keyed on a list of
    # names has a hole for every name nobody added to it.
    complete = stop_reason in COMPLETE_STOP_REASONS and not pages_failed
    row_cls = ROW_CLASS_BY_MODE.get(mode, Product)
    write_stats: dict = {}
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls,
              stats=write_stats)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        # A zero-row run the user opted into with --allow-empty, which ended
        # for a COMPLETE reason with no failed page, is an empty listing read
        # in full: `complete`, not `failed`. Only a zero-row run that did not
        # finish is `failed`.
        status = "complete" if complete else (
            "partial" if rows else "failed")
        # UNDER the caller's `extra`, never over it: a collision would be
        # housekeeping silently dropping something the engine measured.
        if write_stats:
            extra = {**write_stats, **(extra or {})}
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all, and WHY decides the code. The three
        # outcomes are different facts and a pipeline branches on them
        # (blocked is not empty is not "never reached"):
        #
        #   blocked            something stood between the run and the content
        #   did not complete   we never got the pages — a dead proxy, a load
        #                      timeout, an edge serving something else
        #   completed          we asked, and the answer was nothing
        #
        # Keyed on `not complete` rather than on a list of stop reasons, on
        # purpose: a list cannot cover a reason nobody has added to it yet,
        # so a new one falls silently through to "the catalogue is empty" —
        # which is the defect this branch exists to prevent.
        if blocked:
            return EXIT_BLOCKED
        if not complete:
            print(f"[!] Nothing was gathered and the run did not finish "
                  f"({stop_reason}) — exit {EXIT_FETCH_FAILED}, NOT an empty "
                  f"result (exit {EXIT_NO_PRODUCTS}). Nothing can be "
                  f"concluded about the catalogue from this run.")
            return EXIT_FETCH_FAILED
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
