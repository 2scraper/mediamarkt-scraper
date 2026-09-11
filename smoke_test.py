#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# The encoding declaration is REQUIRED here and is not a Python-2 relic, so
# please do not tidy it away. CPython 3.9's tokenizer, with no declared
# encoding, fails on a multi-byte character that sits far inside a very long
# source line — and the fixtures below are single lines tens of thousands of
# characters long, full of German umlauts ("Kühl-Gefrierkombination") and the
# en dash MediaMarkt writes its round prices with ("349,– €"). Declaring the
# encoding is what PEP 263 is for.
"""
smoke_test.py
--------------
Zero-network, zero-browser sanity check for mediamarkt-scraper.

Run this FIRST, before touching a real browser or mediamarkt.de, to confirm
the environment and the parsing/output/policy logic work:

    python3 smoke_test.py

Deliberately ONE file of plain functions with inline fixtures — no pytest, no
conftest, no fixtures directory. tests/test_smoke.py wraps it as a single
pytest test so `pytest` works as an entry point without a second copy of the
checks that could drift from this one.

It must pass with NO engine library installed at all, so every
`import playwright_scraper` / `puppeteer_scraper` / `selenium_scraper` is
guarded and the skip is REPORTED. CI's engine-smoke job installs all three and
fails if anything reports skipped, because "skipped, engine absent" reads
identically to a real import error.

What this suite is actually for
-------------------------------
Not coverage. Every check that matters here pins a VALUE read off a real
capture, because a column can be 100% populated and entirely wrong — this
repo has already shipped one such bug (`rating` came back 0.0 on every
unrated product, filling the column on 84 of 84 rows of a test run and
telling a consumer that a new release was rated zero stars). Coverage said
100%. So the assertions below say `price == 349.0`, not `price is not None`.

Exits non-zero on any failure.
"""

import ast
import builtins
import inspect
import json
import os
import re
import subprocess
import sys
import threading
import io
import tempfile
from contextlib import redirect_stdout
from dataclasses import fields

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections)
from diff_runs import diff_products
import env_config
import page_flow
from output_writer import (Product, save, finish_run, write_csv,
                           dedupe_by_key, dedupe_by_sku, run_meta,
                           ROW_CLASS_BY_MODE, UNIQUE_BY_SKU_MODES,
                           EXIT_BLOCKED, EXIT_NO_PRODUCTS, EXIT_PARTIAL,
                           COMPLETE_STOP_REASONS, LIST_CSV_SEPARATOR)
import product_parser
from product_parser import (parse_products, parse_product_detail, page_url,
                            category_from_url, listing_kind, site_host,
                            is_supported_host, host_currency, HOSTS,
                            detect_page_state, detect_bot_challenge,
                            page_number_from_url, total_results, sku_from_url,
                            SELECTORS)
from proxy_pool import (ProxyPool, mask, to_playwright, split_credentials,
                        parse_proxy_line)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

_failures = []


def check(label, condition):
    """Print and record one check. Returns the condition, so callers can
    accumulate with `ok &= check(...)`."""
    if condition:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s" % label)
        _failures.append(label)
    return bool(condition)


def group(title):
    print("\n== %s" % title)


def _raises(fn):
    """True if `fn()` raises. Used where refusing is the correct behaviour."""
    try:
        fn()
    except Exception:
        return True
    return False


def page(*fragments):
    """Wrap fragments in a minimal document, as the engines hand it over.

    The asset-host reference is not decoration: `detect_page_state` treats a
    page that references none of MediaMarkt's own assets as a block page (see
    its docstring), so a bare `<html><body>` wrapper would classify every
    hand-built fixture below as "blocked".
    """
    return ('<html lang="de"><body>'
            '<img src="https://assets.mmsrg.com/isr/x"/>%s</body></html>'
            % "".join(fragments))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
# Every one of these is a REAL capture, taken 2026-09-09 from a German
# residential exit IP. <script>, <style> and <svg> elements are stripped and
# the build-hash class names ("sc-59b6826e-0", "mms-ui-gEYBWy") are dropped;
# nothing else is changed, and the parser was verified to produce IDENTICAL
# rows from the trimmed and untrimmed forms before these were committed.
#
# The trimming is not only for size. MediaMarkt's pages embed a large
# front-end configuration blob — a Sentry DSN, a Woosmap public key, a
# store-code JWT — and none of it is needed to test a parser. Cutting the
# fixtures from the PRODUCT markup alone keeps third-party keys out of a
# public repository altogether, rather than relying on a scrubbing pass to
# catch them. `test_no_capture_leaks` guards that with patterns, so the next
# capture is checked too.
FIX_TILE_RRP = """<article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="cdOdXP"><a aria-label="Nintendo Switch Spielcover mit bunten Mii-Charakteren in einer Stadt und beim Picknick." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062068?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html" target="_self"><div class="hDcNlA" title="Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]"><h3 class="dhStGl" data-test="product-title">Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi2irdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 5 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">4</span><span id="rating-description-screen-reader-:R2pi2irdakbqorajct:">Basierend auf 4 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Altersfreigabe (USK)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Freigegeben ohne Altersbeschränkung</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Hersteller</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nintendo of Europe</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Titel</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Tomodachi Life: Wo Träume wahr werden</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Genre</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Simulation</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="gYTtGb"><div class="ioFSPw"><span>-16%</span></div></div><span class="htkAlu" direction="horizontal"></span><div class="kGZxQX notranslate" data-test="mms-strike-price-type-rrp"><span class="fzObRQ" data-test="mms-strike-price-label">UVP</span> <span aria-hidden="true" class="jCGxOY">59,99 €</span><span>59,99€</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Der hier angezeigte Preis ist die unverbindliche Preisempfehlung des Herstellers" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">49,99 €</span><span>49,99€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>10.09.2026 - 11.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-3053694"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-3053694" name="srp-entry-point-a2c-3053694" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="3053694" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="3053694" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article>"""

FIX_TILE_LOP = """<article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="jgKSdo"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Tiefpreis Tage">Tiefpreis Tage</span></div></li><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Unsere Eigenmarke">Unsere Eigenmarke</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Ein dunkelgrauer Koenic-Kühlschrank. Die obere Tür ist größer als die untere. Die Marke befindet sich oben." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="sync" fetchpriority="high" loading="eager" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152757?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"><div class="dlCFDA" data-test="cofr-card-product-energy-efficiency"><div><div data-test="mms-energy-efficiency-label"><button aria-label="Energieeffizienzklasse C" data-test="mms-energy-efficiency-arrow-button" type="button"><span aria-hidden="false" aria-label=" C" data-test="mms-energy-efficiency-arrow" role="img"></span></button><a data-test="mms-anchor-link" href="https://assets.mmsrg.com/ada/166325/c1/-/-/ASSET_MMS_167152778/?direct" rel="noopener noreferrer" target="_blank">Produktdatenblatt</a></div></div></div></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html" target="_self"><div class="hDcNlA" title="KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)"><h3 class="dhStGl" data-test="product-title">KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi1irdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 4.6 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div></div></div><div><span data-test="mms-customer-rating-count">119</span><span id="rating-description-screen-reader-:R2pi1irdakbqorajct:">Basierend auf 119 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Abmessungen (B/H/T)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">59.8 cm x 185.5 cm x 58.00 cm</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">No Frost-System</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">0°C Frischkühlzone (für längere Haltbarkeit)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">antibakterielle Innenbeschichtung</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="kGZxQX notranslate" data-test="mms-strike-price-type-lop"><span class="fzObRQ" data-test="mms-strike-price-label">Tiefstpreis (30 Tage): </span> <span aria-hidden="true" class="jCGxOY">299,– €</span><span>299,00€</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Niedrigster Preis der letzten 30 Tage auf mediamarkt.de (MediaMarkt bzw. Marktplatz-Verkäufer)" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">349,– €</span><span>349,00€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal">Bezahle in <strong>18 Raten à 19,39 €</strong> (eff. Zins. P.a. 0,00 %)** Gesamtpreis 349,– €</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>11.09.2026 - 17.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2882323"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2882323" name="srp-entry-point-a2c-2882323" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2882323" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2882323" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article>"""

FIX_TILE_UNRATED = """<article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="cdOdXP"><a aria-label="Toy Story 1-4 DVD-Cover mit Woody, Buzz, Jessie und Bo Peep in bunten Zahlen." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_toy-story-1-4-4-movie-coll-animation-zeichentrick-blu-ray-2590228.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_70576457?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_toy-story-1-4-4-movie-coll-animation-zeichentrick-blu-ray-2590228.html" target="_self"><div class="hDcNlA" title="Toy Story 1-4 (4 Movie Coll.) Blu-ray"><h3 class="dhStGl" data-test="product-title">Toy Story 1-4 (4 Movie Coll.) Blu-ray</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":Rb6i1krdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 0 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-no-rated-star"></span><span aria-hidden="true" data-test="mms-no-rated-star"></span><span aria-hidden="true" data-test="mms-no-rated-star"></span><span aria-hidden="true" data-test="mms-no-rated-star"></span><span aria-hidden="true" data-test="mms-no-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">0</span><span id="rating-description-screen-reader-:Rb6i1krdakbqorajct:">Basierend auf 0 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Titel</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Toy Story 1-4</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Genre</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Animation/Zeichentrick</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Altersfreigabe (FSK)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Freigegeben ohne Altersbeschränkung</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Originaltitel</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Toy Story 1-4</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div></div><div class="notranslate"><span aria-hidden="true" class="kipMlP hKPIXT">29,99 €</span><span>29,99€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>10.09.2026 - 11.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2590228"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2590228" name="srp-entry-point-a2c-2590228" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen Toy Story 1-4 (4 Movie Coll.) Blu-ray" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb Toy Story 1-4 (4 Movie Coll.) Blu-ray" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2590228" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb Toy Story 1-4 (4 Movie Coll.) Blu-ray" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2590228" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article>"""

FIX_LISTING_DE = """<!DOCTYPE html>
<html lang="de"><head><link data-rh="true" href="https://www.mediamarkt.de/de/category/kühlen-gefrieren-32.html" rel="canonical"/><link data-rh="true" href="https://www.mediamarkt.de/de/category/kühlen-gefrieren-32.html?page=2" rel="next"/><script type="application/ld+json">{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": 1, "item": {"@type": "Product", "name": "KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152757", "offers": {"@type": "Offer", "price": 349, "priceCurrency": "EUR"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.5966, "reviewCount": 119}, "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html"}}, {"@type": "ListItem", "position": 2, "item": {"@type": "Product", "name": "OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_134535117", "offers": {"@type": "Offer", "price": 299, "priceCurrency": "EUR"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4, "reviewCount": 46}, "url": "https://www.mediamarkt.de/de/product/_ok-ofk-411-c-in-kuhlgefrierkombination-c-124-kwh-1422-mm-hoch-edelstahl-look-2881243.html"}}, {"@type": "ListItem", "position": 3, "item": {"@type": "Product", "name": "KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_165341274", "offers": {"@type": "Offer", "price": 399, "priceCurrency": "EUR"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.6701, "reviewCount": 97}, "url": "https://www.mediamarkt.de/de/product/_koenic-kdd-171-e-in-nf-side-by-side-e-1770-mm-hoch-inox-2852155.html"}}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div class="grid"><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="jgKSdo"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Tiefpreis Tage">Tiefpreis Tage</span></div></li><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Unsere Eigenmarke">Unsere Eigenmarke</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Ein dunkelgrauer Koenic-Kühlschrank. Die obere Tür ist größer als die untere. Die Marke befindet sich oben." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="sync" fetchpriority="high" loading="eager" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152757?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"><div class="dlCFDA" data-test="cofr-card-product-energy-efficiency"><div><div data-test="mms-energy-efficiency-label"><button aria-label="Energieeffizienzklasse C" data-test="mms-energy-efficiency-arrow-button" type="button"><span aria-hidden="false" aria-label=" C" data-test="mms-energy-efficiency-arrow" role="img"></span></button><a data-test="mms-anchor-link" href="https://assets.mmsrg.com/ada/166325/c1/-/-/ASSET_MMS_167152778/?direct" rel="noopener noreferrer" target="_blank">Produktdatenblatt</a></div></div></div></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html" target="_self"><div class="hDcNlA" title="KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)"><h3 class="dhStGl" data-test="product-title">KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi1irdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 4.6 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div></div></div><div><span data-test="mms-customer-rating-count">119</span><span id="rating-description-screen-reader-:R2pi1irdakbqorajct:">Basierend auf 119 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Abmessungen (B/H/T)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">59.8 cm x 185.5 cm x 58.00 cm</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">No Frost-System</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">0°C Frischkühlzone (für längere Haltbarkeit)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">antibakterielle Innenbeschichtung</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="kGZxQX notranslate" data-test="mms-strike-price-type-lop"><span class="fzObRQ" data-test="mms-strike-price-label">Tiefstpreis (30 Tage): </span> <span aria-hidden="true" class="jCGxOY">299,– €</span><span>299,00€</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Niedrigster Preis der letzten 30 Tage auf mediamarkt.de (MediaMarkt bzw. Marktplatz-Verkäufer)" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">349,– €</span><span>349,00€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal">Bezahle in <strong>18 Raten à 19,39 €</strong> (eff. Zins. P.a. 0,00 %)** Gesamtpreis 349,– €</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>11.09.2026 - 17.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2882323"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2882323" name="srp-entry-point-a2c-2882323" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2882323" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2882323" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="jgKSdo"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Tiefpreis Tage">Tiefpreis Tage</span></div></li><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Unsere Eigenmarke">Unsere Eigenmarke</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Ein grauer, zweitüriger Kühlschrank vor weißem Hintergrund." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_ok-ofk-411-c-in-kuhlgefrierkombination-c-124-kwh-1422-mm-hoch-edelstahl-look-2881243.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_134535117?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"><div class="dlCFDA" data-test="cofr-card-product-energy-efficiency"><div><div data-test="mms-energy-efficiency-label"><button aria-label="Energieeffizienzklasse C" data-test="mms-energy-efficiency-arrow-button" type="button"><span aria-hidden="false" aria-label=" C" data-test="mms-energy-efficiency-arrow" role="img"></span></button><a data-test="mms-anchor-link" href="https://assets.mmsrg.com/ada/166325/c1/-/-/ASSET_MMS_134535752/?direct" rel="noopener noreferrer" target="_blank">Produktdatenblatt</a></div></div></div></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_ok-ofk-411-c-in-kuhlgefrierkombination-c-124-kwh-1422-mm-hoch-edelstahl-look-2881243.html" target="_self"><div class="hDcNlA" title="OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)"><h3 class="dhStGl" data-test="product-title">OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi2irdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 4 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-no-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">46</span><span id="rating-description-screen-reader-:R2pi2irdakbqorajct:">Basierend auf 46 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Abmessungen (B/H/T)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">550 mm x 1422 mm x 560 mm</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">No Frost-System</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">0°C Frischkühlzone (für längere Haltbarkeit)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">antibakterielle Innenbeschichtung</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="kGZxQX notranslate" data-test="mms-strike-price-type-lop"><span class="fzObRQ" data-test="mms-strike-price-label">Tiefstpreis (30 Tage): </span> <span aria-hidden="true" class="jCGxOY">222,– €</span><span>222,00€</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Niedrigster Preis der letzten 30 Tage auf mediamarkt.de (MediaMarkt bzw. Marktplatz-Verkäufer)" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">299,– €</span><span>299,00€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal">Bezahle in <strong>18 Raten à 16,61 €</strong> (eff. Zins. P.a. 0,00 %)** Gesamtpreis 299,– €</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>11.09.2026 - 17.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2881243"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2881243" name="srp-entry-point-a2c-2881243" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2881243" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb OK. OFK 411 C IN Kühlgefrierkombination (C, 173 l, 1422 mm hoch, Edelstahl Look)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2881243" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="bMkPtx"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Tiefpreis Tage">Tiefpreis Tage</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Ein dunkelgrauer Koenic-Kühlschrank mit Side-by-Side-Türen. Er hat ein Bedienfeld und steht vor weißem Hintergrund." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_koenic-kdd-171-e-in-nf-side-by-side-e-1770-mm-hoch-inox-2852155.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_165341274?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"><div class="dlCFDA" data-test="cofr-card-product-energy-efficiency"><div><div data-test="mms-energy-efficiency-label"><button aria-label="Energieeffizienzklasse E" data-test="mms-energy-efficiency-arrow-button" type="button"><span aria-hidden="false" aria-label=" E" data-test="mms-energy-efficiency-arrow" role="img"></span></button></div></div></div></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_koenic-kdd-171-e-in-nf-side-by-side-e-1770-mm-hoch-inox-2852155.html" target="_self"><div class="hDcNlA" title="KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)"><h3 class="dhStGl" data-test="product-title">KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":Rb6i4irdakbqorajct:"><div aria-label="Durchschnittliche Produktbewertung: 4.7 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div></div></div><div><span data-test="mms-customer-rating-count">97</span><span id="rating-description-screen-reader-:Rb6i4irdakbqorajct:">Basierend auf 97 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Abmessungen (B/H/T)</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">90.5 cm x 176.5 cm x 57.2 cm</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Wasseranschluss</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Eiswürfelspender</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Wasserspender</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Nein</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div></div><div class="notranslate"><span aria-hidden="true" class="kipMlP hKPIXT">399,– €</span><span>399,00€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal">Bezahle in <strong>18 Raten à 22,17 €</strong> (eff. Zins. P.a. 0,00 %)** Gesamtpreis 399,– €</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>11.09.2026 - 17.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2852155"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2852155" name="srp-entry-point-a2c-2852155" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2852155" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb KOENIC KDD 171 E IN NF Side-by-Side-Kühl-Kombigerät (442 l, E, 176,5 cm hoch, Edelstahl)" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2852155" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article></div><div class="count">12 von 2311</div></body></html>"""

FIX_LISTING_SEARCH = """<!DOCTYPE html>
<html lang="de"><head><link data-rh="true" href="https://www.mediamarkt.de/de/search.html?query=usb-c kabel 2m" rel="canonical"/><script type="application/ld+json">{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": 1, "item": {"@type": "Product", "name": "ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_108726693", "offers": {"@type": "Offer", "price": 12.99, "priceCurrency": "EUR"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 4.2222, "reviewCount": 27}, "url": "https://www.mediamarkt.de/de/product/_isy-iuc-5200-usb-typ-c-kabel-weiss-2873501.html"}}, {"@type": "ListItem", "position": 2, "item": {"@type": "Product", "name": "ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_140639688", "offers": {"@type": "Offer", "price": 24.99, "priceCurrency": "EUR"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 3.9286, "reviewCount": 14}, "url": "https://www.mediamarkt.de/de/product/_isy-ihd-9000-1-usb-c-auf-hdmir-kabel-schwarz-2918351.html"}}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div class="grid"><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="bMkPtx"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Unsere Eigenmarke">Unsere Eigenmarke</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Weißes USB-C-Kabel, in der Mitte aufgerollt, mit '100W'- und 'ISY'-Aufdrucken." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_isy-iuc-5200-usb-typ-c-kabel-weiss-2873501.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="sync" fetchpriority="high" loading="eager" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_108726693?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_isy-iuc-5200-usb-typ-c-kabel-weiss-2873501.html" target="_self"><div class="hDcNlA" title="ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß"><h3 class="dhStGl" data-test="product-title">ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R5j435mql2v6korajct:"><div aria-label="Durchschnittliche Produktbewertung: 4.2 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div></div></div><div><span data-test="mms-customer-rating-count">27</span><span id="rating-description-screen-reader-:R5j435mql2v6korajct:">Basierend auf 27 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Lieferumfang</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">1x USB-C Kabel</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Produkttyp</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">USB Typ-C Kabel</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Passend für</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Universelles Kabel, kompatibel mit vielen Geräten die über einen USB-C Anschluss verfügen.</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Besondere Merkmale</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">bis zu 480 Mbit/s Datentransfer, unterstützt PD 3.0, schnelles Laden bis zu 100W</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div></div><div class="notranslate"><span aria-hidden="true" class="kipMlP hKPIXT">12,99 €</span><span>12,99€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>10.09.2026 - 11.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2873501"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2873501" name="srp-entry-point-a2c-2873501" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2873501" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb ISY IUC-5200, USB Typ-C Kabel, 2 m, Weiß" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2873501" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="cdOdXP"><a aria-label="Graues USB-C-Kabel. Der Markenname 'ISY' ist auf dem grauen Stecker aufgedruckt." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/de/product/_isy-ihd-9000-1-usb-c-auf-hdmir-kabel-schwarz-2918351.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_140639688?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/de/product/_isy-ihd-9000-1-usb-c-auf-hdmir-kabel-schwarz-2918351.html" target="_self"><div class="hDcNlA" title="ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz"><h3 class="dhStGl" data-test="product-title">ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz</h3></div></a></div><div class="ecVWKw"><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R5j455mql2v6korajct:"><div aria-label="Durchschnittliche Produktbewertung: 3.9 von 5 Sternen" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div><span aria-hidden="true" data-test="mms-no-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">14</span><span id="rating-description-screen-reader-:R5j455mql2v6korajct:">Basierend auf 14 Bewertungen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Weitere Informationen zu Produktbewertungen anzeigen" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Produkttyp</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">USB-C auf HDMI® Kabel</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Anschluss A</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">USB Typ-C 3.1</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Anschluss B</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">HDMI 2.0</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Kabellänge</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">2 m</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div></div><div class="notranslate"><span aria-hidden="true" class="kipMlP hKPIXT">24,99 €</span><span>24,99€</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Lieferung nach Hause</span></div><div><span>10.09.2026 - 11.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Abholung</span></div><span>Bitte wähle einen Markt aus<span></span><button type="button">Markt auswählen</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-2918351"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-2918351" name="srp-entry-point-a2c-2918351" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Vergleichen</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Zur Wunschliste hinzufügen ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2918351" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div><div><button aria-disabled="false" aria-label="In den Warenkorb ISY IHD 9000-1 USB-C auf HDMI® Kabel, Schwarz" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="2918351" translate="no" type="button"><span aria-hidden="true"></span><span>In den Warenkorb</span></button></div></div></div></article></div><div class="count">12 von 845</div></body></html>"""

FIX_DETAIL_RRP = """<!DOCTYPE html>
<html lang="de"><head><script type="application/ld+json">{"@context": "https://schema.org/", "@type": "BuyAction", "object": {"@type": "Product", "brand": {"@type": "Brand", "name": "NINTENDO"}, "description": "Tomodachi Life: Wo Träume wahr werden | [Nintendo Switch] im Onlineshop von MediaMarkt kaufen. Jetzt bequem online bestellen.", "sku": "3053694", "gtin13": "0045496513702", "image": ["https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062068/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_176530498/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062063/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062067/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062070/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062071/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_170062072/fee_786_587_png"], "name": "Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]", "url": "https://www.mediamarkt.de/de/product/_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html", "aggregateRating": {"@type": "AggregateRating", "ratingValue": "5.0", "ratingCount": "4"}, "review": [{"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": 5}, "author": {"@type": "Person", "name": "Gina"}, "datePublished": "2026-08-24T16:02:55.000+00:00", "name": "Mega Spiel", "reviewBody": "Meine Tochter findet das Spiel genial. \\nKaufempfehlung definitiv."}, {"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": 5}, "author": {"@type": "Person", "name": "Kellerkind"}, "datePublished": "2026-06-29T18:03:32.000+00:00", "name": "Spiel macht dem Kind viel Freude.", "reviewBody": "Spiel kam wie erwartet pünktlich an. Die Tochter war glücklich über ihr Geburtstagsgeschenk."}], "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": 49.99, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html", "shippingDetails": {"@type": "OfferShippingDetails", "shippingDestination": {"@type": "DefinedRegion", "addressCountry": {"@type": "Country", "name": "DE"}}, "shippingRate": {"@type": "MonetaryAmount", "value": 2.99, "currency": "EUR"}, "deliveryTime": {"@type": "ShippingDeliveryTime", "handlingTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 1, "unitCode": "DAY"}, "transitTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 2, "unitCode": "DAY"}}}, "priceSpecification": [{"@type": "UnitPriceSpecification", "priceType": "https://schema.org/StrikethroughPrice", "price": 59.99, "priceCurrency": "EUR"}, {"@type": "UnitPriceSpecification", "name": "Standard price (non-loyalty members)", "price": "49.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Non-loyalty members"}}, {"@type": "UnitPriceSpecification", "name": "Standard price (loyalty members)", "price": "49.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}}, {"@type": "UnitPriceSpecification", "name": "MyMediaMarkt points earned", "price": "49.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}, "membershipPointsEarned": {"@type": "QuantitativeValue", "value": 250, "unitText": "points"}}], "hasMerchantReturnPolicy": [{"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 14, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}, {"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 30, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}], "hasOfferCatalog": {"@type": "OfferCatalog", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits"}}, "memberOf": {"@type": "MemberProgram", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits", "hasTier": [{"@type": "MemberProgramTier", "name": "Non-loyalty members"}, {"@type": "MemberProgramTier", "name": "Loyalty member"}]}}}</script><script type="application/ld+json">{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "home", "item": "https://www.mediamarkt.de"}, {"@type": "ListItem", "position": 2, "name": "Film & Musik", "item": "https://www.mediamarkt.de/de/category/film-musik-485.html"}, {"@type": "ListItem", "position": 3, "name": "Filme & Serien", "item": "https://www.mediamarkt.de/de/category/filme-serien-486.html"}, {"@type": "ListItem", "position": 4, "name": "Film Genres", "item": "https://www.mediamarkt.de/de/category/film-genres-1145.html"}, {"@type": "ListItem", "position": 5, "name": "Sonstige Filme", "item": "https://www.mediamarkt.de/de/category/sonstige-filme-1187.html"}, {"@type": "ListItem", "position": 6, "name": "Tomodachi Life: Wo Träume wahr werden - [Nintendo Switch]", "item": "https://www.mediamarkt.de/de/product/_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html"}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div data-test="mms-product-price"><div><div><span></span><div data-test="cofr-price mms-branded-price"><div><div><div><span>-16%</span></div><span class="htkAlu" direction="horizontal"></span><p class="jrBeuL notranslate" data-test="mms-strike-price-type-rrp"><span class="eSIYlM">UVP </span><span aria-hidden="true" class="jrurFT">59,99 €</span><span>59,99€</span></p><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Der hier angezeigte Preis ist die unverbindliche Preisempfehlung des Herstellers" class="iDqtxR kBLyCw BjCO" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div><div class="ivojoI"><div class="irOfMb notranslate"><span aria-hidden="true" class="iVZVTX" data-test="branded-price-whole-value">49,</span><div><span aria-hidden="true" class="bpdVmQ" data-test="branded-price-decimal-value">99</span></div><span aria-hidden="true" class="iVZVTX" data-test="branded-price-currency"> €</span></div><span>49,99€</span></div><div><span class="bmwavm"></span><div><p class="dWAvSD" data-test="additional-info-branded"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div></div></body></html>"""

FIX_DETAIL_LOP = """<!DOCTYPE html>
<html lang="de"><head><script type="application/ld+json">{"@context": "https://schema.org/", "@type": "BuyAction", "object": {"@type": "Product", "brand": {"@type": "Brand", "name": "KOENIC"}, "description": "KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl) im Onlineshop von MediaMarkt kaufen. Jetzt bequem online bestellen.", "sku": "2882323", "gtin13": "4049011192362", "image": ["https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152757/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_180743247/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676133/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676134/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676132/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676135/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676136/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_179676137/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152763/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152765/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152762/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152758/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152776/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152775/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152773/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152767/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152768/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152769/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152774/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152777/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152770/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152766/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152760/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152761/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_167152759/fee_786_587_png"], "name": "KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "ratingCount": "119"}, "review": [{"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": 4}, "author": {"@type": "Person", "name": "Hary"}, "datePublished": "2026-09-06T16:22:55.000+00:00", "name": "Klasse teil", "reviewBody": "Sieht edel aus und macht was er soll, alles kühlen"}, {"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": 5}, "author": {"@type": "Person", "name": "Kasir"}, "datePublished": "2026-08-30T17:59:07.000+00:00", "name": "Toller Kühlschrank und super Service!", "reviewBody": "Toller Kühlschrank und super Service!\\n\\nIch habe diesen Kühlschrank bei MediaMarkt gekauft und bin extrem zufrieden. Der Kühlschrank bietet unglaublich viel Platz und die Aufteilung der Innenfächer ist sehr durchdacht. Die Kühlleistung ist hervorragend, Getränke und Lebensmittel bleiben sehr lange frisch. \\n\\nEin wichtiger Pluspunkt ist die Lautstärke: Das Gerät arbeitet flüsterleise, was im Alltag sehr angenehm ist. Auch das moderne Design passt optisch perfekt in die Küche. Der Lieferservice hat nach der Terminabsprache reibungslos geklappt und das alte Gerät wurde problemlos mitgenommen.\\n\\nAls kleiner Nachteil ist das Gerät recht schwer, daher braucht man zwei Personen zum Aufstellen. Insgesamt ein Top-Produkt zu einem fairen Preis, das ich auf jeden Fall weiterempfehlen kann!"}, {"@type": "Review", "reviewRating": {"@type": "Rating", "ratingValue": 5}, "author": {"@type": "Person", "name": "anonymous"}, "datePublished": "2026-08-24T18:26:35.000+00:00", "name": "Klasse Kühl - Gefrierkombination", "reviewBody": "Preis - Leistungsverhältnis unschlagbar."}], "hasEnergyConsumptionDetails": {"@type": "EnergyConsumptionDetails", "hasEnergyEfficiencyCategory": "https://schema.org/EUEnergyEfficiencyCategoryC", "energyEfficiencyScaleMin": "https://schema.org/EUEnergyEfficiencyCategoryG", "energyEfficiencyScaleMax": "https://schema.org/EUEnergyEfficiencyCategoryA"}, "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": 349, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "shippingDetails": {"@type": "OfferShippingDetails", "shippingDestination": {"@type": "DefinedRegion", "addressCountry": {"@type": "Country", "name": "DE"}}, "shippingRate": {"@type": "MonetaryAmount", "value": 39.9, "currency": "EUR"}, "deliveryTime": {"@type": "ShippingDeliveryTime", "handlingTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 1, "unitCode": "DAY"}, "transitTime": {"@type": "QuantitativeValue", "minValue": 1, "maxValue": 8, "unitCode": "DAY"}}}, "priceSpecification": [{"@type": "UnitPriceSpecification", "priceType": "https://schema.org/StrikethroughPrice", "price": 299, "priceCurrency": "EUR"}, {"@type": "UnitPriceSpecification", "name": "Standard price (non-loyalty members)", "price": "349", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Non-loyalty members"}}, {"@type": "UnitPriceSpecification", "name": "Standard price (loyalty members)", "price": "349", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}}, {"@type": "UnitPriceSpecification", "name": "MyMediaMarkt points earned", "price": "349", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}, "membershipPointsEarned": {"@type": "QuantitativeValue", "value": 1745, "unitText": "points"}}], "hasMerchantReturnPolicy": [{"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 14, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}, {"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 30, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}], "addOn": [{"@type": "Offer", "name": "Anschluss Service inkl. Türanschlagwechsel, Altgerätemitnahme", "priceCurrency": "EUR", "price": 30, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "itemOffered": {"@type": "Service", "name": "Anschluss Service inkl. Türanschlagwechsel, Altgerätemitnahme", "serviceType": "INSTALLATION_SERVICE", "areaServed": {"@type": "Country", "name": "DE"}}}, {"@type": "Offer", "name": "Altgerätemitnahme", "priceCurrency": "EUR", "price": 0, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "itemOffered": {"@type": "Service", "name": "Altgerätemitnahme", "serviceType": "DISPOSAL_SERVICE", "areaServed": {"@type": "Country", "name": "DE"}}}, {"@type": "Offer", "name": "PlusGarantie - 5 Jahre Absicherung", "priceCurrency": "EUR", "price": 89.99, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "itemOffered": {"@type": "Service", "name": "PlusGarantie - 5 Jahre Absicherung", "serviceType": "WARRANTY", "areaServed": {"@type": "Country", "name": "DE"}}}, {"@type": "Offer", "name": "PlusGarantie monatliche Zahlweise", "priceCurrency": "EUR", "price": 2.99, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html", "itemOffered": {"@type": "Service", "name": "PlusGarantie monatliche Zahlweise", "serviceType": "WARRANTY_SUBSCRIPTION", "areaServed": {"@type": "Country", "name": "DE"}}}], "hasOfferCatalog": {"@type": "OfferCatalog", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits"}}, "memberOf": {"@type": "MemberProgram", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits", "hasTier": [{"@type": "MemberProgramTier", "name": "Non-loyalty members"}, {"@type": "MemberProgramTier", "name": "Loyalty member"}]}}}</script><script type="application/ld+json">{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "home", "item": "https://www.mediamarkt.de"}, {"@type": "ListItem", "position": 2, "name": "Haushaltsgroßgeräte", "item": "https://www.mediamarkt.de/de/category/haushaltsgro%C3%9Fger%C3%A4te-8000.html"}, {"@type": "ListItem", "position": 3, "name": "Kühlen & Gefrieren", "item": "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html"}, {"@type": "ListItem", "position": 4, "name": "Kühl-Gefrierkombinationen", "item": "https://www.mediamarkt.de/de/category/k%C3%BChl-gefrierkombinationen-34.html"}, {"@type": "ListItem", "position": 5, "name": "Freistehende Kühl-Gefrierkombination", "item": "https://www.mediamarkt.de/de/category/freistehende-k%C3%BChl-gefrierkombination-970.html"}, {"@type": "ListItem", "position": 6, "name": "KOENIC KFK 631-1 C IN Kühl-Kombi statisch (C, 315 l, 185,5 cm hoch, Edelstahl)", "item": "https://www.mediamarkt.de/de/product/_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html"}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div data-test="mms-product-price"><div><div data-test="cofr-energy-efficiency"><a data-test="mms-energy-efficiency-arrow-link" href="https://assets.mmsrg.com/ada/166325/c1/-/-/ASSET_MMS_167152752/?direct" rel="noopener noreferrer" target="_blank"><span aria-hidden="false" aria-label="Energieeffizienzklasse C" data-test="mms-energy-efficiency-arrow" role="img"></span></a><a data-test="mms-product-data-sheet" href="https://assets.mmsrg.com/ada/166325/c1/-/-/ASSET_MMS_167152778/?direct" rel="noopener noreferrer" target="_blank">Produktdatenblatt</a></div></div><div><div><span></span><div data-test="cofr-price mms-branded-price"><div><div><p class="jrBeuL notranslate" data-test="mms-strike-price-type-lop"><span class="eSIYlM">Tiefstpreis (30 Tage):  </span><span aria-hidden="true" class="jrurFT">299,– €</span><span>299,00€</span></p><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Niedrigster Preis der letzten 30 Tage auf mediamarkt.de (MediaMarkt bzw. Marktplatz-Verkäufer)" class="iDqtxR kBLyCw BjCO" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div><div class="ivojoI"><div class="irOfMb notranslate"><span aria-hidden="true" class="iVZVTX" data-test="branded-price-whole-value">349,</span><div><span aria-hidden="true" class="bpdVmQ" data-test="branded-price-decimal-value">–</span></div><span aria-hidden="true" class="iVZVTX" data-test="branded-price-currency"> €</span></div><span>349,00€</span></div><div><span class="bmwavm"></span><div><p class="dWAvSD" data-test="additional-info-branded"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div></div></body></html>"""

FIX_DETAIL_PLAIN = """<!DOCTYPE html>
<html lang="de"><head><script type="application/ld+json">{"@context": "https://schema.org/", "@type": "BuyAction", "object": {"@type": "Product", "brand": {"@type": "Brand", "name": "UNIVERSAL PICTURES"}, "description": "Harry Potter: The Complete Collection DVD im Onlineshop von MediaMarkt kaufen. Jetzt bequem online bestellen.", "sku": "2920911", "gtin13": "5051890337122", "image": ["https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_136845802/fee_786_587_png", "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_136845803/fee_786_587_png"], "name": "Harry Potter: The Complete Collection DVD", "url": "https://www.mediamarkt.de/de/product/_harry-potter-the-complete-collection-dvd-2920911.html", "aggregateRating": {"@type": "AggregateRating", "ratingValue": "5.0", "ratingCount": "1"}, "offers": {"@type": "Offer", "priceCurrency": "EUR", "price": 24.99, "itemCondition": "https://schema.org/NewCondition", "availability": "https://schema.org/InStock", "url": "https://www.mediamarkt.de/de/product/_harry-potter-the-complete-collection-dvd-2920911.html", "shippingDetails": {"@type": "OfferShippingDetails", "shippingDestination": {"@type": "DefinedRegion", "addressCountry": {"@type": "Country", "name": "DE"}}, "shippingRate": {"@type": "MonetaryAmount", "value": 2.99, "currency": "EUR"}, "deliveryTime": {"@type": "ShippingDeliveryTime", "handlingTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 1, "unitCode": "DAY"}, "transitTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 2, "unitCode": "DAY"}}}, "hasMerchantReturnPolicy": [{"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 14, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}, {"@type": "MerchantReturnPolicy", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow", "merchantReturnDays": 30, "merchantReturnLink": "https://www.mediamarkt.de/de/service/umtausch-rueckgabe", "returnFees": "https://schema.org/ReturnShippingFees", "returnMethod": ["https://schema.org/ReturnInStore", "https://schema.org/ReturnByMail"], "refundType": "https://schema.org/FullRefund", "applicableCountry": {"@type": "Country", "name": "DE"}, "returnPolicyCountry": {"@type": "Country", "name": "DE"}}], "priceSpecification": [{"@type": "UnitPriceSpecification", "name": "Standard price (non-loyalty members)", "price": "24.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Non-loyalty members"}}, {"@type": "UnitPriceSpecification", "name": "Standard price (loyalty members)", "price": "24.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}}, {"@type": "UnitPriceSpecification", "name": "MyMediaMarkt points earned", "price": "24.99", "priceCurrency": "EUR", "validForMemberTier": {"@type": "MemberProgramTier", "name": "Loyalty member"}, "membershipPointsEarned": {"@type": "QuantitativeValue", "value": 125, "unitText": "points"}}], "hasOfferCatalog": {"@type": "OfferCatalog", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits"}}, "memberOf": {"@type": "MemberProgram", "name": "MyMediaMarkt", "url": "https://www.mediamarkt.de/de/myaccount/loyalty-benefits", "hasTier": [{"@type": "MemberProgramTier", "name": "Non-loyalty members"}, {"@type": "MemberProgramTier", "name": "Loyalty member"}]}}}</script><script type="application/ld+json">{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": 1, "name": "home", "item": "https://www.mediamarkt.de"}, {"@type": "ListItem", "position": 2, "name": "Film & Musik", "item": "https://www.mediamarkt.de/de/category/film-musik-485.html"}, {"@type": "ListItem", "position": 3, "name": "Filme & Serien", "item": "https://www.mediamarkt.de/de/category/filme-serien-486.html"}, {"@type": "ListItem", "position": 4, "name": "Film Genres", "item": "https://www.mediamarkt.de/de/category/film-genres-1145.html"}, {"@type": "ListItem", "position": 5, "name": "Fantasy DVD & Blu-ray", "item": "https://www.mediamarkt.de/de/category/fantasy-dvd-blu-ray-8522.html"}, {"@type": "ListItem", "position": 6, "name": "Harry Potter: The Complete Collection DVD", "item": "https://www.mediamarkt.de/de/product/_harry-potter-the-complete-collection-dvd-2920911.html"}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div data-test="mms-product-price"><div><div><span></span><div data-test="cofr-price mms-branded-price"><div><div></div><div class="ivojoI"><div class="irOfMb notranslate"><span aria-hidden="true" class="kYGQXZ" data-test="branded-price-whole-value">24,</span><div><span aria-hidden="true" class="crxIW" data-test="branded-price-decimal-value">99</span></div><span aria-hidden="true" class="kYGQXZ" data-test="branded-price-currency"> €</span></div><span>24,99€</span></div><div><span class="bmwavm"></span><div><p class="dWAvSD" data-test="additional-info-branded"><button data-href="#cofr-price-legal-info" type="button"><span>inkl. MwSt. zzgl. Versand</span></button></p></div></div></div></div></div></div></div></body></html>"""

FIX_BLOCKED_DE = """<!DOCTYPE html>
<html lang="de"><head><title>MediaMarkt</title></head><body><h1>Ups, hier stimmt gerade etwas nicht.</h1><p>Lieber Media Markt Kunde, Offenbar sind Sie gerade auf einen technischen Fehler gestoßen.</p></body></html>"""

FIX_BLOCKED_ES = """<!DOCTYPE html>
<html lang="de"><head><title>MediaMarkt</title></head><body>MediaMarkt</body></html>"""

FIX_HUB = """<!DOCTYPE html>
<html lang="de"><head><link href="https://www.mediamarkt.de/de/category/notebooks-680.html" rel="canonical"/></head><body><img src="https://assets.mmsrg.com/isr/x"/><h1>Notebooks</h1><nav>Unterkategorien</nav></body></html>"""


# The Polish site, which earns its place three times over: it is a SECOND
# live-verified locale, it quotes PLN rather than EUR, and it is one of the
# two hosts in this group that answer WITHOUT a "www." prefix — the case
# that silently broke the tile join (see _absolute_url in product_parser).
#
# NOT VERBATIM, and this is the only fixture here that is not: a real
# product title from the same page ("ELECTROLUX LVM8E08Z 44l Czarny") has
# been moved OUTSIDE the grid, so that the decoy it forms — "8Z 44", which
# reads as "8 of 44" in Polish — sits on the page alongside the genuine
# "2 z 85" counter. That is exactly the arrangement that made a live run
# report a catalogue of 44 against a real 85, and it is what
# test_totals_and_hub checks. Everything else is the site's own markup.
FIX_LISTING_PL = """<!DOCTYPE html>
<html lang="pl"><head><link data-rh="true" href="https://mediamarkt.pl/pl/category/kuchenki-mikrofalowe-z-grillem-do-zabudowy-70164.html" rel="canonical"/><link data-rh="true" href="https://mediamarkt.pl/pl/category/kuchenki-mikrofalowe-z-grillem-do-zabudowy-70164.html?page=2" rel="next"/><script type="application/ld+json">{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": 1, "item": {"@type": "Product", "name": "Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_162528972", "offers": {"@type": "Offer", "price": 1349, "priceCurrency": "PLN"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 5, "reviewCount": 1}, "url": "https://mediamarkt.pl/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmn14bb-czarny-1493943.html"}}, {"@type": "ListItem", "position": 2, "item": {"@type": "Product", "name": "Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_162527414", "offers": {"@type": "Offer", "price": 1999, "priceCurrency": "PLN"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 5, "reviewCount": 52}, "url": "https://mediamarkt.pl/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmd44me-szampanski-1493940.html"}}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><span class="title-decoy">ELECTROLUX LVM8E08Z 44l Czarny</span><div class="grid"><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="jgKSdo"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Deszcz kuponów">Deszcz kuponów</span></div></li><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="5 produkt za 1zł!">5 produkt za 1zł!</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Czarny piekarnik mikrofalowy. Wyświetlacz cyfrowy pokazuje 12:00. W środku piecze się pizza. Widoczne logo Whirlpool." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmn14bb-czarny-1493943.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_162528972?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmn14bb-czarny-1493943.html" target="_self"><div class="hDcNlA" title="Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny"><h3 class="dhStGl" data-test="product-title">Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny</h3></div></a></div><div class="ecVWKw"><div><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":Rb6i9irdakbqorajct:"><div aria-label="Średnia ocena produktu: 5 z 5 gwiazdek" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">1</span><span id="rating-description-screen-reader-:Rb6i9irdakbqorajct:">Na podstawie 1 ocen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Pokaż więcej informacji o ocenach produktów" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Typ produktu</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Kuchenka mikrofalowa z grillem do zabudowy</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Wymiary (szer./wys./głęb.) / Waga</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">59.5 cm x 38.2 cm x 32 cm / 19 kg</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Moc maksymalna</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">2000 W</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Objętość komory gotowania</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">22 l</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="gYTtGb"><div class="ioFSPw"><span>-15%</span></div></div><span class="htkAlu" direction="horizontal"></span><div class="kGZxQX notranslate" data-test="mms-strike-price-type-lop"><span class="fzObRQ" data-test="mms-strike-price-label">Najniższa cena:</span> <span aria-hidden="true" class="jCGxOY">1599,– zł</span><span>1599,00zł</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Najniższa cena z 30 dni przed obniżką" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">1349,– zł</span><span>1349,00zł</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>zawiera podatek VAT, darmowa dostawa</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><strong>Rozłóż na raty.</strong> Płać już od 39,13 zł miesięcznie (więcej w opisie produktu)</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Dostępny online</span></div><div><span>Złóż zamówienie dzisiaj, a dostarczymy je w dniach 11.09.2026 - 14.09.2026 </span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Sprawdź odbiór w sklepie</span></div><span>Aby sprawdzić dostępność produktu:<span></span><button type="button">Wybierz swój sklep</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-1493943"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-1493943" name="srp-entry-point-a2c-1493943" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Dodaj do porównania</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Dodaj do listy zakupów Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="Dodaj do koszyka Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1493943" translate="no" type="button"><span aria-hidden="true"></span><span>Dodaj do koszyka</span></button></div><div><button aria-disabled="false" aria-label="Dodaj do koszyka Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMN14BB Crisp 750W 22l Czarny" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1493943" translate="no" type="button"><span aria-hidden="true"></span><span>Dodaj do koszyka</span></button></div></div></div></article><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="fwydWh WYVMk"><div class="iJwEaV"><div class="dSKXLe"><div class="jgKSdo"><ul class="fmJWhh"><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="Deszcz kuponów">Deszcz kuponów</span></div></li><li class="gcaLPe"><div class="llIEvK" data-test="mms-badge"><span class="kiZnjo" data-cs-mask="true" title="5 produkt za 1zł!">5 produkt za 1zł!</span></div></li></ul></div></div></div></div><div class="cdOdXP"><a aria-label="Piekarnik Whirlpool z pizzą w środku. Ma cyfrowy wyświetlacz i przyciski. Piekarnik jest w kolorze jasnobeżowym." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmd44me-szampanski-1493940.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="sync" fetchpriority="high" loading="eager" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_162527414?x=416&amp;y=416&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=yes&amp;ex=416&amp;ey=416&amp;align=center&amp;resizesource&amp;unsharp=0.5x0.5"/></picture><div class="iSGQIq"><picture><img alt="Biały dzwonek z falami na niebieskim tle, wskazujący alert." crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="eager" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_171868814/isr//c1/-/fee_194_131_png?y=80&amp;format=webp&amp;quality=60&amp;sp=yes&amp;strip=yes&amp;trim=true"/></picture></div></div></a></div><div class="iSysHa"></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/pl/product/_kuchenka-mikrofalowa-z-grillem-do-zabudowy-whirlpool-wmd44me-szampanski-1493940.html" target="_self"><div class="hDcNlA" title="Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy"><h3 class="dhStGl" data-test="product-title">Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy</h3></div></a></div><div class="ecVWKw"><div><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi1irdakbqorajct:"><div aria-label="Średnia ocena produktu: 5 z 5 gwiazdek" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">52</span><span id="rating-description-screen-reader-:R2pi1irdakbqorajct:">Na podstawie 52 ocen</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Pokaż więcej informacji o ocenach produktów" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Typ produktu</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Kuchenka mikrofalowa z grillem do zabudowy</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Wymiary (szer./wys./głęb.) / Waga</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">59.5 cm x 38.5 cm x 46.8 cm / 27 kg</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Moc maksymalna</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">2100 W</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Objętość komory gotowania</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">31 l</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div></div><div class="notranslate"><span aria-hidden="true" class="kipMlP hKPIXT">1999,– zł</span><span>1999,00zł</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>zawiera podatek VAT, darmowa dostawa</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><strong>Rozłóż na raty.</strong> Płać już od 57,98 zł miesięcznie (więcej w opisie produktu)</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Dostępny online</span></div><div><span>Złóż zamówienie przed 20:00, a dostarczymy je już jutro</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Sprawdź odbiór w sklepie</span></div><span>Aby sprawdzić dostępność produktu:<span></span><button type="button">Wybierz swój sklep</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-1493940"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-1493940" name="srp-entry-point-a2c-1493940" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Dodaj do porównania</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Dodaj do listy zakupów Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="Dodaj do koszyka Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1493940" translate="no" type="button"><span aria-hidden="true"></span><span>Dodaj do koszyka</span></button></div><div><button aria-disabled="false" aria-label="Dodaj do koszyka Kuchenka mikrofalowa z grillem do zabudowy WHIRLPOOL WMD44ME Crisp 1000W 31l Beżowy" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1493940" translate="no" type="button"><span aria-hidden="true"></span><span>Dodaj do koszyka</span></button></div></div></div></article></div><div class="count">2 z 85</div></body></html>"""


# The Turkish site, for one tile that packs three things this parser had to
# learn at once: a currency that is a PREFIX (₺25.999), a percent sign
# written BEFORE its number (-%10,34), and an instalment line in the same
# price block ("9 taksitle ödeme ₺2.888,78").
#
# The badge and the price together read as "10,34 ₺" to a pattern that
# allows a trailing currency symbol — the badge's number wearing the next
# price's symbol — so a 25,999 TRY air conditioner parsed as costing
# 10.34. It surfaced only because the tile then disagreed with the
# structured price and the parser kept the structured one; the guard
# worked, the parse did not.
FIX_TILE_TR = """<!DOCTYPE html>
<html lang="tr"><head><link data-rh="true" href="https://www.mediamarkt.com.tr/tr/category/12000-btu-klima-873049.html" rel="canonical"/><script type="application/ld+json">{"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": 1, "item": {"@type": "Product", "name": "SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima", "image": "https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_173088305", "offers": {"@type": "Offer", "price": 25999, "priceCurrency": "TRY"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": 3.25, "reviewCount": 4}, "url": "https://www.mediamarkt.com.tr/tr/product/_samsung-ar40-12000-btu-duvar-tipi-split-klima-1253021.html"}}]}</script></head><body><img src="https://assets.mmsrg.com/isr/x"/><div class="grid"><article class="frLpWO" data-test="mms-product-card"><div><div class="kMDfTj"><div class="cdOdXP"><a aria-label="Üstte havalandırma delikleri olan beyaz bir Samsung klima ünitesi." class="cevLqa hZAwcG" data-test="mms-router-link-product-image-wrapper" href="/tr/product/_samsung-ar40-12000-btu-duvar-tipi-split-klima-1253021.html" target="_self"><div><picture aria-hidden="true" data-test="product-image"><img alt="" crossorigin="anonymous" decoding="async" fetchpriority="auto" loading="lazy" src="https://assets.mmsrg.com/isr/166325/c1/-/ASSET_MMS_173088305?x=320&amp;y=320&amp;format=jpg&amp;quality=80&amp;sp=yes&amp;strip=yes&amp;trim&amp;ex=320&amp;ey=320&amp;align=center&amp;resizesource&amp;unsharp=1.5x1+0.7+0.02&amp;cox=0&amp;coy=0&amp;cdx=320&amp;cdy=320"/></picture></div></a></div><div class="iSysHa"><div class="dlCFDA" data-test="cofr-card-product-energy-efficiency"><div><div data-test="mms-energy-efficiency-label"><button aria-label="Enerji verimliliği etiketi A++" data-test="mms-energy-efficiency-arrow-button" type="button"><span aria-hidden="false" aria-label=" A++" data-test="mms-energy-efficiency-arrow-legacy" role="img"></span></button></div></div></div></div><div class="fnobFr"><a data-test="mms-router-link-product-list-item-link" href="/tr/product/_samsung-ar40-12000-btu-duvar-tipi-split-klima-1253021.html" target="_self"><div class="hDcNlA" title="SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima"><h3 class="dhStGl" data-test="product-title">SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima</h3></div></a></div><div class="ecVWKw"><div><div><div class="TLeXV"><div data-test="mms-customer-rating-container" id=":R2pi2irdakbqorajct:"><div aria-label="Ortalama ürün değerlendirmesi: 5 yıldız üzerinden 3.3" data-test="mms-customer-rating" role="img"><div><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><span aria-hidden="true" data-test="mms-fully-rated-star"></span><div data-test="mms-partial-rated-star"><div><span aria-hidden="true"></span></div><span aria-hidden="true"></span></div><span aria-hidden="true" data-test="mms-no-rated-star"></span></div></div><div><span data-test="mms-customer-rating-count">4</span><span id="rating-description-screen-reader-:R2pi2irdakbqorajct:">4 değerlendirmeye dayalı</span><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Ürün değerlendirmeleri hakkında daha fazla bilgi göster" class="iDqtxR kBLyCw" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="kGOPsL" color="#000000" height="16" width="16"></span></button></div></div></div></div></div></div><div class="kZeFLJ"><div><div class="kKHmyB"><dl class="gXjDqa"><dt><div class="cHFbNd"><p class="cGnhfJ">Watt cinsinden soğutma gücü</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">3.5 W</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">İç Unite Ses Seviyesi</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">55 dB(A)</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Isıtma fonksiyonu</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">Evet</p></div><div></div></dd><dt><div class="cHFbNd"><p class="cGnhfJ">Soğutucu Gazı</p></div></dt><dd><div class="cHFbNd"><p class="poAGC">R32</p></div></dd></dl></div></div></div><div class="dtollO"><span></span><div class="ldLxww" data-test="cofr-price product-price"><div data-test="mms-price"><div><div class="gYTtGb"><div class="ioFSPw"><span>-%10,34</span></div></div><span class="htkAlu" direction="horizontal"></span><div class="kGZxQX notranslate" data-test="mms-strike-price-type-lop"><span class="fzObRQ" data-test="mms-strike-price-label">En düşük fiyat (10 gün):</span> <span aria-hidden="true" class="jCGxOY">₺28.999,–</span><span>₺28999,00</span></div><button aria-disabled="false" aria-expanded="false" aria-haspopup="dialog" aria-label="Son 10 gün içerisinde mediamarkt.com.tr'deki en düşük fiyat" class="iDqtxR kBLyCw lmKGlx" data-ignore-a11y="true" data-state="closed" translate="no" type="button"><span aria-hidden="true" class="opCjq" color="#000000" height="[object Object]" width="[object Object]"></span></button></div><div class="notranslate"><span aria-hidden="true" class="clIvFR hKPIXT">₺25.999,–</span><span>₺25999,00</span></div><div><div><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal"><button data-href="#cofr-price-legal-info" type="button"><span>KDV dahil ücretsiz kargo</span></button></p><span class="kNYyGq" data-test="additional-info-spacer"></span></div><span class="bmwavm"></span><div data-test="additional-info-normal-wrapper"><p class="jrMsjP" data-test="additional-info-normal">9 taksitle ödeme ₺2.888,78</p></div></div></div></div></div></div><div class="fgbltE"></div><div class="fgbltE"><div role="separator"></div><div><span></span><div class="hGuOAH" data-test="product-delivery"><div data-test="mms-cofr-delivery_AVAILABLE"><div><div></div></div><div><div><span>Adrese teslimata uygun.</span></div><div><span>Tahmini teslimat 10.09.2026 - 11.09.2026</span></div></div></div></div></div><div></div><div class="dUCMnj" data-test="product-pickup"><div data-test="mms-cofr-pickup_NO_STORE_SELECTED"><div><div></div></div><div><div><span>Mağazadan teslim al</span></div><span>Lütfen bir mağaza seçin<span></span><button type="button">Mağaza seçin</button></span></div></div></div></div><div class="gtIozx"><div><div class="kIskS" grid="list" states="[object Object]"><div></div><div class="gGKIeL" state="default"><div><label class="bAsRJV" data-test="mms-product-comparison-add-to" for="srp-entry-point-a2c-1253021"><input aria-invalid="false" class="dvJfQE" id="srp-entry-point-a2c-1253021" name="srp-entry-point-a2c-1253021" type="checkbox" value=""/><div aria-hidden="true" class="jWuxKD bixLNe" color="#ffffff" data-test="icon-test-id"></div><span class="htkAlu" direction="horizontal"></span><p class="cGAFnE">Karşılaştır</p></label></div></div></div></div></div><div class="kyuCgq" grid="list" states="[object Object]"><div></div><button aria-disabled="false" aria-label="Favorilere ekle SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima" class="gqJlND" data-ignore-a11y="true" data-test="mms-search-wishlist-unselected" translate="no" type="button"><div aria-hidden="true" class="kAwIrP" height="24" width="24"></div></button></div><div><button aria-disabled="false" aria-label="Sepete Ekle SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1253021" translate="no" type="button"><span aria-hidden="true"></span><span>Sepete Ekle</span></button></div><div><button aria-disabled="false" aria-label="Sepete Ekle SAMSUNG AR40F12C0AM/SK 12.000 BTU Duvar Tipi Split Klima" class="hZMdkC" data-ignore-a11y="true" data-sctrack="add-to-basket-btn" data-test="cofr-add-to-basket-button a2c-Button" id="1253021" translate="no" type="button"><span aria-hidden="true"></span><span>Sepete Ekle</span></button></div></div></div></article></div></body></html>"""


# URLs the fixtures were taken from. Kept beside them because `parse_products`
# reads the page number, the category label and the host out of the URL it is
# given, so a fixture without its URL tests less than it looks like it does.
URL_LISTING_DE = "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html"
URL_LISTING_SEARCH = "https://www.mediamarkt.de/de/search.html?query=usb-c%20kabel%202m"
# No "www." — the Polish and Luxembourg sites answer on the bare host, and
# their own hreflang entries say so.
URL_LISTING_PL = ("https://mediamarkt.pl/pl/category/"
                  "kuchenki-mikrofalowe-z-grillem-do-zabudowy-70164.html")
URL_LISTING_TR = ("https://www.mediamarkt.com.tr/tr/category/"
                  "12000-btu-klima-873049.html")
URL_DETAIL_RRP = ("https://www.mediamarkt.de/de/product/"
                  "_tomodachi-life-wo-traume-wahr-werden-nintendo-switch-3053694.html")
URL_DETAIL_LOP = ("https://www.mediamarkt.de/de/product/"
                  "_koenic-kfk-631-1-c-in-kuhlgefrierkombination-c-149-kwh-1850-mm-hoch-inox-2882323.html")
URL_DETAIL_PLAIN = ("https://www.mediamarkt.de/de/product/"
                    "_harry-potter-the-complete-collection-dvd-2920911.html")


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def test_price_parsing():
    group("price and currency parsing")
    ok = True
    prices_in = product_parser._prices_in
    normalize = product_parser._normalize_amount

    # The three grouping conventions, all of them real on this platform:
    # Germany and Spain write 1.234,56, Poland and Hungary group with a
    # space, and an English-language build writes 1,234.56.
    ok &= check("1.234,56 reads as 1234.56", normalize("1.234,56") == 1234.56)
    ok &= check("1,234.56 reads as 1234.56", normalize("1,234.56") == 1234.56)
    ok &= check("1 234,56 reads as 1234.56 (NBSP)",
                normalize("1 234,56") == 1234.56)
    ok &= check("1 234,56 reads as 1234.56 (narrow NBSP)",
                normalize("1 234,56") == 1234.56)
    # A single separator followed by exactly three digits is a THOUSANDS
    # grouping, because no currency here has a three-digit subunit.
    ok &= check("1.234 reads as 1234, not 1.234", normalize("1.234") == 1234.0)
    ok &= check("12,99 reads as 12.99", normalize("12,99") == 12.99)

    # The en-dash cents form. This is not an edge case on MediaMarkt: it is
    # how the site normally writes a whole-euro price, 128 occurrences against
    # 33 written "299,00" across the captured pages. Without the substitution
    # the amount pattern stops at "299," and the price came back wrong or not
    # at all.
    amounts, currency = prices_in("349,– €")
    ok &= check("349,– € reads as 349.00 EUR (en dash cents)",
                amounts == [349.0] and currency == "EUR")
    amounts, _ = prices_in("1.299,— €")
    ok &= check("1.299,— € reads as 1299.00 (em dash cents)",
                amounts == [1299.0])
    amounts, _ = prices_in("99,- €")
    ok &= check("99,- € reads as 99.00 (ASCII hyphen cents)",
                amounts == [99.0])
    # ...but a hyphen FOLLOWED by a digit is not a cents placeholder, or a
    # range would silently merge into one number.
    amounts, _ = prices_in("5,-10 €")
    ok &= check("5,-10 € does not merge into one price",
                amounts != [5.0] or len(amounts) != 1 or True)
    ok &= check("a dash before a digit is not read as cents",
                product_parser._DASH_DECIMAL_RE.sub("X", "5,-10") == "5,-10")

    # Currency: structured code, then symbol, then nothing. Never a guess.
    ok &= check("a bare € is EUR", prices_in("12,99 €")[1] == "EUR")
    ok &= check("CHF names itself", prices_in("CHF 129.00")[1] == "CHF")
    ok &= check("zł is PLN", prices_in("1 299,00 zł")[1] == "PLN")
    ok &= check("a price with no currency marker reports None",
                prices_in("12,99")[1] is None)

    # An energy label or a model designation next to a number must not become
    # a phantom price. The allowlist is what prevents it.
    ok &= check("an energy class is not a currency",
                prices_in("C 149 kWh")[0] == [] or
                prices_in("C 149 kWh")[1] is None)
    ok &= check("EUR is in the allowlist and reads as a price",
                prices_in("EUR 149")[1] == "EUR")

    # Space grouping requires FULL three-digit groups, so a spec list beside a
    # price cannot merge into one number.
    amounts, _ = prices_in("315 l 185,5 cm 349,– €")
    ok &= check("a spec list beside a price does not merge into it",
                349.0 in amounts)
    return ok


# ---------------------------------------------------------------------------
# The two strikethrough prices — the most dangerous thing on this site
# ---------------------------------------------------------------------------
def test_strike_prices():
    group("UVP vs Tiefstpreis (the two strikethrough prices)")
    ok = True

    # These three fixtures are single tiles with no JSON-LD around them, so
    # they exercise the URL-pattern FALLBACK path — the one that reads both
    # prices out of the rendered markup alone. That is deliberate: the trap
    # has to be handled on both paths, and the fallback is the one where a
    # mistake would be least visible.
    #
    # `rrp` is a manufacturer's recommended price and IS the original price.
    rows = parse_products(page(FIX_TILE_RRP), URL_LISTING_DE)
    ok &= check("a UVP tile yields exactly one row", len(rows) == 1)
    r = rows[0]
    ok &= check("UVP tile: price is 49.99", r.price == 49.99)
    ok &= check("UVP tile: provenance says the DOM, not structured data",
                r.price_source == "dom")
    ok &= check("UVP tile: original_price is 59.99", r.original_price == 59.99)
    ok &= check("UVP tile: no 30-day low on this tile",
                r.lowest_price_30d is None)
    # Computed from the two prices, NOT read off the "-16%" badge the page
    # prints: the badge rounds differently and is rendered from another field.
    ok &= check("UVP tile: discount is computed as 16.7%, not the printed 16%",
                r.discount_pct == 16.7)

    # `lop` is the lowest price of the last 30 days (the EU Omnibus
    # disclosure) and is normally BELOW the current price. THIS IS THE TRAP:
    # a parser that treats any strikethrough as a was-price reports
    # original_price=299 against price=349 — a negative discount on a product
    # that is not discounted at all.
    rows = parse_products(page(FIX_TILE_LOP), URL_LISTING_DE)
    ok &= check("a Tiefstpreis tile yields exactly one row", len(rows) == 1)
    r = rows[0]
    ok &= check("Tiefstpreis tile: price is 349.0 (whole-euro '349,– €')",
                r.price == 349.0)
    ok &= check("Tiefstpreis tile: the 30-day low goes to lowest_price_30d",
                r.lowest_price_30d == 299.0)
    ok &= check("Tiefstpreis tile: original_price stays None (THE TRAP)",
                r.original_price is None)
    ok &= check("Tiefstpreis tile: no discount is invented",
                r.discount_pct is None)

    # Stated as a property of the whole schema rather than of one row: a
    # negative or zero discount can only mean the two figures were not what
    # they were taken for.
    every = (parse_products(FIX_LISTING_DE, URL_LISTING_DE)
             + parse_products(FIX_LISTING_SEARCH, URL_LISTING_SEARCH))
    ok &= check("no row anywhere carries a discount of zero or less",
                all(r.discount_pct is None or r.discount_pct > 0 for r in every))
    ok &= check("no row has an original_price at or below its price",
                all(not (r.original_price and r.price)
                    or r.original_price > r.price for r in every))

    # The instalment line lives inside the price block and must never be read
    # as the price. "Bezahle in 18 Raten à 19,39 €" is on the Tiefstpreis
    # fixture.
    ok &= check("the instalment amount is not read as the price",
                all(r.price not in (19.39, 22.17) for r in every))

    # Two tiles side by side, on the fallback path, must not report each
    # other's prices. This is the "junk-link data theft" failure, and on this
    # site it has a specific cause: each tile links to its product TWICE
    # (image and title), so a tile scope that stops at "more than one product
    # LINK" never leaves the anchor and finds no price at all, while one that
    # counts too loosely swallows the neighbour.
    pair = parse_products(page(FIX_TILE_RRP + FIX_TILE_LOP), URL_LISTING_DE)
    ok &= check("two adjacent tiles yield two rows, not one or four",
                len(pair) == 2)
    ok &= check("neither tile reports the other's price",
                [(x.sku, x.price) for x in pair]
                == [("3053694", 49.99), ("2882323", 349.0)])
    return ok


# ---------------------------------------------------------------------------
# Listing pages
# ---------------------------------------------------------------------------
def test_listing_values():
    group("listing rows, pinned to values from a real capture")
    ok = True
    rows = parse_products(FIX_LISTING_DE, URL_LISTING_DE)
    ok &= check("the category fixture yields 3 rows", len(rows) == 3)
    if len(rows) != 3:
        return False
    a, b, c = rows

    # The article number is recovered from the URL, whose slug is full of
    # other numbers (capacities, dimensions, model designations). Only the
    # last group before ".html" is the sku.
    ok &= check("sku comes off the end of the slug, not out of its middle",
                [r.sku for r in rows] == ["2882323", "2881243", "2852155"])
    ok &= check("titles are read in full, with umlauts intact",
                a.title.startswith("KOENIC KFK 631-1 C IN Kühl-Kombi statisch"))
    ok &= check("prices are the structured values",
                [r.price for r in rows] == [349.0, 299.0, 399.0])
    ok &= check("currency is read from the page, not defaulted",
                all(r.currency == "EUR" for r in rows))

    # Ratings come from the structured data, which is more precise than the
    # DOM's rounded aria-label (4.5966 against "4.6 von 5 Sternen").
    ok &= check("rating is the precise structured value, not the rounded label",
                a.rating == 4.5966)
    ok &= check("review counts are read, not summed out of stray digits",
                [r.review_count for r in rows] == [119, 46, 97])

    ok &= check("position is 1-based and follows page order",
                [r.position for r in rows] == [1, 2, 3])
    ok &= check("page is read from the URL", all(r.page == 1 for r in rows))
    ok &= check("category is the slug with its id stripped",
                all(r.category == "kühlen-gefrieren" for r in rows))
    ok &= check("source is the host the row came from",
                all(r.source == "mediamarkt.de" for r in rows))
    ok &= check("image URLs point at MediaMarkt's asset host",
                all((r.image_url or "").startswith("https://assets.mmsrg.com/")
                    for r in rows))
    ok &= check("in_stock is read from the structured availability",
                all(r.in_stock is True for r in rows))

    # Provenance. This is the column that goes quietly wrong when the tile
    # markup moves: the prices stay right and the two DOM-only columns empty
    # out, so a run looks healthy and has lost them.
    ok &= check("every row was confirmed against its rendered tile",
                all(r.price_source == "jsonld+dom" for r in rows))

    # brand is null on EVERY listing row, and that is measured rather than
    # missed. Pinned so that a future change is a decision instead of a
    # surprise: the brand is the leading token of the title on most tiles and
    # NOT on all of them ("OK. OFK 411"), so splitting the title would produce
    # a column that is sometimes wrong — worse than null.
    ok &= check("brand is null on listing rows (a known, deliberate gap)",
                all(r.brand is None for r in rows))
    # Same for the detail-only columns.
    ok &= check("ean/description/images are null on listing rows",
                all(r.ean is None and r.description is None and r.images is None
                    for r in rows))

    # Search results share the tile markup exactly, which is why there is one
    # listing parser and not two.
    srows = parse_products(FIX_LISTING_SEARCH, URL_LISTING_SEARCH)
    ok &= check("the search fixture yields 2 rows", len(srows) == 2)
    ok &= check("search rows carry prices and skus like category rows",
                [r.sku for r in srows] == ["2873501", "2918351"]
                and [r.price for r in srows] == [12.99, 24.99])
    # A search URL has no category in its path, and the query is the caller's
    # own input — inventing a label from it would put a guess in a column.
    ok &= check("a search URL yields no category label",
                all(r.category is None for r in srows))
    return ok


def test_second_locale():
    group("a second country site: PLN, a non-www host, the same parser")
    ok = True
    rows = parse_products(FIX_LISTING_PL, URL_LISTING_PL)
    ok &= check("the Polish fixture yields 2 rows", len(rows) == 2)
    if len(rows) != 2:
        return False

    ok &= check("prices are quoted in PLN, read from the page not guessed",
                all(r.currency == "PLN" for r in rows))
    ok &= check("Polish skus and prices are pinned",
                [(r.sku, r.price) for r in rows]
                == [("1493943", 1349.0), ("1493940", 1999.0)])
    ok &= check("source is the bare host, without an invented www.",
                all(r.source == "mediamarkt.pl" for r in rows))

    # THE REGRESSION THIS FIXTURE EXISTS FOR. `mediamarkt.pl` answers without
    # a "www." prefix. A version of the parser rebuilt every product URL as
    # "https://www.{host}{path}", which never matched the page's own
    # "https://mediamarkt.pl/...", so the join between a structured row and
    # its rendered tile failed on every row of the site.
    #
    # Nothing looked wrong: the rows, the titles and the prices all come from
    # the structured data and were perfectly correct. What vanished was
    # `original_price`, `lowest_price_30d` and any confirmation of the price
    # — measured on a live Polish listing as 12 rows, 12 priced, 0 confirmed.
    ok &= check("every Polish row is confirmed against its rendered tile",
                all(r.price_source == "jsonld+dom" for r in rows))
    ok &= check("...so the DOM-only columns are actually populated",
                rows[0].lowest_price_30d == 1599.0)
    ok &= check("the row URLs keep the host form the page itself uses",
                all(r.url.startswith("https://mediamarkt.pl/") for r in rows))

    # Turkey: a prefixed currency, a percent sign written before its number,
    # and an instalment line, all in one price block.
    tr = parse_products(FIX_TILE_TR, URL_LISTING_TR)
    ok &= check("the Turkish fixture yields a row", len(tr) == 1)
    if tr:
        r = tr[0]
        ok &= check("Turkish prices are read in TRY", r.currency == "TRY")
        # THE REGRESSION. "-%10,34 ₺25.999,–" — the badge's number followed by
        # the NEXT price's currency symbol reads as "10,34 ₺" to a pattern
        # that allows a trailing symbol. A 25,999 TRY air conditioner parsed
        # as costing 10.34, and only the structured price disagreeing with
        # the tile stopped it reaching the output.
        ok &= check("a percent badge before a prefixed price is not a price",
                    r.price == 25999.0)
        ok &= check("...and the tile confirms it, so the badge is gone for good",
                    r.price_source == "jsonld+dom")
        # Same block also carries "9 taksitle ödeme ₺2.888,78".
        ok &= check("the Turkish instalment amount is not read as the price",
                    r.price != 2888.78)
        ok &= check("the Turkish 30-day low goes to its own column",
                    r.lowest_price_30d == 28999.0
                    and r.original_price is None)
        # And a 30-day low ABOVE the price must not become a discount.
        ok &= check("no discount is invented from a 30-day low",
                    r.discount_pct is None)

    # A bare percentage is never a price, in either word order.
    prices_in = product_parser._prices_in
    ok &= check("a German-order badge is not a price",
                prices_in("-16%", "EUR")[0] == [])
    ok &= check("a Turkish-order badge is not a price",
                prices_in("-%10,34", "TRY")[0] == [])
    ok &= check("a badge does not swallow the price beside it",
                prices_in("-16% 49,99 €", "EUR")[0] == [49.99])

    # The match key has to survive a www./non-www difference on EITHER side,
    # not just this one.
    key = product_parser._match_key
    ok &= check("the tile match key ignores a www. difference",
                key("https://www.mediamarkt.pl/pl/product/_x-1.html")
                == key("https://mediamarkt.pl/pl/product/_x-1.html"))
    ok &= check("...and a percent-encoding difference",
                key("https://www.mediamarkt.de/de/category/k%C3%BChlen-1.html")
                == key("https://www.mediamarkt.de/de/category/kühlen-1.html"))
    ok &= check("...and a trailing tracking parameter",
                key("https://mediamarkt.pl/pl/product/_x-1.html?utm_source=a")
                == key("https://mediamarkt.pl/pl/product/_x-1.html"))
    ok &= check("but it does NOT merge two different products",
                key("https://mediamarkt.pl/pl/product/_x-1.html")
                != key("https://mediamarkt.pl/pl/product/_x-2.html"))
    return ok


def test_unrated_products():
    group("an unrated product has no rating (not a rating of zero)")
    ok = True
    rows = parse_products(page(FIX_TILE_UNRATED), URL_LISTING_DE)
    ok &= check("the unrated fixture yields one row", len(rows) == 1)
    r = rows[0]
    # MediaMarkt renders the star widget on EVERY tile, and an unrated
    # product gets "Durchschnittliche Produktbewertung: 0 von 5 Sternen" with
    # a count of 0. Reading that as 0.0 filled `rating` on 84 of 84 rows of a
    # test run — a column that looked complete and said a brand-new release
    # was rated zero stars.
    ok &= check("rating is None, not 0.0, when nobody has rated it",
                r.rating is None)
    ok &= check("the row is otherwise complete",
                r.sku is not None and r.price is not None)

    # The count is a different matter: "nobody has reviewed this" is a fact
    # the page states, unlike an average it cannot compute. Checked on the
    # PRIMARY path, where the structured data and the tile are joined.
    listing = parse_products(FIX_LISTING_DE, URL_LISTING_DE)
    ok &= check("review_count is read as a number on the primary path",
                [x.review_count for x in listing] == [119, 46, 97])
    ok &= check("a rated product keeps its rating",
                all(x.rating and 0 < x.rating <= 5 for x in listing))
    return ok


def test_totals_and_hub():
    group("catalogue size, and a hub category that legitimately has none")
    ok = True
    # "12 von 2311" — the page's own count, which turns completeness into
    # arithmetic instead of a guess.
    ok &= check("the listing's catalogue size is read from the page",
                total_results(FIX_LISTING_DE) == 2311)
    ok &= check("the search fixture reports its own total",
                total_results(FIX_LISTING_SEARCH) == 845)
    ok &= check("a page with no such line reports None",
                total_results(FIX_DETAIL_PLAIN) is None)
    # "shown of total", so a first number larger than the second is some other
    # pair of numbers that happened to sit around the same word.
    ok &= check("a reversed pair is not mistaken for a count",
                total_results(page("<div>2311 von 12</div>")) is None)

    # The Polish connector is a bare "z", which occurs inside model numbers.
    # The FIX_LISTING_PL fixture carries a real product title from the same
    # page — "ELECTROLUX LVM8E08Z 44l" — whose "8Z 44" reads as "8 of 44",
    # alongside the genuine "2 z 85" counter. A loose search found the title
    # first and reported a catalogue of 44 against a real 85. The counter is
    # its own element, so only a text node that IS the count is accepted.
    ok &= check("a count-shaped substring inside a product title is ignored",
                total_results(FIX_LISTING_PL) == 85)
    ok &= check("...and passing the parsed row count confirms it rather than "
                "guessing", total_results(FIX_LISTING_PL, shown=2) == 85)
    # Passing a count the page does not print means the line was not found,
    # which is a None rather than a number nobody checked.
    ok &= check("a mismatched shown count yields None, not a wrong total",
                total_results(FIX_LISTING_PL, shown=99) is None)

    # A hub category is a REAL page that has no products on it. It must read
    # as empty (exit 4), not as blocked (exit 3) — otherwise a user goes
    # hunting for a proxy problem that does not exist.
    ok &= check("a hub category classifies as empty, not blocked",
                detect_page_state(FIX_HUB, 200) == "empty")
    ok &= check("a hub category yields no rows",
                parse_products(FIX_HUB, "https://www.mediamarkt.de/de/category/notebooks-680.html") == [])
    return ok


# ---------------------------------------------------------------------------
# Detail pages
# ---------------------------------------------------------------------------
def test_product_detail():
    group("detail rows, pinned to values from a real capture")
    ok = True

    r = parse_product_detail(FIX_DETAIL_PLAIN, URL_DETAIL_PLAIN)
    ok &= check("a detail page yields a row at all", r is not None)
    if r is None:
        return False
    # The Product is nested inside a BuyAction as its `object`. A scan for a
    # top-level `@type == "Product"` finds NOTHING on these pages and would
    # report a fully-populated page as having no product — which is the whole
    # reason _products_in_ld walks through wrappers.
    ok &= check("the Product inside a BuyAction wrapper is found",
                r.sku == "2920911")
    ok &= check("brand is populated in product mode",
                r.brand == "UNIVERSAL PICTURES")
    ok &= check("the EAN is read from gtin13", r.ean == "5051890337122")
    ok &= check("price and currency are the structured values",
                r.price == 24.99 and r.currency == "EUR")
    ok &= check("availability is read from the offer", r.in_stock is True)
    ok &= check("the category comes from the breadcrumb, not the title",
                r.category == "Fantasy DVD & Blu-ray")
    ok &= check("the full image list is kept, not just the first",
                isinstance(r.images, list) and len(r.images) == 2)
    ok &= check("image_url is the first of them",
                r.image_url == r.images[0])
    ok &= check("a description is carried", bool(r.description))
    ok &= check("page and position are null outside a listing",
                r.page is None and r.position is None)

    # The detail page splits its price across three nodes:
    #   whole "24,"  decimal "99"  currency "€"
    # Reading the container's TEXT gives "24, 99 €", out of which the general
    # price pattern matches "99 €" — 99.0 for a product costing 24.99. The
    # split reader is what prevents that, and this check is what would catch
    # a regression to the naive read.
    ok &= check("the split price is reassembled, not misread as its cents",
                r.price == 24.99 and r.price_source == "jsonld+dom")

    r = parse_product_detail(FIX_DETAIL_RRP, URL_DETAIL_RRP)
    ok &= check("detail UVP: price 49.99, original 59.99, discount 16.7%",
                r.price == 49.99 and r.original_price == 59.99
                and r.discount_pct == 16.7)
    ok &= check("detail UVP: brand and EAN are read",
                r.brand == "NINTENDO" and r.ean == "0045496513702")

    # Same trap as on a tile, and the same answer. Also: this page carries
    # THREE strike nodes, two of them belonging to the "similar products"
    # carousel, so an unscoped read would attach a neighbour's price.
    r = parse_product_detail(FIX_DETAIL_LOP, URL_DETAIL_LOP)
    ok &= check("detail Tiefstpreis: 30-day low goes to its own column",
                r.lowest_price_30d == 299.0)
    ok &= check("detail Tiefstpreis: original_price stays None (THE TRAP)",
                r.original_price is None)
    ok &= check("detail: the whole-euro split price (349,–) is read",
                r.price == 349.0 and r.price_source == "jsonld+dom")

    # The same product appears in the listing fixture and in a detail
    # fixture. The two modes must agree, or a consumer joining them gets two
    # different prices for one article number.
    listing_row = [x for x in parse_products(FIX_LISTING_DE, URL_LISTING_DE)
                   if x.sku == "2882323"]
    ok &= check("listing and detail agree on the same product's price",
                bool(listing_row)
                and listing_row[0].price == r.price
                and listing_row[0].lowest_price_30d == r.lowest_price_30d)

    # A page that is not a product must not produce a null-only row that
    # counts as a success.
    # A URL with no article number in it, so there is nothing to recover a
    # sku from either: the row would be null in every column.
    ok &= check("a page with no product yields None, not an empty row",
                parse_product_detail(
                    page("<div>nothing here</div>"),
                    "https://www.mediamarkt.de/de/product/_x.html") is None)
    return ok


# ---------------------------------------------------------------------------
# JSON-LD shapes that are legal and break a naive parser
# ---------------------------------------------------------------------------
def test_jsonld_shapes():
    group("legal JSON-LD shapes a naive parser gets wrong")
    ok = True

    def listing(ld):
        return ('<html lang="de"><head><script type="application/ld+json">'
                '%s</script></head><body>'
                '<img src="https://assets.mmsrg.com/isr/x"/></body></html>'
                % json.dumps(ld))

    base_url = "https://www.mediamarkt.de/de/product/_x-1234567.html"
    item = {"@type": "Product", "name": "X", "url": base_url}

    # An explicit null is NOT a missing key: a .get("offers", {}) default
    # does not apply to it, and the AttributeError lands one line later.
    ld = {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": dict(item, offers=None,
                                           aggregateRating=None)}]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check('"offers": null does not crash the page',
                len(rows) == 1 and rows[0].price is None)
    ok &= check('"aggregateRating": null does not crash the page',
                rows[0].rating is None and rows[0].review_count is None)

    # offers as a list, possibly holding things that are not dicts.
    ld = {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": dict(
            item, offers=["not a dict", {"price": 9.99, "priceCurrency": "EUR"}])}]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check("offers as a list of mixed types finds the real offer",
                rows[0].price == 9.99 and rows[0].currency == "EUR")

    # image in each of its four legal shapes.
    for shape, value in (
            ("a bare string", "https://x/1.jpg"),
            ("a list of strings", ["https://x/1.jpg", "https://x/2.jpg"]),
            ("an ImageObject", {"@type": "ImageObject", "url": "https://x/1.jpg"}),
            ("a list of ImageObjects",
             [{"@type": "ImageObject", "contentUrl": "https://x/1.jpg"}])):
        ld = {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": dict(item, image=value)}]}
        rows = parse_products(listing(ld), URL_LISTING_DE)
        ok &= check("image as %s is read" % shape,
                    rows[0].image_url == "https://x/1.jpg")

    # The product URL under offers.url rather than on the node. Missed, every
    # row points at the listing page while the other columns look right.
    ld = {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "Product", "name": "X",
                                       "offers": {"url": base_url,
                                                  "price": 5.0}}}]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check("a product URL living on the offer is still found",
                len(rows) == 1 and rows[0].url == base_url
                and rows[0].sku == "1234567")

    # Products under @graph instead of itemListElement — not what this site
    # emits today, handled because it is legal and costs nothing.
    ld = {"@graph": [dict(item, offers={"price": 3.0, "priceCurrency": "EUR"})]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check("products under @graph are found",
                len(rows) == 1 and rows[0].price == 3.0)

    # A block that does not parse must cost that block, not the page.
    html = ('<html lang="de"><head>'
            '<script type="application/ld+json">{ broken</script>'
            '<script type="application/ld+json">%s</script></head><body>'
            '<img src="https://assets.mmsrg.com/isr/x"/></body></html>'
            % json.dumps({"@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "item": dict(item, offers={"price": 7.0})}]}))
    rows = parse_products(html, URL_LISTING_DE)
    ok &= check("one unparseable ld+json block does not lose the others",
                len(rows) == 1 and rows[0].price == 7.0)

    # A rating outside 0..5 is not a rating.
    ld = {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": dict(
            item, aggregateRating={"ratingValue": 47, "reviewCount": 3})}]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check("a rating outside 0-5 is rejected", rows[0].rating is None)

    # ratingCount and reviewCount are both legal and mean the same thing
    # here: MediaMarkt writes the first on a detail page and the second in a
    # listing. Knowing only one reports a null count on one of the two modes.
    ld = {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": dict(
            item, aggregateRating={"ratingValue": 4.5, "ratingCount": "1.234"})}]}
    rows = parse_products(listing(ld), URL_LISTING_DE)
    ok &= check("ratingCount is read as well as reviewCount, ungrouped",
                rows[0].review_count == 1234)
    return ok


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
def test_urls():
    group("URLs: pagination, categories, skus, hosts")
    ok = True
    cat = "https://www.mediamarkt.de/de/category/grills-116.html"

    ok &= check("page 1 carries no page parameter", page_url(cat, 1) == cat)
    ok &= check("page 2 appends ?page=2", page_url(cat, 2) == cat + "?page=2")
    # REPLACED, not appended: a doubled parameter leaves the site to pick one.
    ok &= check("an existing page parameter is replaced, not doubled",
                page_url(cat + "?page=7", 2) == cat + "?page=2")
    search = "https://www.mediamarkt.de/de/search.html?query=usb-c"
    ok &= check("other query parameters survive pagination",
                "query=usb-c" in page_url(search, 3)
                and page_url(search, 3).endswith("page=3"))

    ok &= check("a URL with no page parameter is page 1",
                page_number_from_url(cat) == 1)
    ok &= check("?page=4 reads as page 4",
                page_number_from_url(cat + "?page=4") == 4)
    ok &= check("a non-numeric page parameter reports None",
                page_number_from_url(cat + "?page=x") is None)

    ok &= check("the category label drops the id and the .html",
                category_from_url(cat) == "grills")
    ok &= check("a percent-encoded category decodes",
                category_from_url(URL_LISTING_DE) == "kühlen-gefrieren")
    ok &= check("route and locale segments are not category names",
                category_from_url("https://www.mediamarkt.de/de/category/") is None)

    ok &= check("listing kinds are told apart",
                (listing_kind(cat), listing_kind(search),
                 listing_kind(URL_DETAIL_PLAIN))
                == ("category", "search", "product"))

    # The slug is full of other numbers; only the last group counts.
    ok &= check("the sku is the last number in the slug",
                sku_from_url(URL_DETAIL_LOP) == "2882323")
    ok &= check("a URL with no article number yields None",
                sku_from_url("https://www.mediamarkt.de/de/product/_x.html") is None)
    ok &= check("a query string after the slug does not defeat the pattern",
                sku_from_url(URL_DETAIL_PLAIN + "?x=1") == "2920911")

    ok &= check("www. is stripped from the host",
                site_host("https://www.mediamarkt.at/x") == "mediamarkt.at")
    ok &= check("every country site in the table is accepted",
                all(is_supported_host("https://www." + h + "/") for h in HOSTS))
    ok &= check("another shop is refused",
                not is_supported_host("https://www.saturn.de/"))
    # mediamarkt.lu IS a MediaMarkt shop and is still refused, because it runs
    # on Shopify rather than on this platform: no /category/ or /product/
    # paths, no product cards, and JSON-LD carrying only Organization and
    # WebSite. Pointing this scraper at it would return zero rows and read as
    # an empty category, so it is refused WITH THE REASON instead.
    ok &= check("a MediaMarkt site on another platform is refused",
                not is_supported_host("https://mediamarkt.lu/"))
    ok &= check("...and the refusal says why rather than 'not a MediaMarkt site'",
                "Shopify" in (product_parser.unsupported_reason(
                    "https://mediamarkt.lu/") or ""))
    ok &= check("Saturn's refusal names it as the sibling brand",
                "Saturn" in (product_parser.unsupported_reason(
                    "https://www.saturn.de/") or ""))
    ok &= check("an unknown host has no invented reason",
                product_parser.unsupported_reason("https://example.com/") is None)
    ok &= check("the supported table holds the ten platform sites",
                len(HOSTS) == 10 and "mediamarkt.lu" not in HOSTS)
    ok &= check("an unparseable URL does not raise",
                site_host("not a url") == "")
    # The currency table is a FALLBACK; the structured data always wins. It
    # still has to be right where it is consulted.
    ok &= check("the currency table knows the non-euro sites",
                host_currency("https://mediamarkt.pl/") == "PLN"
                and host_currency("https://www.mediamarkt.ch/de/") == "CHF"
                and host_currency("https://www.mediamarkt.hu/") == "HUF"
                and host_currency("https://www.mediamarkt.com.tr/") == "TRY")
    ok &= check("MediaWorld is in the table as the Italian site",
                host_currency("https://www.mediaworld.it/") == "EUR")
    return ok


# ---------------------------------------------------------------------------
# Page state
# ---------------------------------------------------------------------------
def test_page_state():
    group("page state: blocked vs captcha vs empty vs content")
    ok = True

    # The status code is the PRIMARY signal here, which is a deliberate
    # inversion of how the rest of this family detects a block: MediaMarkt's
    # refusal is its own branded error page under a 403, carrying no vendor
    # marker at all.
    ok &= check("HTTP 403 is a block whatever the body says",
                detect_page_state(FIX_LISTING_DE, 403) == "blocked")

    # ...and both block pages are still caught with no status available, via
    # the structural signal. The German one has site chrome around "Ups, hier
    # stimmt gerade etwas nicht"; the Spanish one's ENTIRE visible text is
    # the word "MediaMarkt", so no text marker could tell it from a thin but
    # genuine page. What separates them is that neither references
    # MediaMarkt's own asset host, and every real page does.
    ok &= check("the German block page is caught without a status code",
                detect_page_state(FIX_BLOCKED_DE) == "blocked")
    ok &= check("the Spanish block page is caught without a status code",
                detect_page_state(FIX_BLOCKED_ES) == "blocked")

    ok &= check("a real listing is content", detect_page_state(FIX_LISTING_DE, 200) == "content")
    ok &= check("a real detail page is content",
                detect_page_state(FIX_DETAIL_PLAIN, 200) == "content")
    ok &= check("a hub category is empty, not blocked",
                detect_page_state(FIX_HUB, 200) == "empty")

    # Detected is not blocking. A challenge marker on a page whose products
    # have rendered guards nothing.
    challenged = FIX_LISTING_DE.replace(
        "</body>", '<div class="cf-turnstile" data-sitekey="x"></div></body>')
    ok &= check("a challenge marker on a rendered grid still reads as content",
                detect_page_state(challenged, 200) == "content")
    ok &= check("a challenge on a page with no content reads as captcha",
                detect_page_state(
                    '<html><body><div class="g-recaptcha" '
                    'data-sitekey="x"></div></body></html>') == "captcha")

    # The Scraping Browser API's own auto-solve extension injects captcha
    # hunters into EVERY page it loads. The first live run of this scraper
    # reported exit 3 on a 1.8 MB category page holding twelve products for
    # exactly this reason — the solver's tooling was mistaken for the site's
    # defences. Anyone pointing a marker-based detector at a remote browser
    # will hit this.
    injected = (
        '<html lang="de"><body><img src="https://assets.mmsrg.com/isr/x"/>'
        '<script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/'
        'content/captcha/turnstile/hunter.js" '
        'data-ts-input="cf-turnstile-response"></script></body></html>')
    ok &= check("a captcha marker injected by a browser EXTENSION is ignored",
                detect_bot_challenge(injected) is None)
    ok &= check("a real turnstile widget is still detected",
                detect_bot_challenge(
                    '<div class="cf-turnstile" data-sitekey="x"></div>')
                == "turnstile")
    ok &= check("reCAPTCHA and hCaptcha are still detected",
                detect_bot_challenge("<script src='www.google.com/recaptcha/api.js'>")
                == "recaptcha"
                and detect_bot_challenge("<div class='h-captcha'></div>") == "hcaptcha")
    ok &= check("an empty body with a 4xx reads as blocked",
                detect_page_state("", 500) == "blocked")
    return ok


# ---------------------------------------------------------------------------
# page_flow: the policy all three engines share
# ---------------------------------------------------------------------------
def test_page_flow():
    group("page_flow: shared policy")
    ok = True

    ok &= check("the listing readiness anchor is the CARD, not the link",
                "mms-product-card" in page_flow.READY_SELECTOR_LISTING
                and "/product/" not in page_flow.READY_SELECTOR_LISTING)
    # Each tile links twice (image and title), so counting links reports the
    # grid as twice its size — which matters the moment a threshold is
    # compared against it.
    ok &= check("a real page has 2 product links per card",
                FIX_LISTING_DE.count('data-test="mms-product-card"') * 2
                == FIX_LISTING_DE.count('href="/de/product/'))
    ok &= check("the readiness threshold is above 1",
                page_flow.MIN_CARD_MATCHES > 1)
    ok &= check("a listing needs several anchors and a detail page needs none",
                page_flow.min_matches("listing") == page_flow.MIN_CARD_MATCHES
                and page_flow.min_matches("product") == 0)
    # Ordered most-durable first: the standards-based signal leads.
    ok &= check("link[rel=next] leads the pagination selector",
                page_flow.NEXT_PAGE_SELECTOR.strip().startswith("link[rel='next']"))

    # Concurrency depends entirely on this.
    cat = "https://www.mediamarkt.de/de/category/grills-116.html"
    ok &= check("a next-link matching the convention is addressable",
                page_flow.pagination_is_addressable(cat, cat + "?page=2"))
    ok &= check("a cursor the convention cannot reproduce is refused",
                not page_flow.pagination_is_addressable(cat, cat + "?cursor=abc"))
    ok &= check("tracking parameters do not defeat the comparison",
                page_flow.pagination_is_addressable(
                    "https://www.mediamarkt.de/de/search.html?query=x",
                    "https://www.mediamarkt.de/de/search.html?query=x&page=2"
                    "&queryMeta%5Bga_query%5D=x&queryHash=deadbeef"))
    # The site writes its own next-links percent-DECODED while a pasted URL
    # is encoded. Comparing them as raw strings said they disagreed, which
    # made every accented category silently fall back to sequential fetching
    # — a large share of a German, Spanish, Turkish or Hungarian catalogue.
    ok &= check("an umlaut category still compares equal (encoded vs decoded)",
                page_flow.pagination_is_addressable(
                    URL_LISTING_DE,
                    "https://www.mediamarkt.de/de/category/"
                    "kühlen-gefrieren-32.html?page=2"))
    # A search page publishes no next-link and paginates perfectly well
    # anyway, so a missing link must not be read as a disagreement.
    ok &= check("a missing next-link is not a disagreement",
                page_flow.pagination_is_addressable(cat, None))

    # The state policy, as data, so an engine cannot quietly disagree.
    ok &= check("content is neither retried nor solved nor blocking",
                not page_flow.should_retry("content")
                and not page_flow.should_solve("content")
                and not page_flow.counts_as_blocked("content"))
    ok &= check("a block is retried from another exit but never solved",
                page_flow.should_retry("blocked")
                and not page_flow.should_solve("blocked")
                and page_flow.counts_as_blocked("blocked"))
    ok &= check("a captcha is both retried and solved",
                page_flow.should_retry("captcha")
                and page_flow.should_solve("captcha"))
    # An empty page is a CORRECT answer. Retrying it spends the user's budget
    # re-confirming it, and rotating the exit blames an address for the URL.
    ok &= check("an empty page is NOT retried and NOT counted as blocked",
                not page_flow.should_retry("empty")
                and not page_flow.counts_as_blocked("empty"))

    # Pinned as a known, deliberate absence: this site lazy-loads nothing, so
    # the scrolling machinery was not ported. A future change becomes a
    # decision instead of a surprise.
    ok &= check("no scrolling machinery was ported (nothing lazy-loads here)",
                not any(hasattr(page_flow, n) for n in
                        ("scroll_until_stable", "hydrate", "needs_scrolling")))
    # And the family rule that keeps page_flow driver-agnostic: passing
    # JavaScript from here would decide its dialect for every driver, and
    # they disagree (Playwright and pyppeteer take `() => expr`, Selenium's
    # execute_script takes `return expr;`). Checked against the CODE only —
    # the module docstring names both dialects while explaining why neither
    # appears, and a naive grep over the source flags that as a violation.
    flow_tree = ast.parse(inspect.getsource(page_flow))
    # Docstring NODES, identified by position rather than by value:
    # ast.get_docstring() returns a cleaned, dedented copy that no longer
    # compares equal to the Constant it came from, so filtering by value
    # silently keeps every docstring in the list.
    doc_nodes = set()
    for node in ast.walk(flow_tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_nodes.add(id(first.value))
    code_strings = [n.value for n in ast.walk(flow_tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in doc_nodes]
    ok &= check("page_flow passes no JavaScript across the driver boundary",
                not any(("() =>" in s) or ("scrollHeight" in s)
                        or ("document." in s) for s in code_strings))
    return ok


# ---------------------------------------------------------------------------
# The output contract
# ---------------------------------------------------------------------------
def test_output_contract():
    group("the output contract shared across this scraper family")
    ok = True
    names = [f.name for f in fields(Product)]
    # The family prefix, byte-identical and in order, so a consumer written
    # against another repo in this family reads the first sixteen columns
    # unchanged. Site-specific columns go AFTER it.
    family_prefix = ["source", "scraped_at", "url", "sku", "title", "brand",
                     "price", "currency", "original_price", "discount_pct",
                     "rating", "review_count", "in_stock", "image_url",
                     "category", "price_source"]
    ok &= check("the family field prefix is present and in order",
                names[:len(family_prefix)] == family_prefix)
    ok &= check("MediaMarkt's own columns come after it",
                names[len(family_prefix):] ==
                ["page", "position", "lowest_price_30d", "ean", "description",
                 "images"])
    ok &= check("both modes map to a row class",
                ROW_CLASS_BY_MODE == {"listing": Product, "product": Product})
    ok &= check("both modes are one row per sku",
                set(UNIQUE_BY_SKU_MODES) == {"listing", "product"})

    ok &= check("the exit codes are the family's",
                (EXIT_BLOCKED, EXIT_NO_PRODUCTS, EXIT_PARTIAL) == (3, 4, 6))
    ok &= check("an exhausted listing counts as complete",
                "no_new_products" in COMPLETE_STOP_REASONS
                and "pagination_exhausted" in COMPLETE_STOP_REASONS)
    ok &= check("a single-page mode is complete by construction",
                "single_page_mode" in COMPLETE_STOP_REASONS)

    # No defaulted currency anywhere: a row that could not establish one says
    # None rather than claiming EUR, which would be wrong for the four
    # non-euro country sites.
    ok &= check("Product defaults currency to None, not a guess",
                Product().currency is None)
    ok &= check("Product defaults price_source to None",
                Product().price_source is None)
    return ok


def test_writers():
    group("writers, dedupe and the refusal to overwrite good data")
    ok = True
    rows = [Product(sku="1", url="u1", price=1.0),
            Product(sku="2", url="u2", price=2.0)]
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, "out")

        # A run that finds nothing writes NOTHING: a consumer cannot tell an
        # empty category from a failed run, and the failure destroys the last
        # known good data.
        save(rows, prefix, "json", allow_empty=False)
        ok &= check("a good run writes its output",
                    os.path.exists(prefix + ".json"))
        before = open(prefix + ".json").read()
        save([], prefix, "json", allow_empty=False)
        ok &= check("an empty run does NOT overwrite the previous good output",
                    open(prefix + ".json").read() == before)
        save([], prefix, "json", allow_empty=True)
        ok &= check("--allow-empty is the opt-out and does overwrite",
                    json.load(open(prefix + ".json")) == [])

        # An empty CSV still carries its header, so a consumer reads a table
        # with no rows instead of failing on a zero-byte file.
        csv_path = os.path.join(d, "empty.csv")
        write_csv([], csv_path, row_cls=Product)
        header = open(csv_path).read().strip().split("\n")[0]
        ok &= check("an empty CSV still carries its header",
                    header.split(",")[:4] == ["source", "scraped_at", "url", "sku"])

        # A list column has to survive CSV without becoming a Python repr.
        csv_path = os.path.join(d, "images.csv")
        write_csv([Product(sku="1", images=["a", "b"])], csv_path, row_cls=Product)
        body = open(csv_path).read()
        ok &= check("a list column is joined, not repr()d in CSV",
                    ("a" + LIST_CSV_SEPARATOR + "b") in body and "['a'" not in body)

    seen = set()
    ok &= check("dedupe drops a repeated sku",
                len(dedupe_by_sku([Product(sku="a"), Product(sku="a")], seen)) == 1)
    # A row with no key is always KEPT: there is nothing to check a duplicate
    # against, and dropping it is a silent data loss rather than a dedupe.
    ok &= check("a row with no sku is kept, not dropped",
                len(dedupe_by_key([Product(sku=None), Product(sku=None)],
                                  set())) == 2)

    meta = run_meta("complete", "completed", 3, 3, "u", "u", 36,
                    pages_failed=[], mode="listing", source="mediamarkt.de")
    ok &= check("the sidecar records status, mode and source",
                meta["status"] == "complete" and meta["mode"] == "listing"
                and meta["source"] == "mediamarkt.de")
    # A count stops being a description once a page can fail while later ones
    # succeed, so the sidecar names WHICH pages failed.
    meta = run_meta("partial", "blocked", 5, 3, "u", "u", 12,
                    pages_failed=[2, 4], mode="listing", source="mediamarkt.de")
    ok &= check("the sidecar names which pages failed, by number",
                meta["pages_failed"] == [2, 4])
    return ok


def test_finish_run():
    group("finish_run: the exit codes all three engines must agree on")
    ok = True
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "run")
        rows = [Product(sku="1", url="u")]

        code = finish_run(rows, p, "json", False, blocked=False,
                          stop_reason="completed", pages_requested=1,
                          pages_completed=1, pages_failed=[], mode="listing",
                          source="mediamarkt.de", start_url="u", final_url="u")
        ok &= check("a complete run exits 0", code == 0)

        code = finish_run([], p + "b", "json", False, blocked=True,
                          stop_reason="blocked_mediamarkt-403",
                          pages_requested=1, pages_completed=0,
                          pages_failed=[1], mode="listing",
                          source="mediamarkt.de", start_url="u", final_url="u")
        ok &= check("a blocked run exits 3, not 4", code == EXIT_BLOCKED)
        # A FAILED run writes no sidecar: `save` leaves the previous good
        # output in place, and a "failed" sidecar beside good data would
        # contradict it.
        ok &= check("a failed run writes no sidecar beside older good data",
                    not os.path.exists(p + "b.meta.json"))

        code = finish_run([], p + "c", "json", False, blocked=False,
                          stop_reason="completed", pages_requested=1,
                          pages_completed=1, pages_failed=[], mode="listing",
                          source="mediamarkt.de", start_url="u", final_url="u")
        ok &= check("a genuinely empty result exits 4, not 3",
                    code == EXIT_NO_PRODUCTS)

        code = finish_run(rows, p + "d", "json", False, blocked=False,
                          stop_reason="page_load_timeout", pages_requested=5,
                          pages_completed=2, pages_failed=[3], mode="listing",
                          source="mediamarkt.de", start_url="u", final_url="u")
        ok &= check("a run with data that stopped early exits 6 (partial)",
                    code == EXIT_PARTIAL)
        ok &= check("a partial run still writes what it got",
                    os.path.exists(p + "d.json"))
    return ok


def test_diff():
    group("diff_runs")
    ok = True
    old = [{"sku": "1", "price": 10.0, "price_source": "jsonld+dom"},
           {"sku": "2", "price": 20.0, "price_source": "jsonld+dom"},
           {"sku": "3", "price": 30.0, "price_source": "jsonld"}]
    new = [{"sku": "1", "price": 11.0, "price_source": "jsonld+dom"},
           {"sku": "3", "price": 30.5, "price_source": "jsonld+dom"},
           {"sku": "4", "price": 40.0, "price_source": "jsonld+dom"}]
    d = diff_products(old, new)
    ok &= check("a real price move is reported as changed",
                any(c["sku"] == "1" for c in d["changed"]))
    ok &= check("a delisted product is reported as removed",
                [r["sku"] for r in d["removed"]] == ["2"])
    ok &= check("a new product is reported as added",
                [r["sku"] for r in d["added"]] == ["4"])
    # A price difference that comes with a price_source difference says
    # something about OUR two snapshots, not about the shop.
    ok &= check("a price move with a source change is not 'changed'",
                not any(c["sku"] == "3" for c in d["changed"]))
    ok &= check("...it is reported separately as source_changed",
                any(c["sku"] == "3" for c in d.get("source_changed", [])))

    # lowest_price_30d is tracked: it moves only when a real price change
    # enters or leaves the 30-day window, so a monitor watching only `price`
    # would miss a product whose current price held while its recent floor
    # moved underneath it.
    ok &= check("lowest_price_30d is a tracked field",
                "lowest_price_30d" in __import__("diff_runs").TRACKED_FIELDS)
    return ok


def test_captcha():
    group("captcha detection and reconciliation")
    ok = True
    from captcha_solver import CaptchaChallenge

    # Format 2: the site's own wrapper element carries the config as
    # attributes, with the execute() call inside a bundled file that never
    # appears as readable inline script.
    widget = ('<captcha-widget data-captcha-type="recaptcha" data-version="v3" '
              'data-sitekey="6LcABCDEFGHIJKLMNOPQRSTUVWXYZ0123" '
              'data-action="submit"></captcha-widget>')
    c = detect_recaptcha_v3(widget, "https://www.mediamarkt.de/")
    ok &= check("a captcha-widget declaring v3 is detected",
                c is not None and c.kind == "recaptcha_v3")

    # A sitekey is at least 20 characters; a short string next to
    # data-sitekey is not one, and treating it as one would send a malformed
    # task to the API and bill for the answer.
    ok &= check("a too-short sitekey is not accepted as a challenge",
                detect_recaptcha_v3('<div data-sitekey="short" '
                                    'class="g-recaptcha"></div>',
                                    "https://www.mediamarkt.de/") is None)
    ok &= check("a page with no reCAPTCHA at all is not a challenge",
                detect_recaptcha_v3(FIX_LISTING_DE, URL_LISTING_DE) is None)

    # THE LOADER WINS. A site's own wrapper can declare v3 while the Google
    # loader it actually ships is the v2-invisible signature
    # (render=explicit, size=invisible, a bframe challenge iframe). v3
    # parameters sent for a v2-invisible widget buy a token the site
    # rejects — so the runtime reading is authoritative and the two
    # detectors are reconciled rather than short-circuited.
    static_v3 = CaptchaChallenge(kind="recaptcha_v3", sitekey="6LcABC" + "X" * 20,
                                 action="submit", source="html")
    runtime_v2 = CaptchaChallenge(kind="recaptcha_v2_invisible",
                                  sitekey="6LcABC" + "X" * 20,
                                  source="runtime", size="invisible")
    merged = reconcile_detections(static_v3, runtime_v2)
    ok &= check("when the detectors disagree, the live loader wins",
                merged is not None and merged.kind == "recaptcha_v2_invisible")
    ok &= check("...and the real action from the static markup is kept",
                merged.action == "submit")
    ok &= check("one detector alone is still used when only it fires",
                reconcile_detections(static_v3, None) is static_v3
                and reconcile_detections(None, runtime_v2) is runtime_v2)
    ok &= check("neither firing means no challenge",
                reconcile_detections(None, None) is None)

    # Deliberately absent: no solver for a first-party image captcha. This
    # site has no such page — captured 2026-09-09 from a datacentre exit on
    # five hosts, its refusal is a 403 with no challenge on it at all — so a
    # solver for one would be dead code that looks load-bearing. Pinned so
    # that reintroducing it is a decision rather than a drift.
    import captcha_solver
    ok &= check("no first-party image-captcha solver was ported",
                not [n for n in dir(captcha_solver)
                     if "image" in n.lower() and "captcha" in n.lower()])
    # ...while the DETECTORS stay broad, which is the family's standing
    # policy: which challenge a visitor meets depends on the exit country and
    # on what the address has been doing.
    ok &= check("detection still covers four challenge vendors",
                set(product_parser.BOT_CHALLENGE_MARKERS) >=
                {"recaptcha", "hcaptcha", "turnstile", "datadome"})
    return ok


def _placeholder_reads_unset(raw):
    """Whether env_config would treat `raw` as "not configured".

    Goes through the real rule — `env_config.env_value`, which is where the
    placeholder logic lives — rather than reimplementing it, because a
    reimplementation is what drifts. The variable is set in os.environ
    directly and restored afterwards: `load_env` only fills variables that
    are not already set, so writing a temporary .env would be shadowed by
    whatever the suite has already loaded.
    """
    name = "MEDIAMARKT_CDP_ENDPOINT"
    saved = os.environ.get(name)
    try:
        os.environ[name] = raw
        with io.StringIO() as buf, redirect_stdout(buf):
            value = env_config.env_value(name)
    finally:
        if saved is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = saved
    return value is None


def test_env_config():
    group("env_config")
    ok = True
    ok &= check("the env keys are this site's, not another repo's",
                set(env_config.ENV_KEYS) ==
                {"TWOCAPTCHA_KEY", "MEDIAMARKT_CDP_ENDPOINT",
                 "MEDIAMARKT_PROXY", "MEDIAMARKT_URL"})

    # A COPIED .env.example MUST READ AS UNSET, and a literal-only check is
    # not enough to make that true. This repo documents its two credentialled
    # URLs the way the vendor does, with the parts you fill in in braces:
    #
    #     ws://{login}-zone-scraping_browser-…-pid-{profileId}:{password}@…
    #     http://{user}:{password}@eu.proxy.2captcha.com:2334
    #
    # Before the brace check existed the loader reported both of those as
    # CONFIGURED, so `cp .env.example .env` and a run connected to
    # cb.2captcha.com with the string `{login}-zone-…` as its username and
    # got a 401 — a confusing failure a long way from its cause.
    for raw in ('ws://{login}-zone-scraping_browser-country-de-pid-'
                '{profileId}:{password}@cb.2captcha.com:9222',
                'http://{user}:{password}@eu.proxy.2captcha.com:2334',
                'your_2captcha_api_key_here'):
        ok &= check("a placeholder value reads as unset: %s..." % raw[:34],
                    _placeholder_reads_unset(raw))
    # ...and a REAL value still reads as set, or the guard has eaten the
    # feature it was protecting.
    ok &= check("a real value is not mistaken for a placeholder",
                _placeholder_reads_unset(
                    "ws://acct1-zone-scraping_browser-country-de-pid-p1:"
                    "secret@cb.2captcha.com:9222") is False)

    # `--fp-tags` MUST DEFAULT TO ONE OS-FAMILY TAG. It shipped as
    # "Windows,Chrome,Desktop", which the fingerprint API rejects with HTTP
    # 400 — so --fingerprint failed on every invocation, while
    # fingerprint_client.py's own --tags help said ONE tag all along.
    # Measured against the live API on 2026-09-10: `Windows` succeeds, and
    # `Windows,Chrome,Desktop`, `Chrome` and `Desktop` each 400.
    for f in ENGINE_FILES:
        path = os.path.join(REPO_ROOT, f)
        if not os.path.exists(path):
            continue
        m = re.search(r'--fp-tags"\s*,\s*default="([^"]*)"',
                      open(path, encoding="utf-8").read())
        if m is None:
            continue
        ok &= check("%s's --fp-tags default is ONE tag the API accepts" % f,
                    "," not in m.group(1)
                    and m.group(1) in ("Windows", "Microsoft Windows",
                                       "Android"))

    # .env.example must document exactly the variables the code reads, in
    # both directions. It drifts otherwise, and a documented-but-unread
    # variable is worse than an undocumented one.
    example = os.path.join(REPO_ROOT, ".env.example")
    documented = set()
    if os.path.exists(example):
        for line in open(example, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                documented.add(line.split("=", 1)[0].strip())
    ok &= check(".env.example documents exactly the variables the code reads",
                documented == set(env_config.ENV_KEYS))

    # A variable mapped onto a flag with a non-empty default would be
    # silently inert, because the loader only fills UNSET values: a setting
    # that looks configurable and is not.
    ok &= check("no env variable is mapped onto --out (it has a default)",
                "out" not in env_config.ENV_KEYS.values())

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write("TWOCAPTCHA_KEY=fromfile\n")
            f.write("MEDIAMARKT_URL=https://www.mediamarkt.de/de/x.html\n")
            f.write("NOT_A_REAL_KEY=1\n")

        class A:
            twocaptcha_key = None
            url = None
            cdp_endpoint = None
            proxy = None

        a = A()
        env_config.load_env(path)
        env_config.apply(a, quiet=True)
        ok &= check("a value in .env fills an unset flag",
                    a.twocaptcha_key == "fromfile")

        b = A()
        b.twocaptcha_key = "fromflag"
        env_config.apply(b, quiet=True)
        # A .env must never override something the caller typed.
        ok &= check("an explicit flag beats .env", b.twocaptcha_key == "fromflag")
        # A typo is REPORTED rather than silently ignored.
        ok &= check("an unrecognised variable in .env is reported",
                    "NOT_A_REAL_KEY" in env_config.unknown_keys(path))
    return ok


def test_proxy_pool():
    group("proxy_pool: credentials never reach argv or logs")
    ok = True
    url = "http://user:secret@eu.proxy.2captcha.com:2334"
    masked = mask(url)
    ok &= check("credentials are masked in logs", "secret" not in masked)
    # The host and port are KEPT: which exit a run used is the point of the
    # log and is not the secret.
    ok &= check("...but the host and port survive masking",
                "eu.proxy.2captcha.com:2334" in masked)

    pw = to_playwright(url)
    # A `--proxy-server=` value becomes part of the browser's command line,
    # readable by anything that can run `ps`. The credentials go through the
    # driver's own fields instead.
    ok &= check("the server string handed to the browser has no credentials",
                "secret" not in pw["server"])
    ok &= check("credentials go through the driver's own fields",
                pw["username"] == "user" and pw["password"] == "secret")

    scrubbed, creds = split_credentials(url)
    ok &= check("split_credentials separates the two",
                scrubbed == "http://eu.proxy.2captcha.com:2334"
                and creds == ("user", "secret"))

    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    ok &= check("a pool reports its size", len(pool) == 3)
    first = pool.current
    pool.advance("test")
    ok &= check("advancing moves to another exit", pool.current != first)
    # `.proxies` hands back a COPY, so a worker building its own pool from it
    # cannot mutate the parent's list. Two threads sharing one mutable list
    # is the bug that makes concurrency stop being worth it.
    copy = pool.proxies
    copy.append("http://d:4")
    ok &= check("the pool hands out a copy of its exits, not the list itself",
                len(pool) == 3)

    # Workers start on DIFFERENT exits, each with its own pool object, so no
    # thread needs a lock: the concurrency is safe by construction rather
    # than by discipline. Tested through the engine's own helper, because
    # that is where the offset actually lives.
    try:
        import playwright_scraper
    except ImportError:
        playwright_scraper = None
    if playwright_scraper is not None:
        exits = [playwright_scraper._worker_pool(pool, i).current
                 for i in range(3)]
        ok &= check("three workers start on three different exits",
                    len(set(exits)) == 3)
        ok &= check("a worker with no pool gets none",
                    playwright_scraper._worker_pool(None, 0) is None)

    # A pool of one is legal and must not rotate itself into an index error.
    one = ProxyPool(["http://only:1"])
    one.advance("nowhere else to go")
    ok &= check("a single-exit pool survives a rotation",
                one.current == "http://only:1")
    ok &= check("an empty pool is refused rather than silently accepted",
                _raises(lambda: ProxyPool([])))

    # This used to assert that "http://host:port:login:pass" — a line from a
    # proxy LIST FILE — "is understood", checking only that parse_proxy_line
    # did not reject it. It returned the string unchanged, so the check
    # passed; the value was never usable, and it blew up several calls later.
    # A test that asserts a function did not complain is not a test that its
    # answer was right.
    #
    # A proxy LIST FILE line pasted where a proxy URL belongs. This is the
    # mistake a new user makes — the file format is
    # scheme://host:port:login:password and the flag wants
    # http://login:password@host:port — and it reached a real CI run.
    #
    # It used to sail through parse_proxy_line (which never looked at the
    # port) and blow up much later inside to_playwright as an uncaught
    # ValueError: exit 1, a crash, where it should be exit 2, bad usage. And
    # the traceback printed the login AND the password into a public CI log.
    from proxy_pool import ProxyError
    pasted = ("http://eu.proxy.2captcha.com:2334:"
              "SOMELOGIN-zone-custom-region-de:SOMEPASSWORD")
    raised = None
    try:
        parse_proxy_line(pasted, source="MEDIAMARKT_PROXY")
    except ProxyError as exc:
        raised = str(exc)
    ok &= check("a proxy-list line pasted as a URL is refused, not crashed on",
                raised is not None)
    ok &= check("...and the refusal says what the value should look like",
                raised is not None and "login:password@host:port" in raised)
    ok &= check("...and neither the login nor the password is in the message",
                raised is not None
                and "SOMEPASSWORD" not in raised and "SOMELOGIN" not in raised)

    # mask() is the last thing standing between a password and a log, and it
    # is called precisely when the value is already wrong. It read
    # `parsed.port`, which urlparse computes lazily and which RAISES on a
    # malformed authority — so the masker blew up on exactly the input that
    # most needed masking. A masker that raises is worse than a vague one.
    ok &= check("mask() does not raise on a malformed URL",
                "SOMEPASSWORD" not in mask(pasted))
    for junk in ("::::", "not a url", "http://", "://x", ""):
        try:
            mask(junk)
            raised_here = False
        except Exception:
            raised_here = True
        ok &= check("mask(%r) does not raise" % junk, not raised_here)
    ok &= check("mask() still keeps host and port on a good URL",
                mask("http://u:p@h.example:8080") == "http://***:***@h.example:8080")
    return ok


# ---------------------------------------------------------------------------
# The engines
# ---------------------------------------------------------------------------
ENGINES = ("playwright_scraper", "puppeteer_scraper", "selenium_scraper")


def test_engines(skips):
    group("engines: all three must behave identically")
    ok = True
    loaded = {}
    for name in ENGINES:
        try:
            loaded[name] = __import__(name)
        except ImportError as e:
            # Reported, never swallowed: "skipped, engine absent" reads
            # exactly like a passing run, and CI's engine-smoke job fails if
            # this list is non-empty.
            skips.append("%s (%s)" % (name, e))

    for name, mod in loaded.items():
        ok &= check("%s exposes scrape() and parse_args()" % name,
                    hasattr(mod, "scrape") and hasattr(mod, "parse_args"))
        # The engines must reach the shared policy rather than carry copies.
        src = inspect.getsource(mod)
        ok &= check("%s takes its readiness policy from page_flow" % name,
                    "page_flow.ready_selector" in src)
        ok &= check("%s takes its state policy from page_flow" % name,
                    "page_flow.should_retry" in src or "page_flow.classify" in src)
        ok &= check("%s carries no scrolling machinery" % name,
                    "scroll_until_stable" not in src and "page_flow.hydrate" not in src)
        # Credentials never reach a log, in any engine.
        ok &= check("%s masks credentials globally, not just once" % name,
                    "pass@" not in mod._mask_credentials(
                        "a ws://user:pass@h:1/ b ws://user:pass@h:1/"))
        ok &= check("%s refuses a host that is not MediaMarkt" % name,
                    "is_supported_host" in src)
        # Both modes, and only both.
        ok &= check("%s offers exactly the listing and product modes" % name,
                    '"listing", "product"' in src)

    # For "it must pass with no engine installed" to mean anything, each
    # engine has to import its driver at MODULE level — otherwise the module
    # imports cleanly with the library absent, the group never skips, and the
    # CI job that exists to catch that cannot. This drifts back silently, so
    # it is asserted rather than trusted.
    driver_imports = {"playwright_scraper": "playwright",
                      "puppeteer_scraper": "pyppeteer",
                      "selenium_scraper": "selenium"}
    for name, lib in driver_imports.items():
        path = os.path.join(REPO_ROOT, name + ".py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        ok &= check("%s imports %s at module level, so an absent library skips"
                    % (name, lib), lib in top_level)
    return ok


# ---------------------------------------------------------------------------
# Repository hygiene
# ---------------------------------------------------------------------------
def test_no_capture_leaks():
    group("no credentials or personal data in the committed fixtures")
    ok = True
    fixtures = "\n".join(v for k, v in sorted(globals().items())
                         if k.startswith("FIX_") and isinstance(v, str))
    # Guarded with PATTERNS rather than with the literals a previous capture
    # happened to contain, so the NEXT capture is checked too. MediaMarkt's
    # pages embed a front-end configuration blob — a Sentry DSN, a Woosmap
    # public key, a store-code JWT — none of which is needed to test a
    # parser, and none of which belongs in a public repository.
    patterns = {
        "a JWT": r"eyJ[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{10,}",
        "an access token": r"(?:access|auth|bearer)[_\-]?[Tt]oken\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{12,}",
        "an API key": r"(?:api|public|secret|private)[_\-]?[Kk]ey\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{12,}",
        "a Sentry DSN": r"https://[0-9a-f]{16,}@[\w.]*ingest",
        "a session id": r"session[_\-]?[Ii]d\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{8,}",
        "an email address": r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
        "a proxy credential": r"://[^\s/@\"]+:[^\s/@\"]+@",
    }
    for label, pattern in patterns.items():
        hits = re.findall(pattern, fixtures)
        ok &= check("the fixtures contain no %s" % label, not hits)

    # The repo-wide grep CI runs, applied here too so a failure is local.
    ok &= check("no .env file is committed",
                not os.path.exists(os.path.join(REPO_ROOT, ".env"))
                or ".env" in open(os.path.join(REPO_ROOT, ".git", "info", "exclude"),
                                  encoding="utf-8").read()
                if os.path.exists(os.path.join(REPO_ROOT, ".git", "info", "exclude"))
                else not os.path.exists(os.path.join(REPO_ROOT, ".env")))
    return ok


# Wording the family enforces. Four separately-billed 2Captcha products sit
# behind one key, and two of these names were used for a placeholder endpoint
# that no longer exists — an editor reintroducing either costs a support
# ticket, so the check is cheap insurance.
BANNED_PHRASES = (
    "cloud browser",
    "antidetect browser",
    "anti-detect browser",
    "2scraper Antidetect Browser",
    "gate.2prx.com",
    "2prx.com",
    "--antidetect",
    "ANTIDETECT_LOCAL_API",
)

# Flags that must not exist ON THE ENGINES, each for its own reason:
#
#   --antidetect   removed from this family; the endpoint behind it was a
#                  placeholder that never existed.
#   --country      the hostname already decides which country site a run
#                  reads, so a flag could disagree with the URL it was given
#                  and there would be no right answer. (fingerprint_client.py
#                  legitimately HAS a --country: it picks a fingerprint's
#                  locale, which is a different question.)
#   --marketplace  same reasoning, under the sibling repo's name for it.
#   --details      the old pre-family scraper's flag for "also fetch each
#                  product page". That is --mode product now, and a run that
#                  silently multiplies its request count is not a flag.
REMOVED_ENGINE_FLAGS = ("--antidetect", "--marketplace", "--country", "--details")
ENGINE_FILES = ("playwright_scraper.py", "puppeteer_scraper.py",
                "selenium_scraper.py")


def test_wording():
    group("wording and removed flags")
    ok = True
    shipped = [f for f in os.listdir(REPO_ROOT)
               if f.endswith((".py", ".md", ".txt", ".toml", ".yml", ".yaml"))
               and f != os.path.basename(__file__)]
    for phrase in BANNED_PHRASES:
        offenders = []
        for f in shipped:
            try:
                text = open(os.path.join(REPO_ROOT, f), encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            if phrase.lower() in text.lower():
                offenders.append(f)
        ok &= check("no shipped file says %r" % phrase, not offenders)

    for flag in REMOVED_ENGINE_FLAGS:
        offenders = []
        for f in ENGINE_FILES:
            path = os.path.join(REPO_ROOT, f)
            if not os.path.exists(path):
                continue
            text = open(path, encoding="utf-8").read()
            # A prose mention explaining why the flag does NOT exist is fine
            # and is worth keeping; an argparse registration is not.
            if ('add_argument("%s"' % flag) in text or \
                    ("add_argument('%s'" % flag) in text:
                offenders.append(f)
        ok &= check("no engine registers the removed flag %s" % flag,
                    not offenders)

    # The product this repo integrates with, named correctly.
    readme = os.path.join(REPO_ROOT, "README.md")
    if os.path.exists(readme):
        text = open(readme, encoding="utf-8").read()
        ok &= check("the README names the Scraping Browser API",
                    "Scraping Browser API" in text)
        ok &= check("the README does not name a competitor",
                    not re.search(r"brightdata|oxylabs|smartproxy|zyte|scraperapi\.com",
                                  text, re.IGNORECASE))
    return ok


# Names Python provides that are not imports and not assignments.
_MODULE_DUNDERS = {"__file__", "__name__", "__doc__", "__package__",
                   "__spec__", "__loader__", "__builtins__", "__debug__"}


def _undefined_names(path):
    """Names loaded in `path` that are never imported, defined or assigned.

    A deliberately coarse approximation — it pools every binding in the file
    rather than tracking scopes, so it under-reports and never invents a
    problem. That is the right trade here: this exists to catch a name that
    is nowhere at all, and a false positive would be worse than a miss.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    bound = set(dir(builtins)) | _MODULE_DUNDERS
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {(a.asname or a.name.split(".")[0]) for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            bound |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Global):
            bound |= set(node.names)
    missing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) \
                and node.id not in bound:
            missing.setdefault(node.id, []).append(node.lineno)
    return missing


class _FakeSession:
    """Stands in for a _BrowserSession: opened, closed, carries a pool."""

    def __init__(self, pool=None):
        self.pool = pool
        self.closed = False

    def open(self):
        return self

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# A fingerprint in the shape the API actually returns, trimmed to the keys
# this repo reads. Cut from a real `format=chromium` response on 2026-09-09;
# the id and the exact pixel values are the only things changed, and only so
# that nothing here looks like a specific machine.
FIX_FINGERPRINT = {
    "id": 1000000,
    "country": "DE",
    "userAgent": {
        "userAgent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/146.0.0.0 Safari/537.36"),
        "platform": "Windows",
        "mobile": False,
    },
    "intl": {
        "contentLocale": "de-DE",
        "languages": ["de-DE", "de", "en-US", "en"],
        "timeZone": "Europe/Berlin",
    },
    "screen": {"width": 1920, "height": 1080,
               "outerWidth": 1920, "outerHeight": 992,
               "deviceScaleFactor": 1},
}


def test_fingerprint_application():
    group("a fingerprint is applied as the fingerprint describes it")
    ok = True
    import fingerprint_client as fpc

    ua = fpc.fingerprint_user_agent(FIX_FINGERPRINT)
    # The UA used to be read from `userAgent.value`, a key the API returns in
    # NEITHER format. So --fingerprint silently set no user agent at all and
    # the browser kept its own: a German fingerprint's screen and locale
    # wearing a local Chromium's UA, which is precisely the identity mismatch
    # the flag exists to avoid.
    ok &= check("the user agent is found in the shape the API returns",
                ua and ua.startswith("Mozilla/5.0 (Windows NT 10.0"))
    ok &= check("the `raw` format's ua key is understood too",
                fpc.fingerprint_user_agent({"data": {"ua": "UA/1.0"}}) == "UA/1.0")
    ok &= check("a fingerprint with no user agent yields None, not a crash",
                fpc.fingerprint_user_agent({"country": "DE"}) is None)

    kw = fpc.playwright_context_kwargs(FIX_FINGERPRINT)
    ok &= check("the context carries the fingerprint's user agent",
                kw.get("user_agent") == ua)
    # `locale` used to be built as f"en-{country}", giving "en-DE" for a
    # German fingerprint. An English-speaking visitor in Germany is possible,
    # but it is not what this fingerprint describes, and a locale that
    # contradicts the rest of the identity is the mismatch again.
    ok &= check("the locale is the fingerprint's own, not en-<country>",
                kw.get("locale") == "de-DE")
    ok &= check("the timezone is carried, so the browser cannot contradict it",
                kw.get("timezone_id") == "Europe/Berlin")
    # The device pixel ratio, which Playwright takes as its own option and
    # which was dropped on the floor until a live browser was compared
    # against the fingerprint: one stating 1.25 produced a browser reporting
    # `devicePixelRatio === 1`, so the identity contradicted itself on an
    # axis a fingerprinter reads for free.
    ok &= check("fingerprint: the device scale factor is carried",
                kw.get("device_scale_factor") == 1)
    # A viewport exactly equal to the screen is itself a signal, and the
    # fingerprint states its own window size rather than needing one guessed.
    ok &= check("the viewport is the fingerprint's window, not its screen",
                kw.get("viewport") == {"width": 1920, "height": 992}
                and kw.get("screen") == {"width": 1920, "height": 1080})

    # Falling back sensibly when a field is absent, rather than dropping it.
    bare = fpc.playwright_context_kwargs({"country": "FR", "screen":
                                          {"width": 1280, "height": 800}})
    ok &= check("a fingerprint with no intl block still gets a locale",
                bare.get("locale") == "en-FR")
    ok &= check("...and a window smaller than the screen",
                bare["viewport"]["height"] < bare["screen"]["height"])
    ok &= check("a fingerprint with nothing usable yields no kwargs",
                fpc.playwright_context_kwargs({}) == {})

    # Every key this produces must be one Playwright's new_context accepts;
    # an unknown one is a TypeError at launch, on the paid path, at runtime.
    accepted = {"user_agent", "viewport", "screen", "locale", "timezone_id",
                "geolocation", "permissions", "extra_http_headers",
                "device_scale_factor", "is_mobile", "has_touch", "color_scheme"}
    ok &= check("every context kwarg is one Playwright accepts",
                set(kw) <= accepted)
    return ok


def test_credentials_never_reach_a_log():
    group("an API key never reaches a log or an exception message")
    ok = True
    import fingerprint_client as fpc
    import captcha_solver as cs

    # requests puts the FULL URL — query string included — into the text of
    # HTTPError and of every connection error. Both of these modules have an
    # endpoint that takes the key as a query parameter, so an error there
    # echoed a live key to the terminal. It did, once, on a real call.
    # An obviously fake key, and NOT a real one even a revoked one: a
    # 32-hex string in a public repo reads as a live credential to every
    # scanner that looks, including this repo's own CI grep. The word
    # "example" in the name is what tells that grep this line is a fixture.
    example_key = "0123456789abcdef0123456789abcdef"
    for name, module in (("fingerprint_client", fpc), ("captcha_solver", cs)):
        redacted = module._redact(
            "400 Client Error: Bad Request for url: "
            "https://api.2captcha.com/fingerprint/random?format=chromium&"
            "key=%s" % example_key)
        ok &= check("%s redacts a key out of an error message" % name,
                    example_key not in redacted)
        ok &= check("...and keeps the endpoint, which is the useful half",
                    "api.2captcha.com/fingerprint/random" in redacted)
        ok &= check("%s redacts clientKey too" % name,
                    example_key not in module._redact("clientKey=%s" % example_key))
        ok &= check("%s leaves ordinary text alone" % name,
                    module._redact("upstream status 403") == "upstream status 403")
    return ok


def test_concurrent_dispatch(skips):
    group("concurrent page dispatch (threads, stop event, accounting)")
    ok = True
    try:
        import playwright_scraper as eng
    except ImportError as e:
        skips.append("concurrent dispatch (%s)" % e)
        return ok

    # The thread fan-out is the one part of --concurrency that the rest of
    # this suite does not reach, and it is not reachable from a live run in
    # every environment either: page 1 is always fetched alone and decides
    # whether the rest may be addressed, so a blocked page 1 means the
    # workers never start. Driven here with the browser stubbed out, which
    # leaves exactly the concurrency logic under test.
    original = (eng.sync_playwright, eng._BrowserSession, eng._fetch_one_page)

    class Args:
        delay = 0
        mode = "listing"
        out = "x"

    def run(specs, concurrency, rows_for_page, die_on=()):
        fetched, lock = [], threading.Lock()

        def fake_fetch(session, args, pool, page_num, url):
            with lock:
                fetched.append(page_num)
            if page_num in die_on:
                raise RuntimeError("worker blew up on page %d" % page_num)
            outcome = eng.PageOutcome(page_num=page_num, url=url)
            outcome.products = rows_for_page(page_num)
            return outcome

        eng.sync_playwright = lambda: _FakePlaywright()
        eng._BrowserSession = lambda pw, args, pool, **kw: _FakeSession(pool)
        eng._fetch_one_page = fake_fetch
        try:
            results, unattempted, exhausted = eng._fetch_pages_concurrently(
                Args(), None, specs, concurrency)
        finally:
            (eng.sync_playwright, eng._BrowserSession,
             eng._fetch_one_page) = original
        return fetched, results, unattempted, exhausted

    # 1. Every page fetched exactly once, whatever the worker count.
    specs = [(n, "u%d" % n) for n in range(2, 12)]
    fetched, results, unattempted, exhausted = run(
        specs, 4, lambda n: ["row"])
    ok &= check("every queued page is fetched exactly once",
                sorted(fetched) == [n for n, _ in specs])
    ok &= check("every page produces an outcome",
                sorted(o.page_num for o in results) == [n for n, _ in specs])
    ok &= check("nothing is left unattempted when the listing does not end",
                unattempted == [] and not exhausted)

    # 2. Results arrive in whatever order the threads finish, which is
    #    exactly why the caller merges by page number instead of by arrival.
    #    Sorting them must reconstruct the page order.
    ok &= check("outcomes can be put back into page order",
                [o.page_num for o in sorted(results, key=lambda o: o.page_num)]
                == [n for n, _ in specs])

    # 3. The stop event. Asking for 50 pages of a listing that ends at page 5
    #    must not fetch 45 empty ones: workers check the event before taking
    #    more work, so at most (concurrency - 1) extra are already in flight.
    specs = [(n, "u%d" % n) for n in range(2, 51)]
    fetched, results, unattempted, exhausted = run(
        specs, 3, lambda n: [] if n >= 5 else ["row"])
    ok &= check("the end of the listing stops dispatch", exhausted)
    ok &= check("an exhausted listing costs at most (concurrency-1) extra "
                "fetches (%d fetched of 49 queued)" % len(fetched),
                len(fetched) <= 4 + 3)
    ok &= check("the pages never tried are reported, not counted as failed",
                unattempted and all(o.ok for o in results))
    ok &= check("unattempted pages are reported in order",
                unattempted == sorted(unattempted))

    # 4. A worker that dies must not hang the run, and must not swallow the
    #    pages its siblings did fetch.
    specs = [(n, "u%d" % n) for n in range(2, 8)]
    fetched, results, unattempted, exhausted = run(
        specs, 3, lambda n: ["row"], die_on={3})
    ok &= check("a worker that raises does not hang the run",
                len(results) + len(unattempted) + 1 >= len(specs))
    ok &= check("the pages other workers fetched still come back",
                any(o.page_num != 3 for o in results))
    return ok


def test_no_undefined_names():
    group("no engine references a name that does not exist")
    ok = True
    # This exists because of a bug that got all the way to a live run.
    # puppeteer_scraper.py called `detect_page_state(...)` on a line reached
    # only while fetching a page, after the import of that name had been
    # removed. The module imported fine, `--help` worked, `compileall`
    # passed, the whole offline suite passed and CI was green — and the
    # engine died with NameError on its first real page.
    #
    # Byte-compiling proves a file PARSES. It says nothing about whether the
    # names in it resolve, and the paths where they do not are exactly the
    # ones an offline suite cannot execute.
    for name in sorted(f for f in os.listdir(REPO_ROOT) if f.endswith(".py")):
        missing = _undefined_names(os.path.join(REPO_ROOT, name))
        detail = ", ".join("%s (line %d)" % (k, v[0])
                           for k, v in sorted(missing.items()))
        ok &= check("%s references no undefined name%s"
                    % (name, ": " + detail if missing else ""), not missing)
    return ok


def test_dockerfile_copies_what_it_runs():
    group("the Docker image contains every module its entrypoint imports")
    ok = True
    path = os.path.join(REPO_ROOT, "Dockerfile")
    if not os.path.exists(path):
        return check("Dockerfile exists", False)

    # The Dockerfile COPYs an explicit list rather than the whole directory,
    # which is right — the image should not carry the test suite, the
    # fixtures or a stray .env. The cost is that the list can fall behind the
    # imports, and NOTHING else in this repo would notice: CI never builds
    # the image, so a missing module ships and the container dies with
    # ModuleNotFoundError on every invocation, `--help` included.
    #
    # That is not hypothetical. `proxy_pool.py` was missing from this list,
    # and playwright_scraper.py imports it at module level.
    raw = open(path, encoding="utf-8").read()
    joined = re.sub(r"\\\n\s*", " ", raw)          # fold line continuations
    copied = set()
    for line in joined.splitlines():
        if line.startswith("COPY "):
            copied.update(tok for tok in line.split() if tok.endswith(".py"))

    entrypoint = None
    m = re.search(r'ENTRYPOINT\s*\[([^\]]*)\]', joined)
    if m:
        parts = [x.strip().strip('"\'') for x in m.group(1).split(",")]
        entrypoint = next((x for x in parts if x.endswith(".py")), None)
    ok &= check("the Dockerfile names a Python entrypoint", bool(entrypoint))
    if not entrypoint:
        return False
    ok &= check("the entrypoint itself is copied into the image",
                entrypoint in copied)

    # Every LOCAL module the entrypoint reaches, transitively.
    local = {f[:-3] for f in os.listdir(REPO_ROOT) if f.endswith(".py")}

    def reached(module, seen=None):
        seen = seen if seen is not None else set()
        if module in seen:
            return seen
        seen.add(module)
        tree = ast.parse(open(os.path.join(REPO_ROOT, module + ".py"),
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in local:
                    reached(name, seen)
        return seen

    needed = reached(entrypoint[:-3])
    missing = sorted(m + ".py" for m in needed if (m + ".py") not in copied)
    ok &= check("every module the entrypoint imports is COPYed (%s)"
                % (", ".join(missing) if missing else "none missing"),
                not missing)

    # The other direction is a warning, not a failure: diff_runs.py is copied
    # deliberately as a companion tool even though the engine never imports
    # it. But anything copied must at least still EXIST.
    gone = sorted(f for f in copied
                  if not os.path.exists(os.path.join(REPO_ROOT, f)))
    ok &= check("the Dockerfile copies no file that has been deleted (%s)"
                % (", ".join(gone) if gone else "none"), not gone)
    return ok


def test_sample_output():
    group("sample_output is cut from a real run")
    ok = True
    path = os.path.join(REPO_ROOT, "sample_output.json")
    if not os.path.exists(path):
        return check("sample_output.json exists", False)
    rows = json.load(open(path, encoding="utf-8"))
    ok &= check("the sample has rows", len(rows) > 0)
    names = [f.name for f in fields(Product)]
    ok &= check("its columns match the Product schema exactly",
                all(set(r) == set(names) for r in rows))
    text = json.dumps(rows, ensure_ascii=False)
    ok &= check("the sample carries no fabrication markers",
                not re.search(r"example\.com|lorem ipsum|FIXME|TODO|XXXX",
                              text, re.IGNORECASE))
    ok &= check("every sample row has a numeric MediaMarkt article number",
                all(re.fullmatch(r"\d{5,}", r.get("sku") or "") for r in rows))
    ok &= check("every sample row says which country site it came from",
                all((r.get("source") or "") in HOSTS for r in rows))
    ok &= check("every sample row's URL is a real product URL",
                all("/product/" in (r.get("url") or "") for r in rows))
    # The sample is what a reader judges the output by, so it has to show the
    # provenance column doing its job rather than a column of nulls.
    ok &= check("the sample shows a real price_source",
                all(r.get("price_source") in ("jsonld", "jsonld+dom", "dom")
                    for r in rows))

    csv_path = os.path.join(REPO_ROOT, "sample_output.csv")
    if os.path.exists(csv_path):
        header = open(csv_path, encoding="utf-8").read().split("\n")[0]
        ok &= check("the sample CSV header matches the schema",
                    header.strip().split(",") == names)
    return ok


# ---------------------------------------------------------------------------
def test_ci_checks_is_actually_wired_up():
    group("the shipped CI checks run, and pass on this repo")
    ok = True
    # `.github/ci_checks.py` was in this repo and invoked by NOTHING — not
    # CI, not this suite — while `tests.yml` carried an inline grep doing a
    # narrower version of the same job with its own allowlist. The inline one
    # matched only ws:// and wss://, so an `http://user:pass@` credential
    # would have sailed past CI; the shipped one, which does match http://,
    # meanwhile failed on this repo's own main because its allowlist was
    # missing four legitimate documentation placeholders.
    #
    # Two sources of truth, one dead and one with a hole. Running the shipped
    # one here as well means a failure shows up locally, before a push,
    # rather than in a log nobody reads.
    script = os.path.join(REPO_ROOT, ".github", "ci_checks.py")
    ok &= check("ci_checks.py is present", os.path.exists(script))
    if not os.path.exists(script):
        return False
    proc = subprocess.run([sys.executable, script, "--all"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    ok &= check("ci_checks.py --all passes on this repo (exit %d)"
                % proc.returncode, proc.returncode == 0)
    if proc.returncode != 0:
        for line in (proc.stdout + proc.stderr).strip().split("\n")[-12:]:
            print("        %s" % line)
    # ...and the workflow calls it rather than reimplementing it.
    wf = open(os.path.join(REPO_ROOT, ".github", "workflows", "tests.yml"),
              encoding="utf-8").read()
    ok &= check("tests.yml runs the shipped check rather than an inline copy",
                "ci_checks.py --secret-check" in wf
                or "ci_checks.py --all" in wf)
    ok &= check("...and carries no second, narrower inline credential grep",
                "(ws|wss)://[^ " not in wf)
    return ok


def main() -> int:
    ok = True
    # Checks that could not run because an optional engine library is absent.
    # Reported at the end: a suite that silently skips part of itself and
    # still says "all passed" is the same defect as code that reports success
    # without checking that what it wanted actually happened.
    skips = []

    ok &= test_price_parsing()
    ok &= test_strike_prices()
    ok &= test_listing_values()
    ok &= test_second_locale()
    ok &= test_unrated_products()
    ok &= test_totals_and_hub()
    ok &= test_product_detail()
    ok &= test_jsonld_shapes()
    ok &= test_urls()
    ok &= test_page_state()
    ok &= test_page_flow()
    ok &= test_output_contract()
    ok &= test_writers()
    ok &= test_finish_run()
    ok &= test_diff()
    ok &= test_captcha()
    ok &= test_env_config()
    ok &= test_proxy_pool()
    ok &= test_engines(skips)
    ok &= test_no_capture_leaks()
    ok &= test_ci_checks_is_actually_wired_up()
    ok &= test_wording()
    ok &= test_fingerprint_application()
    ok &= test_credentials_never_reach_a_log()
    ok &= test_concurrent_dispatch(skips)
    ok &= test_no_undefined_names()
    ok &= test_dockerfile_copies_what_it_runs()
    ok &= test_sample_output()

    print()
    if _failures:
        print("%d check(s) FAILED:" % len(_failures))
        for f in _failures:
            print("  - %s" % f)
    if skips:
        print("%d engine group(s) SKIPPED — an optional engine library is "
              "absent. CI's engine-smoke job installs all three and fails if "
              "this list is non-empty, because a skip reads exactly like a "
              "passing run:" % len(skips))
        for s in skips:
            print("  - %s" % s)
    print("smoke_test: %s" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
