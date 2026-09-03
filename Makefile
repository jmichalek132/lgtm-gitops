.PHONY: check test lint setup check-docker

# Everything CI runs. Identical locally: CI invokes exactly this target and
# nothing else, so a green laptop run means a green pipeline.
check: test
	./scripts/check.sh

# One-time setup (and again after a pin bump): venv, python deps, and the
# four pinned tools into .venv/bin, checksum-verified. Pins are parsed from
# .github/workflows/ci.yaml so this cannot drift from CI.
setup:
	./scripts/setup.sh

# The same `make check` CI runs, inside a container holding the pinned
# toolchain, against a bind mount of this repository. Needs docker only.
check-docker:
	./scripts/check_docker.sh

# Fast inner loop: chart behaviour plus helper unit tests.
test:
	./tests/chart_test.sh
	python3 -m pytest tests/ -q

lint:
	helm lint .
