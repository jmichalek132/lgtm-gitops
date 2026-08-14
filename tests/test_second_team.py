"""The payments fixture exists to be depended upon.

Each test here fails if a piece of the second team is removed. Without them,
the fixture is decoration that a future refactor would delete without noticing.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_checks(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "scripts/rulecheck.py", str(root)],
        cwd=root, capture_output=True, text=True,
    )


@pytest.fixture
def repo_copy(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT, dest,
        ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__",
                                      ".superpowers"),
    )
    # check_publishability (Gate 1, run as part of every rulecheck.py invocation)
    # discovers files through scripts/privatescan.py's iter_scannable_files,
    # which shells out to `git ls-files` and treats a failed discovery as a
    # finding rather than a silent skip, on purpose: a scan that could not run
    # must never be reportable as a scan that found nothing. That means a
    # repo_copy with no .git at all is not a clean tree to run rulecheck.py
    # against, it is a discovery FAILURE, which fails every test below at
    # collection time for a reason that has nothing to do with the mutation
    # under test. `git init` here gives the copy a fresh, empty repository, not
    # the original's history: nothing is staged, so `git ls-files --cached`
    # stays empty and `git ls-files --others --exclude-standard` reports every
    # copied file as untracked, which is all iter_scannable_files needs. The
    # same problem, and the same fix, already exists in
    # tests/test_check_sh.py's own repo_copy fixture, which keeps the real
    # .git for exactly this reason; the difference here is deliberate, since
    # copying the real .git would also drag in history the mutations below
    # have no reason to touch.
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    return dest


def test_baseline_is_clean(repo_copy):
    assert run_checks(repo_copy).returncode in (0, 3)


def test_deleting_the_payments_rule_is_detected(repo_copy):
    (repo_copy / "rules" / "payments" / "mimir" / "checkout-alerts.yaml").unlink()
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "deleting the payments rule left every check green, so the fixture is ornamental"
    )


def test_deleting_the_payments_dashboard_is_detected(repo_copy):
    shutil.rmtree(repo_copy / "dashboards" / "payments")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "deleting the payments dashboard left every check green"
    )


def test_removing_the_rules_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/rules/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3)


def test_removing_the_dashboards_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/dashboards/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3)


def test_giving_payments_paths_to_another_team_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    text = path.read_text() + "\n/rules/payments/ @org/platform\n"
    path.write_text(text)
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "GitHub resolves CODEOWNERS by the last matching pattern, and the "
        "appended line repeats the exact pattern '/rules/payments/' verbatim, "
        "so it does not add @org/platform as a co-owner alongside @org/payments: "
        "it replaces the earlier line outright and becomes the sole resolved "
        "owner. Every path under rules/payments/ now belongs to @org/platform "
        "alone, and the payments team can no longer approve changes to its own "
        "alerting rules."
    )
