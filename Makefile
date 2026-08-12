.PHONY: check test lint

# Everything CI runs. Identical locally: CI invokes exactly this target and
# nothing else, so a green laptop run means a green pipeline.
check: test
	./scripts/check.sh

# Fast inner loop: chart behaviour plus helper unit tests.
test:
	./tests/chart_test.sh
	python3 -m pytest tests/ -q

lint:
	helm lint .
