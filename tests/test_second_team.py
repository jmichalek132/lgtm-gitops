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


def findings(result: subprocess.CompletedProcess) -> list[str]:
    """The individual finding lines of a rulecheck run, one string per finding.

    Every assertion below is made against this rather than against the exit
    code, because an exit code says only that SOMETHING is red. A mutation test
    whose whole claim is "removing this piece is detected" passes on any red run
    at all if it asserts the code alone, including a run that is red because the
    repository is broken in a way that has nothing to do with the mutation. That
    is the difference between proving the mutation was detected and proving the
    build was already failing.

    Findings go to STDERR, verified by reading main() in scripts/rulecheck.py
    and by running it against a mutated copy: stdout carries only the per-check
    '[<name>] ok' lines. main() prints a '[<name>] N finding(s):' header and
    then each finding indented by two spaces, so the indent is what separates
    findings from the header and from the unindented '[ownership] WARNING:'
    line the shipped example always prints. No finding contains a newline, so
    one line is exactly one finding.

    Matching per finding also stops a test passing on two DIFFERENT findings
    that happen to hold the two halves of what it asked for.
    """
    return [line.strip() for line in result.stderr.splitlines() if line.startswith("  ")]


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
    # The unmutated copy must report NO finding, not merely a tolerable exit
    # code. Every test below reads the absence of its own finding as proof that
    # the mutation went undetected, which is only true if the baseline is silent
    # to begin with.
    result = run_checks(repo_copy)
    assert findings(result) == []
    assert result.returncode in (0, 3)


def test_deleting_the_payments_rule_is_detected(repo_copy):
    (repo_copy / "rules" / "payments" / "mimir" / "checkout-alerts.yaml").unlink()
    result = run_checks(repo_copy)
    assert any(
        "rules/payments/mimir/checkout-alerts-tests.yaml" in f
        and "'checkout-alerts.yaml'" in f
        and "does not exist" in f
        for f in findings(result)
    ), (
        "deleting the payments rule did not produce the orphaned-fixture finding, "
        "so the fixture is ornamental: nothing in this repository depends on the "
        "rule file still being there"
    )
    assert result.returncode not in (0, 3)


def test_deleting_the_payments_dashboard_is_detected(repo_copy):
    shutil.rmtree(repo_copy / "dashboards" / "payments")
    result = run_checks(repo_copy)
    assert any(
        "claims team 'payments' under /dashboards/" in f
        and "no dashboards/payments/ folder exists" in f
        for f in findings(result)
    ), (
        "deleting dashboards/payments/ did not produce the per-parent finding. "
        "That finding is the only thing that catches it: the per-team probe loop "
        "reads CODEOWNERS's own stale entry as evidence and stays silent"
    )
    assert result.returncode not in (0, 3)


def test_removing_the_rules_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/rules/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    reported = findings(result)
    assert any(
        "team 'payments' has a rules/ folder but no CODEOWNERS entry" in f
        for f in reported
    ), "removing the rules entry did not produce the missing-entry finding"
    # The consequence, not just the omission: with no entry of its own, every
    # file under rules/payments/ falls through to the catch-all '*' and the
    # payments team can no longer approve changes to its own alerting rules.
    assert any(
        "rules/payments/mimir/checkout-alerts.yaml" in f
        and "@org/platform" in f
        and "through pattern '*'" in f
        and "must resolve to @org/payments" in f
        for f in reported
    ), "the payments rule file was not reported as resolving to the wrong owner"
    assert result.returncode not in (0, 3)


def test_removing_the_dashboards_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/dashboards/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    reported = findings(result)
    assert any(
        "team 'payments' has a dashboards/ folder but no CODEOWNERS entry" in f
        for f in reported
    ), "removing the dashboards entry did not produce the missing-entry finding"
    # A team's rules and its dashboards are ONE ownership boundary, so owning
    # only the rules is not partial success: the dashboard falls through to the
    # default owner exactly as the rule file would.
    assert any(
        "dashboards/payments/checkout-overview.json" in f
        and "@org/platform" in f
        and "through pattern '*'" in f
        and "must resolve to @org/payments" in f
        for f in reported
    ), "the payments dashboard was not reported as resolving to the wrong owner"
    assert result.returncode not in (0, 3)


def test_giving_payments_paths_to_another_team_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    text = path.read_text() + "\n/rules/payments/ @org/platform\n"
    path.write_text(text)
    result = run_checks(repo_copy)
    # Pinning the PATTERN is what makes this test about last-match-wins rather
    # than about ownership in general. The finding names '/rules/payments/', the
    # appended duplicate, as the deciding pattern; the sibling test above, where
    # no entry exists at all, names the catch-all '*' instead. Asserting only
    # that some owner is wrong would not tell those two apart.
    assert any(
        "rules/payments/mimir/checkout-alerts.yaml" in f
        and "@org/platform" in f
        and "through pattern '/rules/payments/'" in f
        and "must resolve to @org/payments" in f
        for f in findings(result)
    ), (
        "GitHub resolves CODEOWNERS by the last matching pattern, and the "
        "appended line repeats the exact pattern '/rules/payments/' verbatim, "
        "so it does not add @org/platform as a co-owner alongside @org/payments: "
        "it replaces the earlier line outright and becomes the sole resolved "
        "owner. Every path under rules/payments/ now belongs to @org/platform "
        "alone, and the payments team can no longer approve changes to its own "
        "alerting rules."
    )
    assert result.returncode not in (0, 3)
