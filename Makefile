.PHONY: test doctor serve compile clean

PYTHON ?= python3

compile:
	$(PYTHON) -m compileall -q polyglotmaker tests

test:
	$(PYTHON) -m unittest discover -s tests -v

doctor:
	$(PYTHON) -m polyglotmaker doctor

serve:
	$(PYTHON) -m polyglotmaker serve --port 8765

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.py[co]' -delete
