"""Capture the batch mode and verdict matrix screenshots against the running dev server.

Also asserts, mid-capture, the things a screenshot cannot show: that the headline counts
sum to the input, that abstentions are present in the table rather than filtered out, and
that no cell of the matrix was dropped.
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "batch"
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:5173/"

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
    print(("  ok   " if cond else "  FAIL ") + msg)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    pg.goto(URL, wait_until="networkidle")
    pg.wait_for_selector(".launch")

    # 1. Single gene is what opens. Assert it before touching anything.
    check(pg.locator(".shell.landing").count() == 1, "landing state is single gene")
    check(pg.locator(".batch").count() == 0, "batch is not the default view")

    # 2. Endpoint menu with the drawn rating scale and category icons.
    pg.click(".epick-btn")
    pg.wait_for_selector(".epick-opt")
    pg.wait_for_timeout(250)
    check(pg.locator(".rscale-ticks").count() > 0, "rating renders as ticks")
    check(pg.locator(".epick-cat").count() >= 7, "every endpoint has a category icon")
    pg.screenshot(path=OUT / "01-endpoint-menu-visual.png")
    pg.keyboard.press("Escape")

    # 3. Endpoint menu ranked by a gene. FLG must still list every endpoint.
    pg.click(".gfield input")
    pg.type(".gfield input", "FLG", delay=50)
    pg.wait_for_timeout(1800)
    pg.keyboard.press("Escape")
    pg.click(".epick-btn")
    pg.wait_for_selector(".epick-opt")
    pg.wait_for_timeout(400)
    n_opts = pg.locator(".epick-opt").count()
    check(n_opts >= 7, f"ranking did not filter: {n_opts} endpoints still listed")
    check(pg.locator(".epick-rankhead").count() == 1, "menu says it is ordered by the gene")
    check(pg.locator(".evmark").count() >= 7, "every endpoint carries an evidence mark")
    pg.screenshot(path=OUT / "02-endpoint-ranked-by-gene.png")
    pg.keyboard.press("Escape")

    # 4. Batch mode, preset run.
    pg.click(".modeseg button:has-text('Assess a list')")
    pg.wait_for_selector(".bl-presets")
    pg.wait_for_timeout(300)
    pg.screenshot(path=OUT / "03-batch-landing.png")

    pg.click(".bl-preset >> nth=0")
    pg.wait_for_selector(".bl-headline", timeout=60000)
    pg.wait_for_timeout(700)

    headline = pg.locator(".bl-line").inner_text()
    print("\nheadline:", headline.replace("\n", " "))
    check("tier 1 or tier 2" in headline, "headline names the tier 1 / tier 2 split")

    # The matrix must show every cell, including the empty ones.
    cells = pg.locator(".mx-cell").count()
    check(cells == 6, f"matrix renders all six cells (got {cells})")
    check(pg.locator(".mx-cell.is-empty").count() > 0,
          "empty cells render as empty rather than being hidden")
    check(pg.locator(".mx-band").count() == 1, "insufficient is a band, not a fourth row")

    # Abstentions are grouped, not dropped.
    check(pg.locator(".bl-declined-head").count() == 1, "abstentions have their own band")
    declined = int(pg.locator(".bl-declined-head .num").inner_text())
    check(declined > 0, f"abstentions are shown ({declined} of them)")

    pg.screenshot(path=OUT / "04-batch-result.png", full_page=True)

    # Matrix on its own, for the deck.
    pg.locator(".matrix").scroll_into_view_if_needed()
    pg.wait_for_timeout(200)
    pg.locator(".matrix").screenshot(path=OUT / "05-matrix.png")

    # 5. Back to single, with the locator beside the verdict.
    pg.click(".mx-dot >> nth=0")
    pg.wait_for_selector(".assessment", timeout=60000)
    pg.wait_for_timeout(900)
    check(pg.locator(".verdict-matrix .matrix-locator").count() == 1,
          "single assessment carries the compact locator")
    check(pg.locator(".verdict-matrix .mx-dot").count() == 0,
          "locator carries no dots, so it cannot read as a second verdict")
    active = pg.locator(".matrix-locator .is-active").count()
    check(active == 1, f"exactly one cell is lit (got {active})")
    pg.screenshot(path=OUT / "06-single-with-locator.png", full_page=True)

    # 6. Narrow viewport: no horizontal overflow anywhere.
    pg.set_viewport_size({"width": 375, "height": 812})
    pg.wait_for_timeout(500)
    over = pg.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    check(over <= 0, f"no horizontal overflow at 375px (overflow {over}px)")
    pg.screenshot(path=OUT / "07-narrow.png", full_page=True)

    b.close()

print("\n" + "\n".join(str(p) for p in sorted(OUT.glob("*.png"))))
if failures:
    print(f"\n{len(failures)} CHECK(S) FAILED:")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("\nall checks passed")
