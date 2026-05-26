.PHONY: up down install seed embed psql

up:
	docker compose up -d
	@echo "Waiting for Postgres..."
	@until docker compose exec -T postgres pg_isready -U app -d recommend > /dev/null 2>&1; do sleep 1; done
	@echo "Postgres ready."

down:
	docker compose down

install:
	python3.11 -m venv .venv && \
	.venv/bin/pip install -U pip && \
	.venv/bin/pip install -r requirements.txt

seed:
	.venv/bin/python scripts/seed_products.py

embed:
	.venv/bin/python scripts/embed_products.py

psql:
	docker compose exec postgres psql -U app -d recommend
