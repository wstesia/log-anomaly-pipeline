.PHONY: install lint test demo clean

install:
	python3 -m pip install -e ".[dev]"

lint:
	python3 -m ruff check src tests

test:
	python3 -m pytest --cov=log_analyzer --cov-report=term-missing

demo:
	log-analyzer demo --output-dir artifacts/demo --minutes 180 --seed 42

clean:
	rm -rf artifacts data .pytest_cache .ruff_cache htmlcov .coverage
