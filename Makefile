.PHONY: test install-dev

PYTHON ?= python3

install-dev:
	$(PYTHON) -m pip install --user pytest pyyaml

test: install-dev
	$(PYTHON) -m pytest -v
