.PHONY: up down test lint k8s-up k8s-down openapi

up:
	@docker compose -f infra/docker/docker-compose.yml up -d --build

down:
	@docker compose -f infra/docker/docker-compose.yml down

k8s-up:
	@kubectl apply -k infra/k8s/overlays/local
	@kubectl -n dayforce-local rollout status deployment/postgres
	@kubectl -n dayforce-local rollout status deployment/backend
	@kubectl -n dayforce-local rollout status deployment/frontend

k8s-down:
	@kubectl -n dayforce-local scale deployment/frontend deployment/postgres --replicas=0

openapi:
	@echo "TODO: Satyam"

test:
	cd backend && python -m pytest

lint:
	cd backend && ruff check .
