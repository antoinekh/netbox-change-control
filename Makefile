# Development commands, on the stack in dev/stack/. See dev/DEV.md.

COMPOSE := docker compose -f dev/stack/compose.yml
MANAGE  := /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py
# Files written into the bind mount must belong to the host user, not to the container's
# netbox user (uid 999), or you cannot edit them afterwards without root.
AS_HOST := -u $(shell id -u):$(shell id -g)

# The ports the compose file publishes; exported so it reads the same values.
export NETBOX_PORT ?= 8890
export E2E_PORT ?= 8891
export WEBHOOK_PORT ?= 8892
# The API token the compose file gives the e2e superuser.
E2E_AUTHORIZATION := Bearer nbt_e2eAdminKey1.devStackE2eTokenNotASecret00000000000000

# Pinned to the versions CI uses, so local and CI results agree. Override with RUFF=ruff or
# ZENSICAL=zensical if you have them installed.
RUFF     ?= uvx ruff@0.17.4
ZENSICAL ?= uvx zensical@0.1.3
SOURCES  := netbox_change_control dev/netbox_change_control_dev dev/stack e2e

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up:  ## Start the stack (builds the image on first run) and wait until NetBox answers
	$(COMPOSE) up -d --build --wait netbox netbox-worker
	@echo "NetBox: http://localhost:$(NETBOX_PORT) (admin / admin)"

.PHONY: down
down:  ## Stop the stack, keeping its databases
	$(COMPOSE) --profile e2e --profile webhook down

.PHONY: destroy
destroy:  ## Stop the stack and delete its databases, so the next start migrates from empty
	$(COMPOSE) --profile e2e --profile webhook down -v

.PHONY: reload
reload:  ## Restart NetBox and the worker to load source changes
	$(COMPOSE) restart netbox netbox-worker

.PHONY: logs
logs:  ## Follow the NetBox log
	$(COMPOSE) logs -f netbox

.PHONY: shell
shell:  ## Open a Django shell
	$(COMPOSE) exec netbox $(MANAGE) shell

.PHONY: seed
seed:  ## Create the demo reviewers, policies, a branch and an open change request
	$(COMPOSE) exec -T netbox $(MANAGE) seed_change_control

.PHONY: adopt
adopt:  ## Open a change request for every ready branch which has none yet
	$(COMPOSE) exec -T netbox $(MANAGE) seed_change_control --skip-branch --adopt

.PHONY: captures
captures:  ## Build the states the documentation screenshots need, and print where to shoot
	$(COMPOSE) exec -T netbox $(MANAGE) seed_captures

.PHONY: captures-clean
captures-clean:  ## Remove the capture branches and their change requests
	$(COMPOSE) exec -T netbox $(MANAGE) seed_captures --teardown

.PHONY: migrations
migrations:  ## Generate migrations for the plugin
	$(COMPOSE) exec -T $(AS_HOST) netbox $(MANAGE) makemigrations netbox_change_control

.PHONY: migrations-check
migrations-check:  ## Fail if the models and the migrations disagree, the way CI does
	$(COMPOSE) exec -T netbox $(MANAGE) makemigrations netbox_change_control --check --dry-run

.PHONY: migrate
migrate:  ## Apply the plugin migrations
	$(COMPOSE) exec -T netbox $(MANAGE) migrate netbox_change_control

.PHONY: check
check:  ## Run the Django system checks
	$(COMPOSE) exec -T netbox $(MANAGE) check

.PHONY: test
test:  ## Run the unit tests (ARGS=netbox_change_control.tests.test_policy for one module)
	$(COMPOSE) exec -T netbox $(MANAGE) test $(or $(ARGS),netbox_change_control) --keepdb -v 2

.PHONY: e2e
e2e:  ## Run the browser tests against a fresh e2e NetBox (ARGS=-k window for some)
	$(COMPOSE) --profile e2e up -d --build --wait --force-recreate netbox-e2e netbox-e2e-worker
	uv run --locked --only-group e2e playwright install chromium
	NETBOX_URL=http://localhost:$(E2E_PORT) E2E_AUTHORIZATION='$(E2E_AUTHORIZATION)' \
	  uv run --locked --only-group e2e pytest e2e $(ARGS)

.PHONY: e2e-down
e2e-down:  ## Stop the e2e NetBox, keeping its database
	$(COMPOSE) --profile e2e stop netbox-e2e netbox-e2e-worker

.PHONY: webhook
webhook:  ## Start the webhook receiver and follow what it receives
	$(COMPOSE) --profile webhook up -d webhook
	@echo "Webhook URL in NetBox: http://webhook:9000/hook, deliveries: http://localhost:$(WEBHOOK_PORT)/"
	$(COMPOSE) logs -f webhook

.PHONY: lint
lint:  ## Lint the sources, the same way CI does
	$(RUFF) check $(SOURCES)
	$(RUFF) format --check $(SOURCES)

.PHONY: format
format:  ## Reformat the sources
	$(RUFF) check --fix $(SOURCES)
	$(RUFF) format $(SOURCES)

.PHONY: docs
docs:  ## Serve the documentation site at http://localhost:8000
	$(ZENSICAL) serve

.PHONY: docs-build
docs-build:  ## Build the documentation site into site/, the way the workflow does
	$(ZENSICAL) build --clean --strict
