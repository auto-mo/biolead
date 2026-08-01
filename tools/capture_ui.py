"""Capture UI screenshots against the running dev server."""
import pathlib, sys
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "ui"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:5173/"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

    # 1. Landing state, empty.
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_selector(".launch")
    pg.wait_for_timeout(600)
    pg.screenshot(path=OUT / "01-landing.png")

    # 2. Endpoint menu open, showing every borrow and its rating before any query runs.
    pg.click(".epick-btn")
    pg.wait_for_selector(".epick-opt")
    pg.wait_for_timeout(300)
    pg.screenshot(path=OUT / "02-endpoint-menu.png")

    # 3. Gene autocomplete, the ranked list for "AR".
    pg.keyboard.press("Escape")
    pg.click(".gfield input")
    pg.type(".gfield input", "AR", delay=60)
    pg.wait_for_selector(".gfield-opt", timeout=8000)
    pg.wait_for_timeout(300)
    pg.screenshot(path=OUT / "03-gene-autocomplete.png")

    b.close()
print("\n".join(str(p) for p in sorted(OUT.glob("*.png"))))
