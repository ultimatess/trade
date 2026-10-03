.DEFAULT_GOAL := help
UV  ?= uv
RUN := $(UV) run --frozen
PY  := $(RUN) python

.PHONY: help setup dev test test-critical demo backtest paper lint format format-check typecheck \
        reset health record-social record-social-once refresh-universe tag-social social-status ci

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## Create .venv and install locked dependencies (Python 3.11+)
	$(UV) sync --frozen
	@test -f .env || echo "Note: no .env yet. Copy .env.example to .env to configure the social recorder."

dev: ## Run the dashboard on http://127.0.0.1:8080 (loopback only)
	$(PY) serve_ui.py --port 8080

test: ## Run the full test suite
	$(RUN) pytest

test-critical: ## Run safety-critical tests only (any failure blocks merge)
	$(RUN) pytest -m critical

demo: ## Full end-to-end demo (Phase 3+)
	@echo "NOT IMPLEMENTED: the end-to-end demo needs the event-driven backtester (Phase 3). See docs/MIGRATION_PLAN.md."; exit 1

backtest: ## Legacy synthetic simulation (NOT evidence of edge)
	$(PY) -m trade.backtesting

paper: ## One demonstration paper-trading cycle
	$(PY) -m trade.paper

lint: ## Ruff lint + import-layer contracts
	$(RUN) ruff check .
	$(RUN) lint-imports

format: ## Apply ruff formatting
	$(RUN) ruff format .

format-check: ## Check formatting without changing files
	$(RUN) ruff format --check .

typecheck: ## mypy (strict on trade.core)
	$(RUN) mypy

reset: ## Remove caches. Never touches kill-switch state, tokens or recorded data
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist
	@echo "Caches removed. Kill-switch state is only cleared by an operator unlock in the dashboard."

health: ## Report config, kill switch, state dir, universe and recorder status (read-only)
	$(PY) -m trade.health

record-social: ## Record real Reddit posts forward (needs .env credentials; Ctrl-C to stop)
	$(PY) -m trade.data.recorders run

record-social-once: ## Single recorder poll
	$(PY) -m trade.data.recorders run --once

refresh-universe: ## Snapshot the Nifty 500 list from NSE (dated + hashed)
	$(PY) -m trade.data.recorders refresh-universe

tag-social: ## Tag one day of recordings with symbols: make tag-social DATE=YYYY-MM-DD
	$(PY) -m trade.data.recorders tag $(if $(DATE),--date $(DATE),)

social-status: ## Summarise recorded social data and recorder events
	$(PY) -m trade.data.recorders status

ci: format-check lint typecheck test ## Everything CI runs (except the secret scan)
