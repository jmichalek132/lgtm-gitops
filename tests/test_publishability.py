import re
import textwrap
from pathlib import Path

import pytest

from scripts.rulecheck import (
    PublishabilityConfigError,
    check_publishability,
    load_publishability_config,
    scan_text_with_patterns,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / "publishability.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_shipped_config_loads():
    patterns = load_publishability_config(REPO_ROOT)
    assert [p["id"] for p in patterns] == ["macos-home-path", "linux-home-path"]


def test_shipped_patterns_do_not_match_any_line_of_their_own_config():
    """The whole no-self-exemption design rests on this property: Gate 1 will
    scan this file like any other tracked file, so no pattern may match any
    line of it, not just the line that defines the pattern."""
    text = (REPO_ROOT / "publishability.yaml").read_text(encoding="utf-8")
    for pattern in load_publishability_config(REPO_ROOT):
        rx = re.compile(pattern["regex"])
        for lineno, line in enumerate(text.splitlines(), start=1):
            assert not rx.search(line), (
                f"pattern {pattern['id']} matches its own configuration file "
                f"at line {lineno}: {line!r}"
            )


def test_shipped_patterns_still_catch_a_real_path():
    patterns = {p["id"]: re.compile(p["regex"]) for p in load_publishability_config(REPO_ROOT)}
    # Built by concatenation so the literal never appears in a tracked file.
    probe = "/" + "Users/alice/notes"
    assert patterns["macos-home-path"].search(probe)
    assert not patterns["linux-home-path"].search(probe)


def test_missing_config_fails(tmp_path):
    with pytest.raises(PublishabilityConfigError, match="not found"):
        load_publishability_config(tmp_path)


@pytest.mark.parametrize(
    "body, expected",
    [
        ("[]", "mapping"),
        ("version: 1\n", "exactly"),
        ("version: 2\npatterns: [{id: a, regex: x, message: y}]\n", "version"),
        ("version: true\npatterns: [{id: a, regex: x, message: y}]\n", "version"),
        ("version: 1\npatterns: []\n", "non-empty"),
        ("version: 1\npatterns: [{id: a, regex: x}]\n", "exactly"),
        ("version: 1\npatterns: [{id: a, regex: x, message: y, extra: z}]\n", "exactly"),
        ("version: 1\npatterns: [{id: 'A', regex: x, message: y}]\n", "id"),
        ("version: 1\npatterns: [{id: a, regex: '', message: y}]\n", "regex"),
        ("version: 1\npatterns: [{id: a, regex: 'x*', message: y}]\n", "empty string"),
        ("version: 1\npatterns: [{id: a, regex: '[', message: y}]\n", "compile"),
        ("version: 1\npatterns: [{id: a, regex: x, message: ''}]\n", "message"),
        ("version: 1\npatterns: [{id: a, regex: x, message: \"y\\nz\"}]\n", "message"),
        (
            "version: 1\npatterns: [{id: a, regex: x, message: y}, {id: a, regex: z, message: w}]\n",
            "unique",
        ),
        (
            "version: 1\npatterns: [{id: a, regex: x, message: y}, {id: b, regex: x, message: w}]\n",
            "unique",
        ),
        ("version: 1\nversion: 1\npatterns: [{id: a, regex: x, message: y}]\n", "duplicate"),
        ("version: 1\npatterns: [{id: a, regex: x, message: y}]\nunknown: 1\n", "exactly"),
        ("version: 1\npatterns: {}\n", "non-empty"),
        ("version: 1\npatterns: [1]\n", "exactly"),
        ("version: 1\npatterns: [{id: 1, regex: x, message: y}]\n", "id"),
        ("version: 1\npatterns: [{id: a, regex: 1, message: y}]\n", "regex"),
        ("version: 1\npatterns: [{id: a, regex: x, message: 1}]\n", "message"),
        (
            "version: 1\npatterns:\n  - id: a\n    id: b\n    regex: x\n    message: y\n",
            "duplicate",
        ),
    ],
)
def test_malformed_config_fails(tmp_path, body, expected):
    root = write_config(tmp_path, body)
    with pytest.raises(PublishabilityConfigError, match=expected):
        load_publishability_config(root)


def test_overlong_regex_fails(tmp_path):
    root = write_config(
        tmp_path,
        "version: 1\npatterns: [{id: a, regex: '%s', message: y}]\n" % ("a" * 513),
    )
    with pytest.raises(PublishabilityConfigError, match="512"):
        load_publishability_config(root)


def test_overlong_message_fails(tmp_path):
    root = write_config(
        tmp_path,
        "version: 1\npatterns: [{id: a, regex: x, message: '%s'}]\n" % ("m" * 201),
    )
    with pytest.raises(PublishabilityConfigError, match="200"):
        load_publishability_config(root)


def test_invalid_utf8_fails(tmp_path):
    (tmp_path / "publishability.yaml").write_bytes(
        b"version: 1\npatterns: [{id: a, regex: x, message: '\xff\xfe'}]\n"
    )
    with pytest.raises(PublishabilityConfigError, match="UTF-8"):
        load_publishability_config(tmp_path)


def test_invalid_yaml_syntax_fails(tmp_path):
    root = write_config(tmp_path, "version: 1\npatterns: [{id: a, regex: x, message: y}\n")
    with pytest.raises(PublishabilityConfigError, match="valid YAML"):
        load_publishability_config(root)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "version: 1\npatterns: [{id: a, regex: x, message: y}]\n---\nfoo: bar\n",
    ],
)
def test_wrong_document_count_fails(tmp_path, body):
    root = write_config(tmp_path, body)
    with pytest.raises(PublishabilityConfigError, match="one YAML document"):
        load_publishability_config(root)


def test_pattern_hit_is_reported_with_message_not_matched_text():
    patterns = [{"id": "probe", "regex": "SECRETSHAPE-[0-9]+", "message": "probe shape"}]
    findings = scan_text_with_patterns("x.txt", "a SECRETSHAPE-42 b", patterns)
    assert findings == ["x.txt:1: probe shape"]
    assert "SECRETSHAPE-42" not in findings[0]


def test_multiple_lines_report_correct_line_numbers():
    """Multiple hits on different lines in one file, not just a single hit,
    so incremental line tracking across successive matches is exercised."""
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns(
        "x.txt", "clean\nINTERNAL-7\nclean\nINTERNAL-9\nclean\nINTERNAL-11\n", patterns
    )
    assert findings == ["x.txt:2: probe", "x.txt:4: probe", "x.txt:6: probe"]


def test_match_on_first_line_reports_line_one():
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", "INTERNAL-1\nclean\nclean\n", patterns)
    assert findings == ["x.txt:1: probe"]


def test_match_on_last_line_reports_correct_line():
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", "clean\nclean\nINTERNAL-1\n", patterns)
    assert findings == ["x.txt:3: probe"]


def test_match_with_no_trailing_newline_reports_correct_line():
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", "clean\nclean\nINTERNAL-1", patterns)
    assert findings == ["x.txt:3: probe"]


def test_control_characters_in_path_are_escaped():
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("bad\rname.txt", "INTERNAL-1", patterns)
    assert "\r" not in findings[0]
    assert "\\r" in findings[0]


def test_catastrophic_backtracking_yields_a_finding_not_a_hang():
    """A pattern that would hang re.search must produce a finding via the
    worker deadline, and must do so well inside the outer test timeout."""
    patterns = [{"id": "evil", "regex": "(a+)+$", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", "a" * 40 + "!", patterns, deadline=1.0)
    assert len(findings) == 1
    assert "evil" in findings[0]
    assert "deadline" in findings[0]


def test_worker_exception_becomes_a_finding_not_an_empty_result():
    """A crash while matching, not just a timeout, must surface as a finding
    naming the pattern. The old code let a dead worker's empty queue collapse
    into an empty list via `except Exception: return []`, which is
    indistinguishable from a clean scan that found nothing: exactly the
    silent-empty-result the constraint forbids. `text=None` reliably crashes
    the worker inside `finditer`, since re expects a string-like object."""
    patterns = [{"id": "boom", "regex": "x", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", None, patterns)
    assert len(findings) == 1
    assert "boom" in findings[0]
    assert "could not be checked" in findings[0]


def test_large_result_completes_well_inside_the_deadline_with_correct_lines():
    """The old code had two compounding bugs on a large result: joining the
    worker process before draining its queue deadlocks once the pickled
    result is bigger than the pipe buffer (the child cannot exit until the
    write completes, and the parent is blocked in join rather than reading),
    and recomputing text.count("\\n", 0, m.start()) from the START of the
    text on every match is O(matches x text length), slow enough on its own
    to trip the deadline for perfectly ordinary text. Either bug silently
    disables the pattern for every remaining file in the scan. This uses the
    reviewer's reproduction numbers: 40,000 matches in a ~1.4MB file."""
    match_count = 40_000
    pad = "z" * 12
    lines = [f"{pad} hit{i} {pad}" for i in range(match_count)]
    text = "\n".join(lines) + "\n"
    assert 1_300_000 <= len(text) <= 1_500_000  # matches the reviewer's "1.4MB file"

    patterns = [{"id": "many", "regex": "hit[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", text, patterns, deadline=10.0)

    assert len(findings) == match_count
    assert not any("deadline" in f for f in findings)
    assert findings[0] == "x.txt:1: probe"
    assert findings[-1] == f"x.txt:{match_count}: probe"


def test_gate1_passes_on_the_real_repository():
    # `iter_scannable_files` was missing until Task 4's temporary stand-in
    # (scripts/rulecheck.py), which this now exercises for real. Task 8
    # replaces that stand-in with the shared implementation; this assertion
    # keeps guarding the same property either way, so no marker is needed
    # once the dependency exists and the repository is actually clean.
    assert check_publishability(REPO_ROOT) == []


def test_symlink_is_never_silently_skipped(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write_config(tmp_path, "version: 1\npatterns: [{id: a, regex: x, message: y}]\n")
    (tmp_path / "target.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "target.txt")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    assert any("link.txt" in f and "symlink" in f for f in check_publishability(tmp_path))


def test_discovery_failure_is_a_finding_not_an_empty_pass(tmp_path):
    write_config(tmp_path, "version: 1\npatterns: [{id: a, regex: x, message: y}]\n")
    findings = check_publishability(tmp_path)  # not a git repo: ls-files fails
    assert findings and "discovery failed" in findings[0]
