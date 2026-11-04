# Convenience targets for Linux / macOS. Windows users can run the commands
# directly (see README) or use `make` from Git Bash / WSL.

PY ?= python
VENV ?= .venv
BACKEND := backend

.PHONY: help install test test-backend test-frontend build backend frontend simulator up down logs smoke detect clean

help:
	@echo "install      create the venv and install backend + frontend dependencies"
	@echo "test         run the backend test suite"
	@echo "build        typecheck and build the frontend bundle"
	@echo "backend      run the FastAPI app on :8000"
	@echo "frontend     run the Vite dev server on :5173"
	@echo "simulator    run the metric simulator on :9101"
	@echo "up           docker compose up -d --build"
	@echo "smoke        curl the core endpoints"
	@echo "detect       run one detection cycle from the CLI"

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -r $(BACKEND)/requirements.txt
	cd frontend && npm install

test:
	cd $(BACKEND) && ../$(VENV)/bin/python -m pytest tests -v

build:
	cd frontend && npm run build

backend:
	cd $(BACKEND) && ../$(VENV)/bin/python -m uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

simulator:
	$(VENV)/bin/python simulator/app.py

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

smoke:
	curl -fsS localhost:8000/api/v1/health && echo
	curl -fsS localhost:8000/api/v1/overview | head -c 400 && echo
	curl -fsS localhost:9101/healthz && echo

detect:
	$(VENV)/bin/python scripts/detect_incidents.py

clean:
	rm -rf $(VENV) frontend/node_modules frontend/dist backend/data .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
