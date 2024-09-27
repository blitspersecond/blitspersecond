# Makefile

install:
	pip install -e .[dev]

format:
	black blitspersecond

lint:
	mypy blitspersecond

test:
	pytest --cov=blitspersecond

coverage:
	pytest --cov=blitspersecond --cov-report=html

package:
	python setup.py sdist bdist_wheel

clean:
	rm -rf build dist *.egg-info .pytest_cache .coverage
	find . -name "__pycache__" -exec rm -rf {} +

.PHONY: install format lint test coverage package clean
