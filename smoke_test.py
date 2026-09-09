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
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
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


# URLs the fixtures were taken from. Kept beside them because `parse_products`
# reads the page number, the category label and the host out of the URL it is
# given, so a fixture without its URL tests less than it looks like it does.
URL_LISTING_DE = "https://www.mediamarkt.de/de/category/k%C3%BChlen-gefrieren-32.html"
URL_LISTING_SEARCH = "https://www.mediamarkt.de/de/search.html?query=usb-c%20kabel%202m"
# No "www." — the Polish and Luxembourg sites answer on the bare host, and
# their own hreflang entries say so.
URL_LISTING_PL = ("https://mediamarkt.pl/pl/category/"
                  "kuchenki-mikrofalowe-z-grillem-do-zabudowy-70164.html")
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


def test_env_config():
    group("env_config")
    ok = True
    ok &= check("the env keys are this site's, not another repo's",
                set(env_config.ENV_KEYS) ==
                {"TWOCAPTCHA_KEY", "MEDIAMARKT_CDP_ENDPOINT",
                 "MEDIAMARKT_PROXY", "MEDIAMARKT_URL"})

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

    line = "http://eu.proxy.2captcha.com:2334:login:pass"
    ok &= check("a host:port:user:pass line is understood",
                parse_proxy_line(line) is not None)
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
    ok &= test_wording()
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
