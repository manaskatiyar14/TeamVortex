PYTHON ?= python3
VENV := .venv
VENV_PY := $(VENV)/bin/python

.PHONY: setup run test clean distclean

setup:
	$(PYTHON) -m venv $(VENV)
	"$(VENV_PY)" -m pip install -U pip
	"$(VENV_PY)" -m pip install -r requirements.txt

run:
	@if [ ! -x "$(VENV_PY)" ]; then echo "ERROR: environment is not set up. Run: make setup"; echo "Then export AI_API_KEY and re-run make run."; exit 1; fi
	"$(VENV_PY)" main.py $(ARGS)

test:
	@if [ ! -x "$(VENV_PY)" ]; then echo "ERROR: environment is not set up. Run: make setup"; exit 1; fi
	"$(VENV_PY)" -m pytest tests/

clean:
	rm -rf .pytest_cache .coverage htmlcov .mypy_cache .ruff_cache workspaces
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type f -name "*.log" -delete 2>/dev/null || true

distclean: clean
	rm -rf $(VENV)
