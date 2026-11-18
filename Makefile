.PHONY: up down test lint k8s-up k8s-down openapi
up:
	@echo "TODO: Satyam"
down:
	@echo "TODO: Satyam"
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
