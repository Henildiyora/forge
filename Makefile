PYTHON ?= python3

.PHONY: install test lint run api

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .
	mypy swarm tests

run:
	$(PYTHON) -m swarm.main

api:
	uvicorn swarm.api.app:create_app --factory --reload
