.PHONY: dev-backend dev-frontend test lint compile-check

# Native local dev — no Docker required.

dev-backend:
	cd backend && \
	( [ -d .venv ] || python3 -m venv .venv ) && \
	. .venv/bin/activate && \
	pip install -q -r requirements.txt && \
	PYTHONPATH=.. uvicorn app.main:app --reload

dev-frontend:
	cd frontend && npm install && npm run dev

test:
	cd backend && \
	. .venv/bin/activate && \
	PYTHONPATH=".:.." pytest ../tests

lint:
	cd frontend && npx tsc --noEmit

compile-check:
	find core agents providers rendering backend tests scripts -name "*.py" -print0 | xargs -0 -n1 python3 -m py_compile
