#!/usr/bin/env bash
# The single validation entrypoint. Runs identically on a laptop and in CI so a
# contributor can reproduce any failure without pushing.
#
# Stages are ordered cheapest-first: the mistakes people actually make are
# caught in seconds rather than after a full render.
set -uo pipefail
cd "$(dirname "$0")/.."

STATUS=0
stage() { printf '\n=== %s ===\n' "$1"; }
run()   { "$@" || STATUS=1; }

have() { command -v "$1" >/dev/null 2>&1; }

require() {
  if ! have "$1"; then
    printf 'missing required tool: %s\n' "$1" >&2
    STATUS=1
    return 1
  fi
}

stage "1-2. structure, contract, fixtures, environment matchers, CODEOWNERS, dashboards"
require python3 && run python3 scripts/rulecheck.py .

# macOS ships bash 3.2, which has no `mapfile`. This keeps the script working
# with the system bash so `make check` behaves identically everywhere.
# Reads NUL-delimited paths from stdin into the global array FILES.
# Verified: both `find -print0` and `sort -z` work on BSD and GNU userland.
collect() {
  FILES=()
  while IFS= read -r -d '' f; do FILES+=("$f"); done
}

stage "3. contract (promruval)"
if require promruval; then
  # promruval needs explicit paths; fixtures are not rule files.
  # Split by dialect. promruval parses PromQL by default; --support-loki is
  # required for LogQL rules and --support-mimir for Mimir-flavoured ones.
  # Verified against the promruval README.
  collect < <(find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -gt 0 ]; then
    run promruval validate --config-file=./validation.yaml --support-mimir "${FILES[@]}"
  else
    printf 'no mimir/prometheus rule files found\n'
  fi

  collect < <(find rules -path 'rules/*/loki/*' -name '*.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -gt 0 ]; then
    run promruval validate --config-file=./validation.yaml --support-loki "${FILES[@]}"
  else
    printf 'no loki rule files found\n'
  fi
fi

stage "4. syntax (promtool, lokitool)"
if require promtool; then
  collect < <(find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -gt 0 ]; then
    run promtool check rules "${FILES[@]}"
  else
    printf 'no mimir/prometheus rule files found\n'
  fi
fi
# lokitool is REQUIRED, not optional. Making it optional meant CI silently
# skipped every LogQL syntax check, since it was never installed there.
# Set ALLOW_MISSING_LOKITOOL=1 for a local run without it, never in CI.
if [ "${ALLOW_MISSING_LOKITOOL:-0}" = "1" ] && ! have lokitool; then
  printf 'WARNING: lokitool missing, LogQL syntax NOT checked (local override)\n' >&2
elif require lokitool; then
  collect < <(find rules -path 'rules/*/loki/*' -name '*.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -gt 0 ]; then
    run lokitool rules check "${FILES[@]}"
  else
    printf 'no loki rule files found\n'
  fi
fi

stage "5. unit tests (promtool test rules)"
# Executing a fixture is not the same as a fixture testing something: promtool
# prints SUCCESS and exits 0 for `tests: []`. That assertion lives in stage 1
# (rulecheck's `fixtures` check), which parses every fixture and fails when its
# `tests` or `rule_files` list is absent or empty.
if require promtool; then
  collect < <(find rules -name '*-tests.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -eq 0 ]; then
    printf 'no test fixtures found\n'
  else
    for f in "${FILES[@]}"; do
      # promtool resolves rule_files relative to the fixture, so run in its directory.
      ( cd "$(dirname "$f")" && promtool test rules "$(basename "$f")" ) || STATUS=1
    done
  fi
fi

stage "6. render (helm template) and Kubernetes constraints"
if require helm; then
  run ./tests/chart_test.sh
  run python3 scripts/render_assert.py
fi

if [ "$STATUS" -eq 0 ]; then
  printf '\nall checks passed\n'
else
  printf '\nCHECKS FAILED\n' >&2
fi
exit "$STATUS"
