PYTHON ?= python3

.PHONY: install test e2e lint run api clean

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	pytest -q

e2e:
	pytest -q -m e2e

lint:
	ruff check forge tests
	mypy forge tests

run:
	$(PYTHON) -m forge.main

api:
	uvicorn forge.api.app:create_app --factory --reload

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
