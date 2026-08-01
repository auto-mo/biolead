"""Run one assessment and report every href the page renders, so a link that points at a
site root instead of the cited page is visible rather than assumed."""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "demo"

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    pg.goto("http://localhost:5173/", wait_until="networkidle")
    pg.click(".pop-examples .btn-outline")
    pg.wait_for_selector(".menu-row")
    pg.click(".menu-row:has-text('AR · hair thinning')")
    pg.wait_for_selector(".prov-footer", timeout=180_000)
    pg.wait_for_function("() => !document.querySelector('.progress')", timeout=180_000)
    pg.wait_for_timeout(500)

    # Expand the trace so its chips are in the DOM too.
    if pg.evaluate("() => document.querySelector('.trace-bar').getAttribute('aria-expanded') === 'false'"):
        pg.click(".trace-bar")
        pg.wait_for_timeout(300)

    links = pg.evaluate("""() => [...document.querySelectorAll('a.ref')].map(a => ({
        text: a.textContent.trim(), href: a.href,
        where: a.closest('.steps') ? 'trace' : a.closest('.ledger') ? 'ledger' : 'other',
    }))""")
    plain = pg.evaluate("""() => [...document.querySelectorAll('.refs span.t-mono, .step-top span.t-mono')]
.map(s => s.textContent.trim())""")
    b.close()

seen = set()
print(f"{'WHERE':8} {'LABEL':26} HREF")
for l in links:
    key = (l["where"], l["text"], l["href"])
    if key in seen:
        continue
    seen.add(key)
    print(f"{l['where']:8} {l['text'][:26]:26} {l['href']}")

roots = [l for l in links if l["href"].rstrip("/") in
         ("https://platform.opentargets.org", "https://clinicaltrials.gov")]
print(f"\nplain (unlinked) labels: {sorted(set(plain))}")
print(f"bare site-root links remaining: {len(roots)}")
