.PHONY: help build up down logs shell migrate createsuperuser collectstatic lint lint-docs format test test-verbose test-coverage test-specific clean restart stop start ps

help:
	@echo "WhimsyBots - Available Commands"
	@echo "================================"
	@echo ""
	@echo "Docker Commands:"
	@echo "  make build              - Build Docker images"
	@echo "  make up                 - Start all services (detached)"
	@echo "  make down               - Stop all services"
	@echo "  make restart            - Restart all services"
	@echo "  make restart-app        - Restart app container"
	@echo "  make restart-worker     - Restart celery worker container"
	@echo "  make restart-scheduler  - Restart celery beat scheduler"
	@echo "  make stop               - Stop services without removing containers"
	@echo "  make start              - Start stopped services"
	@echo "  make ps                 - Show running containers"
	@echo "  make logs               - View logs from all services (follow mode)"
	@echo "  make logs-app           - View app service logs"
	@echo "  make logs-worker        - View celery worker logs"
	@echo "  make logs-scheduler     - View celery beat scheduler logs"
	@echo "  make logs-redis         - View redis service logs"
	@echo ""
	@echo "Django Commands:"
	@echo "  make shell              - Access Django shell"
	@echo "  make migrate            - Run database migrations"
	@echo "  make makemigrations     - Create new migrations"
	@echo "  make createsuperuser    - Create admin user"
	@echo "  make collectstatic      - Collect static files"
	@echo ""
	@echo "Database Commands:"
	@echo "  make db-shell           - Access PostgreSQL shell"
	@echo "  make db-dump            - Dump database to file"
	@echo "  make db-restore FILE=   - Restore database from dump"
	@echo ""
	@echo "Development Commands:"
	@echo "  make test               - Run the full test suite (pytest)"
	@echo "  make test-verbose       - Run tests with verbose output"
	@echo "  make test-coverage      - Run tests with coverage report"
	@echo "  make test-specific FILE=<path>::<Test>::<test> - Run a specific test"
	@echo "  make clean              - Remove docker volumes and containers"
	@echo ""
	@echo "Service Commands:"
	@echo "  make app-shell          - Access app container shell"
	@echo "  make celery-shell       - Access celery container shell"
	@echo ""

# Docker Commands
build:
	cd src && docker-compose build

up:
	cd src && docker-compose up -d
	@echo "Services started. Access Django admin at http://localhost:8000/admin/"

down:
	cd src && docker-compose down

restart: down up

restart-app:
	cd src && docker-compose restart app

restart-worker:
	cd src && docker-compose restart celery_worker

restart-scheduler:
	cd src && docker-compose restart celery_beat

stop:
	cd src && docker-compose stop

start:
	cd src && docker-compose start

ps:
	cd src && docker-compose ps

logs:
	cd src && docker-compose logs -f

logs-app:
	cd src && docker-compose logs -f app

logs-worker:
	cd src && docker-compose logs -f celery_worker

logs-scheduler:
	cd src && docker-compose logs -f celery_beat

logs-redis:
	cd src && docker-compose logs -f redis

# Django Commands
shell:
	cd src && docker-compose exec app python manage.py shell

migrate:
	cd src && docker-compose exec app python manage.py migrate

makemigrations:
	cd src && docker-compose exec app python manage.py makemigrations

createsuperuser:
	cd src && docker-compose exec app python manage.py createsuperuser

collectstatic:
	cd src && docker-compose exec app python manage.py collectstatic --noinput

# Database Commands
db-shell:
	cd src && docker-compose exec db psql -U $${DB_USER} -d $${DB_NAME}

db-dump:
	cd src && docker-compose exec db pg_dump -U $${DB_USER} $${DB_NAME} > ../backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "Database dumped"

db-restore:
ifndef FILE
	@echo "Usage: make db-restore FILE=backup.sql"
	@exit 1
endif
	cd src && docker-compose exec -T db psql -U $${DB_USER} $${DB_NAME} < ../$$(FILE)
	@echo "Database restored"

# Development Commands
# Tests use pytest (see src/pytest.ini) against the SQLite-backed test
# settings module (whimsybots.settings.test) which already provides fakeredis
# and shims for pgvector / ArrayField. We always run from src/ so pytest
# discovers the `tests/` tree and the test settings module on sys.path.
test:
	pytest src/tests/

test-verbose:
	pytest src/tests/ -v

test-specific:
ifndef FILE
	@echo "Usage: make test-specific FILE=test_app/test_models.py::TestClass::test_method"
	@exit 1
endif
	pytest src/tests/$(FILE)

# Service Commands
app-shell:
	cd src && docker-compose exec app /bin/sh

worker-shell:
	cd src && docker-compose exec celery_worker /bin/sh

scheduler-shell:
	cd src && docker-compose exec celery_beat /bin/sh

redis-shell:
	cd src && docker-compose exec redis redis-cli

# Cleanup
clean:
	cd src && docker-compose down -v
	@echo "All services, containers, and volumes removed"

# View environment info
env:
	@echo "Environment variables from .env:"
	@if [ -f src/.env ]; then cat src/.env; else echo "No .env file found. Copy from src/.env.example"; fi

# Development setup
dev-setup: build migrate createsuperuser
	@echo "Development environment setup complete!"

# Install dependencies locally (for IDE/editor support)
install-deps:
	pip install -r src/requirements.txt

# Build and test
build-test: build test
	@echo "Build and test completed"

# Lint — runs ruff with the project config (see pyproject.toml).
# First rollout is non-blocking: reports violations but does not fail the build.
lint:
	ruff check --config src/pyproject.toml src/ || true

# Docstring-only lint (pydocstyle / Google convention).
lint-docs:
	ruff check --config src/pyproject.toml --select D src/ || true
