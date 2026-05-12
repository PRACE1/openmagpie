.PHONY: up build down logs logs-core dev-exec dev-manage dev-test dev-makemigrations dev-dbshell dev-migrate dev-lint dev-lint-fix dev-types dev-check

up: ## Start local Docker dev environment
	docker compose up -d

build: ## Rebuild Docker images and start
	docker compose up --build -d

down: ## Stop local Docker dev environment
	docker compose down

logs: ## Tail all Docker container logs
	docker compose logs -f

logs-core: ## Tail Django logs
	docker compose logs -f core

dev-exec: ## Run a command in a container (e.g. make dev-exec SVC=core CMD="uv run ruff check .")
	docker compose exec $(SVC) $(CMD)

dev-manage: ## Run Django manage.py command (e.g. make dev-manage CMD=shell)
	$(MAKE) dev-exec SVC=core CMD="uv run python manage.py $(CMD)"

dev-test: ## Run Django test suite
	$(MAKE) dev-manage CMD=test

dev-makemigrations: ## Generate Django migration files (e.g. make dev-makemigrations ARGS="myapp")
	$(MAKE) dev-manage CMD="makemigrations $(ARGS)"

dev-dbshell: ## Open Django's dbshell (SQLite)
	$(MAKE) dev-manage CMD=dbshell

dev-migrate: ## Run Django database migrations + ensure cache table exists
	$(MAKE) dev-manage CMD=migrate
	$(MAKE) dev-manage CMD=createcachetable

dev-lint: ## Run linters (ruff + whitespace/final-newline on tracked text files)
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format --check ."
	./scripts/check-whitespace.sh

dev-lint-fix: ## Auto-fix lint issues
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check --fix ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format ."
	./scripts/check-whitespace.sh --fix

dev-types: ## Run ty static type checker
	$(MAKE) dev-exec SVC=core CMD="uv run ty check ."

dev-check: ## Run lint + types + tests (pre-commit habit)
	$(MAKE) dev-lint
	$(MAKE) dev-types
	$(MAKE) dev-test
