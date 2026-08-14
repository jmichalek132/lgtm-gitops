import re
import textwrap
from pathlib import Path

import pytest

from scripts.rulecheck import PublishabilityConfigError, load_publishability_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / "publishability.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_shipped_config_loads():
    patterns = load_publishability_config(REPO_ROOT)
    assert [p["id"] for p in patterns] == ["macos-home-path", "linux-home-path"]


def test_shipped_patterns_do_not_match_their_own_config_line():
    """The whole no-self-exemption design rests on this property."""
    text = (REPO_ROOT / "publishability.yaml").read_text(encoding="utf-8")
    for pattern in load_publishability_config(REPO_ROOT):
        rx = re.compile(pattern["regex"])
        for line in text.splitlines():
            if pattern["regex"] in line:
                assert not rx.search(line), (
                    f"pattern {pattern['id']} matches its own configuration line"
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
