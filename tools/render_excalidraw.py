"""Render each .excalidraw in docs/ to PNG through Excalidraw's own exportToBlob.

The PNG is produced by the real renderer rather than a lookalike, which is the only way to
catch a label overflowing its box or an arrow drawn through its own caption.

Needs the throwaway Vite page described in tools/README.md on port 5199.

NOTE: start Vite with --force after changing a diagram. Vite caches the `?raw` import and
will otherwise re-render the previous version while reporting success, which has already
produced one confidently wrong PNG.

Usage:
    python3 tools/render_excalidraw.py                        # every diagram
    python3 tools/render_excalidraw.py architecture-aws-slide
"""
import base64, pathlib, sys
from playwright.sync_api import sync_playwright

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
DOCS.mkdir(parents=True, exist_ok=True)

names = sys.argv[1:] or sorted(p.stem for p in DOCS.glob("*.excalidraw"))
if not names:
    sys.exit("no .excalidraw files in docs/")

with sync_playwright() as pw:
    b = pw.chromium.launch()
    for name in names:
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://localhost:5199/?doc={name}", wait_until="networkidle")
        try:
            pg.wait_for_function(
                "document.getElementById('s').textContent.startsWith('READY')",
                timeout=60000)
        except Exception:
            print(f"{name}: FAILED")
            print("  STATUS:", (pg.text_content("#s") or "")[:600])
            print("  ERRORS:", errs[:5])
            b.close()
            sys.exit(1)
        png = base64.b64decode(pg.evaluate("window.__png"))
        (DOCS / f"{name}.png").write_bytes(png)
        print(f"{name}.png  {len(png):,} bytes")
        pg.close()
    b.close()
