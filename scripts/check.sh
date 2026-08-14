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

# Standing facts about this repository that a green run does not make go away.
# Printed immediately before the final status so they cannot scroll past.
NOTES=()
note() { NOTES+=("$1"); }

# Verification this run did NOT perform. Unlike a note, a caveat changes the
# final status line: every check that ran may have passed, but a run that
# skipped one cannot claim a clean bill of health it did not earn.
CAVEATS=()
caveat() { CAVEATS+=("$1"); }

have() { command -v "$1" >/dev/null 2>&1; }

require() {
  if ! have "$1"; then
    printf 'missing required tool: %s\n' "$1" >&2
    STATUS=1
    return 1
  fi
}

stage "1-2. structure, contract, fixtures, environment matchers, ownership, CODEOWNERS, dashboards"
# Exit 3 is rulecheck's "everything passed, but this repository still ships the
# placeholder organisation and therefore enforces nothing on GitHub". That is not
# a build failure (an unadopted example is entitled to say so), but it must not
# scroll past either, so it is carried to the end as a caveat: this run did not
# verify that GitHub enforces anything, so it cannot claim a clean bill of health.
if require python3; then
  python3 scripts/rulecheck.py .
  case "$?" in
    0) ;;
    3) caveat 'ownership is UNCONFIGURED (ownership.yaml: configured: false). CODEOWNERS names a placeholder organisation, so GitHub requires review from nobody on any path in this repository.' ;;
    *) STATUS=1 ;;
  esac
fi

# macOS ships bash 3.2, which has no `mapfile`. This keeps the script working
# with the system bash so `make check` behaves identically everywhere.
# Reads NUL-delimited paths from stdin into the global array FILES.
# Verified: both `find -print0` and `sort -z` work on BSD and GNU userland.
collect() {
  FILES=()
  while IFS= read -r -d '' f; do FILES+=("$f"); done
}

# Discovery that FAILS when discovery fails. `collect < <(find ... | sort -z)`
# threw the exit status of both commands away: process substitution does not
# propagate it and the pipeline status is not the reader's. A `find` that died
# produced an empty FILES, which is indistinguishable from "this repository has
# no rule files", so every stage reported "no files found" and the run ended
# `all checks passed` having validated nothing. That silent skip is the exact
# failure this repository exists to prevent, so discovery is run through a
# temporary file where both statuses can be seen and checked.
collect_find() {
  local raw sorted rc
  FILES=()
  raw=$(mktemp "${TMPDIR:-/tmp}/observability-rules-find.XXXXXX") || {
    printf 'unable to create temporary file for rule discovery\n' >&2
    STATUS=1
    return 1
  }
  sorted="${raw}.sorted"
  rc=0

  if ! find "$@" -print0 >"$raw"; then
    printf 'rule discovery failed: find %s\n' "$*" >&2
    STATUS=1
    rc=1
  elif ! sort -z "$raw" >"$sorted"; then
    printf 'rule discovery failed while sorting results\n' >&2
    STATUS=1
    rc=1
  else
    collect <"$sorted"
  fi

  rm -f "$raw" "$sorted"
  return "$rc"
}

stage "3. contract (promruval)"
if require promruval; then
  # promruval needs explicit paths; fixtures are not rule files.
  # Split by dialect. promruval parses PromQL by default; --support-loki is
  # required for LogQL rules and --support-mimir for Mimir-flavoured ones.
  # Verified against the promruval README.
  if collect_find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml'; then
    if [ "${#FILES[@]}" -gt 0 ]; then
      run promruval validate --config-file=./validation.yaml --support-mimir "${FILES[@]}"
    else
      printf 'no mimir/prometheus rule files found\n'
    fi
  fi

  if collect_find rules -path 'rules/*/loki/*' -name '*.yaml'; then
    if [ "${#FILES[@]}" -gt 0 ]; then
      run promruval validate --config-file=./validation.yaml --support-loki "${FILES[@]}"
    else
      printf 'no loki rule files found\n'
    fi
  fi
fi

stage "4. syntax (promtool, lokitool)"
if require promtool; then
  if collect_find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml'; then
    if [ "${#FILES[@]}" -gt 0 ]; then
      run promtool check rules "${FILES[@]}"
    else
      printf 'no mimir/prometheus rule files found\n'
    fi
  fi
fi
# lokitool is REQUIRED, not optional. Making it optional meant CI silently
# skipped every LogQL syntax check, since it was never installed there.
# Set ALLOW_MISSING_LOKITOOL=1 for a local run without it, never in CI.
if [ "${ALLOW_MISSING_LOKITOOL:-0}" = "1" ] && ! have lokitool; then
  printf 'WARNING: lokitool missing, LogQL syntax NOT checked (local override)\n' >&2
  caveat 'LogQL syntax was NOT checked: lokitool is missing and ALLOW_MISSING_LOKITOOL=1 was set. Every loki rule in this repository is unvalidated by this run.'
elif require lokitool; then
  if collect_find rules -path 'rules/*/loki/*' -name '*.yaml'; then
    if [ "${#FILES[@]}" -gt 0 ]; then
      run lokitool rules check "${FILES[@]}"
    else
      printf 'no loki rule files found\n'
    fi
  fi
fi

stage "5. unit tests (promtool test rules)"
# Executing a fixture is not the same as a fixture testing something: promtool
# prints SUCCESS and exits 0 for `tests: []`. That assertion lives in stage 1
# (rulecheck's `fixtures` check), which parses every fixture and fails when its
# `tests` or `rule_files` list is absent or empty.
if require promtool; then
  if collect_find rules -name '*-tests.yaml'; then
    if [ "${#FILES[@]}" -eq 0 ]; then
      printf 'no test fixtures found\n'
    else
      for f in "${FILES[@]}"; do
        # promtool resolves rule_files relative to the fixture, so run in its directory.
        ( cd "$(dirname "$f")" && promtool test rules "$(basename "$f")" ) || STATUS=1
      done
    fi
  fi
fi

stage "6. render (helm template) and Kubernetes constraints"
if require helm; then
  run ./tests/chart_test.sh
  run python3 scripts/render_assert.py
fi

stage "8. private term scan (Gate 2)"
# Gate 2 is fail-closed. An absent denylist is a FAILURE, not a caveat: the
# caveat mechanism exits 0, so Make, Actions and pre-push hooks would all read
# "could not run" as success.
#
# CI=true is an ordinary environment value, not authentication. This skip stops
# an absent denylist from accidentally becoming a green local run; it cannot
# stop a deliberate bypass, and section 9 of the design says so.
if [ "${PUBLISHABILITY_PRIVATE_SCAN:-}" = "skip-untrusted-ci" ] && [ "${CI:-}" = "true" ]; then
  caveat 'the private term scan did NOT run: this job is public-checks only and deliberately holds no denylist.'
elif [ -n "${PUBLISHABILITY_PRIVATE_SCAN:-}" ] && [ "${PUBLISHABILITY_PRIVATE_SCAN}" != "skip-untrusted-ci" ]; then
  printf 'PUBLISHABILITY_PRIVATE_SCAN=%s is not a recognised value; the only accepted skip is skip-untrusted-ci under CI=true\n' \
    "${PUBLISHABILITY_PRIVATE_SCAN}" >&2
  STATUS=1
elif [ "${PUBLISHABILITY_PRIVATE_SCAN:-}" = "skip-untrusted-ci" ]; then
  printf 'PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci is only accepted when CI=true\n' >&2
  STATUS=1
elif require python3; then
  python3 scripts/privatescan.py . || STATUS=1
fi

if [ "${#NOTES[@]}" -gt 0 ]; then
  printf '\n'
  for n in "${NOTES[@]}"; do printf 'NOTE: %s\n' "$n"; done
fi

if [ "$STATUS" -ne 0 ]; then
  printf '\nCHECKS FAILED\n' >&2
elif [ "${#CAVEATS[@]}" -gt 0 ]; then
  # Deliberately not the words "all checks passed": a run that skipped a check
  # must not be greppable, or readable, as one that did not.
  printf '\nCHECKS INCOMPLETE: everything that ran passed, but this run did NOT verify:\n'
  for c in "${CAVEATS[@]}"; do printf '  - %s\n' "$c"; done
  printf 'This is not a clean bill of health. Only the deliberate public-checks CI job may end here.\n'
else
  printf '\nall checks passed\n'
fi
exit "$STATUS"
