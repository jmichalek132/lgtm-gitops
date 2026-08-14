"""Tests for scripts/check.sh itself, the one script nothing else was checking.

check.sh is the only gate between a contributor and production alerting, and its
signature failure mode is a stage that reports success while never having run.
These execute the real script; they are the only way to observe that.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_SH = REPO_ROOT / "scripts" / "check.sh"

# Emitted by every discovery call site when it finds nothing. A healthy run of
# this repository must print none of them: there are rules for all three targets
# and a test fixture, so any of these means discovery silently came back empty.
EMPTY_DISCOVERY = (
    "no mimir/prometheus rule files found",
    "no loki rule files found",
    "no test fixtures found",
)

# check.sh's own `require` already fails a run missing one of these, and CI
# installs all four, so that is where absence is actually enforced. These
# tests exist to observe check.sh's own behaviour, not to re-derive it: run
# them for real when the toolchain is here, skip (not fail) when it is not,
# so a workstation without e.g. lokitool gets a clean signal instead of a
# failure that has nothing to do with the code under test.
REQUIRED_TOOLS = ("helm", "promtool", "promruval", "lokitool")
MISSING_TOOLS = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
requires_toolchain = pytest.mark.skipif(
    bool(MISSING_TOOLS),
    reason=f"missing required tool(s) for check.sh: {', '.join(MISSING_TOOLS)}",
)


def run_check(env_path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_path:
        env["PATH"] = env_path
    # These tests are about rule discovery and the render pipeline, not Gate 2
    # (the private term scan), and they run against the real checkout, which
    # ships with no PUBLISHABILITY_TERMS_FILE configured. A forced override,
    # not setdefault: `make check` also runs `make test` (this very suite)
    # under one inherited environment, so an ambient PUBLISHABILITY_PRIVATE_SCAN
    # or CI value (e.g. set by hand to exercise Gate 2's own tests below) must
    # not leak into what this helper is supposed to guarantee unconditionally.
    # Gate 2's own fail-closed tests below construct their own env from
    # scratch via run_check_sh and do not go through this helper.
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "skip-untrusted-ci"
    env["CI"] = "true"
    return subprocess.run(
        [str(CHECK_SH)], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )


@pytest.fixture
def repo_copy(tmp_path) -> Path:
    """A full filesystem copy of this repository, working-tree changes
    included, so Gate 2's env-driven behaviour can be exercised without ever
    touching the real checkout.

    check.sh finds its own root via `cd "$(dirname "$0")/.."`, not an
    argument, so testing it against isolated state means copying the whole
    tree rather than passing a path: .git included, so the git ls-files
    calls scripts/privatescan.py depends on still see the same
    tracked/untracked files the real repo has (including any uncommitted
    change under test), and .venv/__pycache__/.pytest_cache excluded, since
    they are gitignored, irrelevant to check.sh, and only add copy time.
    """
    dest = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT,
        dest,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    return dest


def run_check_sh(repo_copy: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(repo_copy / "scripts" / "check.sh")],
        cwd=repo_copy,
        env=env,
        capture_output=True,
        text=True,
    )


@requires_toolchain
def test_check_sh_passes_and_actually_discovers_rules():
    proc = run_check()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # This repository ships with ownership.yaml: configured: false, so a clean
    # run is not "all checks passed": it is the CAVEATS variant that says so
    # explicitly and refuses to call an unconfigured repository a clean bill
    # of health. "all checks passed" is asserted absent on purpose.
    assert "CHECKS INCOMPLETE" in proc.stdout
    assert "all checks passed" not in proc.stdout
    for message in EMPTY_DISCOVERY:
        assert message not in proc.stdout, (
            f"{message!r}: a stage found no files in a repository that has them, "
            f"so it validated nothing while reporting success"
        )


@requires_toolchain
def test_check_sh_fails_when_rule_discovery_fails(tmp_path):
    # Reproduces the silent skip: `collect < <(find ... | sort -z)` discarded the
    # exit status of both commands, so a find that died looked exactly like a
    # repository with no rules, and the run ended `all checks passed`.
    stub = tmp_path / "bin"
    stub.mkdir()
    broken_find = stub / "find"
    broken_find.write_text("#!/bin/sh\nexit 2\n")
    broken_find.chmod(0o755)

    proc = run_check(env_path=f"{stub}:{os.environ['PATH']}")

    assert "rule discovery failed" in proc.stderr, proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "all checks passed" not in proc.stdout
    for message in EMPTY_DISCOVERY:
        assert message not in proc.stdout, (
            f"{message!r}: a failed discovery must not be reported as an empty one"
        )


# Gate 2 wiring (Task 9). An earlier design revision routed an absent
# denylist through the existing caveat mechanism above (CHECKS INCOMPLETE,
# exit 0); that was rejected because Make, Actions and pre-push hooks all
# read "exit 0" as success regardless of what the text says. These tests
# exercise the corrected, fail-closed wiring directly against the real
# script rather than against scripts/privatescan.py in isolation, because
# the point under test is check.sh's own branching (default/skip/reject),
# not the CLI's exit code by itself, which tests/test_privatescan.py
# already covers.


def test_check_sh_fails_when_denylist_is_absent(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env.pop("PUBLISHABILITY_PRIVATE_SCAN", None)
    result = run_check_sh(repo_copy, env)
    assert result.returncode != 0
    assert "CHECKS FAILED" in result.stdout + result.stderr


@requires_toolchain
def test_check_sh_honours_the_named_skip_only_under_ci(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "skip-untrusted-ci"

    env["CI"] = "true"
    result = run_check_sh(repo_copy, env)
    assert result.returncode == 0, result.stdout + result.stderr

    env.pop("CI")
    assert run_check_sh(repo_copy, env).returncode != 0


def test_check_sh_rejects_any_other_skip_value(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "yes"
    env["CI"] = "true"
    result = run_check_sh(repo_copy, env)
    assert result.returncode != 0
    assert "CHECKS FAILED" in result.stdout + result.stderr


@requires_toolchain
def test_ci_sentence_does_not_contradict_a_deliberate_skip(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "skip-untrusted-ci"
    env["CI"] = "true"
    result = run_check_sh(repo_copy, env)
    out = result.stdout
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHECKS INCOMPLETE" in out
    assert "CI must never end here" not in out
