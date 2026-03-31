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
	@echo "  make stop               - Stop services without removing containers"
	@echo "  make start              - Start stopped services"
	@echo "  make ps                 - Show running containers"
	@echo "  make logs               - View logs from all services (follow mode)"
	@echo "  make logs-app           - View app service logs"
	@echo "  make logs-celery        - View celery worker logs"
	@echo "  make logs-beat          - View celery beat logs"
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

logs-celery:
	cd src && docker-compose logs -f celery

logs-beat:
	cd src && docker-compose logs -f celery_beat

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
	cd src && docker-compose exec app /bin/bash

celery-shell:
	cd src && docker-compose exec celery /bin/bash

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
