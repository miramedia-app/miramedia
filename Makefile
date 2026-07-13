SHELL := /bin/bash

# Docker Compose command (override if needed)
COMPOSE ?= docker compose
DC := $(COMPOSE) -f docker-compose.dev.yaml

# Log args passthrough, e.g.:
# make logs ARGS="--follow --tail=100"
ARGS ?=

# Service names (override if your compose uses different names)
APP_SVC ?= api
FRONTEND_SVC ?= web

.PHONY: help up up-all dev down logs ps restart app frontend openapi openapi-json \
	lint format format-check ty test integration-test migration-head-audit check audit \
	frontend-bootstrap frontend-generate \
	tsc frontend-build

help:
	@echo "Usage:"
	@echo "  All commands run using docker-compose.dev.yaml"
	@echo ""
	@echo "  make up                     # Up core stack (api/web/db), build if needed"
	@echo "  make up-all                 # Up core + external tools (prowlarr/qbittorrent/jackett)"
	@echo "  make dev                    # Up + watch host files; rebuild on Dockerfile/lockfile change"
	@echo "  make down                   # Tear down stack"
	@echo "  make logs ARGS=\"...\"        # (Optional) Set ARGS like \"--follow --tail=100\""
	@echo "  make ps | restart           # Check status or restart containers"
	@echo "  make app                    # Shell into $(APP_SVC) container"
	@echo "  make frontend               # Shell into $(FRONTEND_SVC) container"
	@echo "  make test                   # Run the backend test suite on the host (no docker needed)"
	@echo "  make integration-test       # PostgreSQL integration suite (requires MIRAMEDIA_TEST_DATABASE_URL)"
	@echo "  make lint | format | format-check | ty  # Backend lint, format, format check, typecheck"
	@echo "  make check                  # lint + format-check + ty + test + tsc (CI parity minus OpenAPI drift)"
	@echo "  make frontend-bootstrap     # Fresh-clone web setup (install + generate)"
	@echo "  make frontend-generate      # Generate web build prerequisites (web 'generate' script)"
	@echo "  make frontend-build         # Generate prerequisites, then build the static export"
	@echo "  make tsc                    # Type-check the Next.js frontend"

# Core lifecycle
up:
	$(DC) up -d --build

up-all:
	$(DC) --profile all up -d --build

dev:
	$(DC) up -d --build && $(DC) watch

down:
	$(DC) --profile all down

logs:
	$(DC) logs $(ARGS)

ps:
	$(DC) ps

restart:
	$(DC) restart

# Interactive shells (prefer bash, fallback to sh)
app:
	@$(DC) exec -it $(APP_SVC) bash 2>/dev/null || $(DC) exec -it $(APP_SVC) sh

frontend:
	@$(DC) exec -it $(FRONTEND_SVC) bash 2>/dev/null || $(DC) exec -it $(FRONTEND_SVC) sh

# Write the static OpenAPI spec (web/public/openapi.json) — bundled into the export so the
# backendless GitHub Pages docs site can render the Scalar API reference. PUBLIC_VERSION is
# unset so the committed spec is deterministic (pyproject version), not a stray shell/CI value;
# CI diffs this target's output against the committed file to catch drift.
openapi-json:
	@env -u PUBLIC_VERSION MIRAMEDIA_LOG_FILE=/tmp/mm.log uv run --python 3.13 python -c "import sys, io, json; buf = io.StringIO(); sys.stdout = buf; from miramedia.main import app; sys.stdout = sys.__stdout__; sys.stdout.write(json.dumps(app.openapi(), indent=2))" > web/public/openapi.json

# Regenerate frontend OpenAPI client types (web/src/lib/api/api.d.ts) without running the server.
openapi: openapi-json
	@cd web && pnpm exec openapi-typescript public/openapi.json -o src/lib/api/api.d.ts

lint:
	@uv run --python 3.13 ruff check .

format:
	@uv run --python 3.13 ruff format .

format-check:
	@uv run --python 3.13 ruff format --check .

ty:
	@uv run --python 3.13 ty check miramedia

# Canonical frontend generation step. `web/src` imports the Fumadocs collections
# (`collections/*` -> `web/.source`) and Next's generated type declarations, both
# of which are gitignored — so every build path must generate them first.
# The command itself lives in web/package.json ("generate") so the Makefile and
# the Dockerfile share one definition; extend that script, not this target.
# Assumes dependencies are already installed (see frontend-bootstrap).
frontend-generate:
	@cd web && pnpm run generate

frontend-bootstrap:
	@cd web && pnpm install --frozen-lockfile
	@$(MAKE) frontend-generate

# Scan production Python deps for known vulnerabilities (CI parity).
audit:
	@uv export --locked --no-dev --format requirements-txt > /tmp/req.txt
	@uvx pip-audit --strict -r /tmp/req.txt --disable-pip

# CI parity minus OpenAPI/api.d.ts drift checks (those are PR-only in ci.yml).
check: lint format-check ty test tsc

# Type-check the Next.js frontend
tsc:
	@cd web && pnpm exec tsgo --noEmit

frontend-build: frontend-generate
	@cd web && pnpm build

# Run the backend test suite on the host (no docker needed).
test:
	@MIRAMEDIA_LOG_FILE=/dev/null uv run --python 3.13 pytest

# PostgreSQL integration suite — not collected by `make test`.
integration-test:
	@MIRAMEDIA_LOG_FILE=/dev/null uv run --python 3.13 pytest -m integration tests/integration

migration-head-audit:
	@uv run --python 3.13 python scripts/migration_head_audit.py