.PHONY: up down test lint k8s-up k8s-down openapi
up:
	@docker compose -f infra/docker/docker-compose.yml up -d --build
down:
	@docker compose -f infra/docker/docker-compose.yml down
k8s-up:
	@echo "TODO: Satyam"
k8s-down:
	@echo "TODO: Satyam"
openapi:
	@echo "TODO: Satyam"
test:
	cd backend && python -m pytest
lint:
	cd backend && ruff check .
