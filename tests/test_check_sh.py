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
    return subprocess.run(
        [str(CHECK_SH)], cwd=REPO_ROOT, env=env, capture_output=True, text=True
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
