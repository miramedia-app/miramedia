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

.PHONY: help up up-all dev down logs ps restart app frontend openapi openapi-json test

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

# Type-check the Next.js frontend
.PHONY: tsc frontend-build
tsc:
	@cd web && pnpm exec tsgo --noEmit

frontend-build:
	@cd web && pnpm build

# Run the backend test suite on the host (no docker needed).
test:
	@MIRAMEDIA_LOG_FILE=/dev/null uv run --python 3.13 pytest