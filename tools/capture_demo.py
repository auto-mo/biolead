"""Capture the five demo fallback screenshots, in slide-10 order.

Run against a live API with ANTHROPIC_API_KEY set, so the second read in every shot is the
real model rather than the deterministic stub.
"""
import pathlib, sys
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "demo"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:5173/"

CASES = [
    ("01-AR-hair-thinning",       "AR · hair thinning"),
    ("02-IL17A-oily-skin",        "IL17A · oily skin"),
    ("03-FLG-atopic-dermatitis",  "FLG · atopic dermatitis"),
    ("04-FLG-cosmetic-dry-skin",  "FLG · cosmetic dry skin"),
    ("05-TNF-oily-skin",          "TNF · oily skin"),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for name, label in CASES:
        pg = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector(".launch")

        # Drive it through the examples menu, the same path a demo would take.
        pg.click(".pop-examples .btn-outline")
        pg.wait_for_selector(".menu-row")
        pg.click(f".menu-row:has-text('{label}')")

        # Done = the assessment rendered its footer and the progress bar is gone.
        pg.wait_for_selector(".prov-footer", timeout=180_000)
        pg.wait_for_function("() => !document.querySelector('.progress')", timeout=180_000)
        pg.wait_for_timeout(700)

        stub = pg.evaluate("() => (document.querySelector('.register-src')||{}).textContent || ''")
        if "stub" in stub.lower():
            print(f"ABORT {name}: stub adjudicator in use, not the real model")
            browser.close(); sys.exit(1)

        pg.screenshot(path=OUT / f"{name}.png", full_page=True)
        verdict = pg.evaluate("() => (document.querySelector('.rec-verb')||{}).textContent || ''")
        print(f"{name}: {verdict}")
        pg.close()
    browser.close()

print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))
