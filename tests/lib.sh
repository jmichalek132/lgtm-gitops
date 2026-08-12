#!/usr/bin/env bash
# Minimal assertion helpers. No test framework dependency on purpose:
# the whole repo needs to be runnable with helm, python and bash alone.

FAILURES=0
PASSES=0

pass() { PASSES=$((PASSES + 1)); printf '  ok   %s\n' "$1"; }

fail() {
  FAILURES=$((FAILURES + 1))
  printf '  FAIL %s\n' "$1"
  [ -n "${2:-}" ] && printf '       %s\n' "$2"
}

assert_contains() {
  local haystack="$1" needle="$2" name="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    pass "$name"
  else
    fail "$name" "expected to find: $needle"
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" name="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    fail "$name" "expected NOT to find: $needle"
  else
    pass "$name"
  fi
}

assert_count() {
  local haystack="$1" needle="$2" expected="$3" name="$4"
  local actual
  actual=$(printf '%s' "$haystack" | grep -cF -- "$needle" || true)
  if [ "$actual" = "$expected" ]; then
    pass "$name"
  else
    fail "$name" "expected $expected occurrences of '$needle', got $actual"
  fi
}

# Asserts the command fails AND its stderr mentions the given text.
assert_fails_with() {
  local expected="$1" name="$2"; shift 2
  local output status
  output=$("$@" 2>&1) && status=0 || status=$?
  if [ "$status" -eq 0 ]; then
    fail "$name" "expected failure, but command succeeded"
  elif printf '%s' "$output" | grep -qF -- "$expected"; then
    pass "$name"
  else
    fail "$name" "failed as expected but message lacked: $expected"
  fi
}

summary() {
  printf '\n%s passed, %s failed\n' "$PASSES" "$FAILURES"
  [ "$FAILURES" -eq 0 ]
}
