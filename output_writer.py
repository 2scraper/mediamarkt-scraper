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

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The hostname a row came from. MediaMarkt is one platform across eleven
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


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Product) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT) -> dict:
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
    """
    return {
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


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Product) -> int:
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
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} products -> {out_prefix}.csv")
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
               mode: str = "listing", source: str = SOURCE_DEFAULT) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, Product)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows)))

    if not rows:
        # Nothing gathered at all: a challenge outranks "empty result",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
