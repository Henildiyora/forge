PYTHON ?= .venv/bin/python

.PHONY: install fixtures test lint run-offline benchmark dashboard stack-up stack-down seed clean

install:
	$(PYTHON) -m pip install -e ".[dev]"

fixtures:
	$(PYTHON) -m benchmark.seed_prometheus --generate-only

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check swarm benchmark tests
	$(PYTHON) -m mypy swarm benchmark

run-offline:
	$(PYTHON) -m swarm run --scenario payment_timeout --offline --skip-llm --max-repair-attempts 0

benchmark:
	$(PYTHON) -m benchmark.benchmark --offline

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py

stack-up:
	docker compose up -d --build

stack-down:
	docker compose down

seed:
	$(PYTHON) -m benchmark.seed_prometheus --all

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .swarm
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
