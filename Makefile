.PHONY: dev api web eval eval-graph eval-model demo venv
PY := .venv/bin/python

venv: ; python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
api:  ; cd backend && PYTHONPATH=. ../$(PY) -m uvicorn app.api.routes:app --port 8931 --reload
web:  ; cd frontend && npm run dev
eval: ; $(PY) eval/run_eval.py
eval-graph: ; $(PY) eval/run_eval.py --provider graph
demo: ; $(PY) tools/run_demo_order.py --runs 3
eval-model: ; $(PY) eval/run_eval.py --model
