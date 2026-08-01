# tools/

Reproducible one-shot scripts. None of these are part of the app; they generate or verify
artefacts that are committed under `docs/`. All paths are repo-relative, so run them from
anywhere.

Most need the dev servers up (`make api` on 8931, `make web` on 5173) and Playwright
(`python3 -m playwright install chromium` if it is not already there).

| Script | What it does | Needs |
|---|---|---|
| `gen_palette.py` | Prints the generated M3 tonal ramps seeded from the LABS anchor `#080331`. The output is pasted into `:root` in `frontend/src/styles.css`; rerun it if the seed changes. |, |
| `gen_aws_diagram.py` | Writes `docs/architecture-aws-full.excalidraw` and `docs/architecture-aws-slide.excalidraw`. Edit the layout here, never by hand in the JSON. |, |
| `render_excalidraw.py` | Renders every `docs/*.excalidraw` to PNG through Excalidraw's own `exportToBlob`, so the PNG is the real renderer. Takes an optional name to render just one. Needs the little Vite page in the note below. | Playwright + the export page |
| `capture_ui.py` | Captures `docs/ui/`, landing state, endpoint menu, gene autocomplete. | both servers |
| `capture_demo.py` | Captures the five slide-10 fallback screenshots into `docs/demo/`. Asserts mid-run that the second read came from the model and aborts if it sees the stub. | both servers + `ANTHROPIC_API_KEY` |
| `audit_links.py` | Runs one assessment and dumps every rendered `href` with where it sits, then counts bare site-root links. This is the regression guard for the citation-links fix; it should always report `0`. | both servers + key |
| `measure_cost.py` | Real per-assessment token cost. Runs three representative packets against Sonnet and Haiku and reads `usage` back. | key |
| `measure_disagreement.py` | Writes `docs/disagreement-pairs.json`: every (gene, condition) pair with two or more deterministically readable trials, the winner by trial count and by summed enrolment, and the per-trial detail behind both. ~70 batched ClinicalTrials.gov calls at 1.5/s. **Do not run it alongside `make eval`**; the two share one limiter and a starved fetch reads as a missing result. | network |

## Regenerating the AWS diagram PNG

`render_excalidraw.py` expects a Vite page serving `@excalidraw/excalidraw` on port 5199. That
scaffold was not committed because it is three throwaway files and ~200MB of `node_modules`. To
rebuild it:

```bash
mkdir -p /tmp/exc && cd /tmp/exc && npm init -y && npm i @excalidraw/excalidraw react react-dom vite
cp "<repo>/docs/"*.excalidraw .
# index.html: <div id="s"></div> + <script type="module" src="/main.js">
# main.js:    import { exportToBlob } from "@excalidraw/excalidraw"
#             const mods = import.meta.glob("./*.excalidraw", {query:"?raw",
#                            import:"default", eager:true})   // pick by ?doc= query param
#             -> exportToBlob({elements, appState:{exportBackground:true,
#                viewBackgroundColor:"#ffffff", exportPadding:40}, files:{},
#                mimeType:"image/png", getDimensions:(w,h)=>({width:w*2,height:h*2,scale:2})})
#             -> base64 onto window.__png, then set #s textContent to "READY"
npx vite --port 5199
```

Then run `python3 tools/render_excalidraw.py`. The committed PNG is current, so this is only
needed if the diagram changes.
