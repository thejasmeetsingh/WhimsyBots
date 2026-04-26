.PHONY: help build up down logs shell migrate createsuperuser collectstatic lint format test clean restart stop start ps

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
	@echo "  make restart-beat-worker - Restart celery beat worker container"
	@echo "  make restart-default-worker - Restart celery default worker container"
	@echo "  make restart-scheduler  - Restart celery beat scheduler"
	@echo "  make stop               - Stop services without removing containers"
	@echo "  make start              - Start stopped services"
	@echo "  make ps                 - Show running containers"
	@echo "  make logs               - View logs from all services (follow mode)"
	@echo "  make logs-app           - View app service logs"
	@echo "  make logs-beat-worker   - View celery beat worker logs"
	@echo "  make logs-default-worker - View celery default worker logs"
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
	@echo "  make test               - Run tests"
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

restart-beat-worker:
	cd src && docker-compose restart celery_beat_worker

restart-default-worker:
	cd src && docker-compose restart celery_default_worker

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

logs-beat-worker:
	cd src && docker-compose logs -f celery_beat_worker

logs-default-worker:
	cd src && docker-compose logs -f celery_default_worker

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
test:
	cd src && docker-compose exec app python manage.py test

# Service Commands
app-shell:
	cd src && docker-compose exec app /bin/sh

beat-worker-shell:
	cd src && docker-compose exec celery_beat_worker /bin/sh

default-worker-shell:
	cd src && docker-compose exec celery_default_worker /bin/sh

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
