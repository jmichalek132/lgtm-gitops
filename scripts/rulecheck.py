#!/usr/bin/env python3
"""Structure and contract checks for the observability-rules repository.

Query-language validity is NOT checked here: promtool and lokitool do that in
CI stage 3, and they are version-aligned with the deployed backends. This helper
covers what those tools cannot see, namely repository layout, the label and
annotation contract, the canonical environment matcher, CODEOWNERS agreement,
and dashboard identity.

Every check_* function takes the repository root and returns a list of
human-readable findings. An empty list means the check passed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGETS = ("mimir", "loki", "prometheus")
ENVIRONMENTS = ("dev", "staging", "prod")
SEVERITIES = ("info", "warning", "error", "critical")
PLATFORM_TEAM = "platform"

# Targets whose rules promtool can unit-test. Loki has no LogQL unit-test
# command, so a fixture there would never run and must not be committed.
TEST_FIXTURE_TARGETS = ("mimir", "prometheus")

RULE_FILENAME_RE = re.compile(r"^[a-z0-9-]+\.yaml$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
MAX_NAME_BYTES = 253
MAX_SEGMENT_BYTES = 63


def rule_entries(root: Path) -> list[Path]:
    """Every file under rules/, including ones with the wrong extension.

    check_layout rejects non-.yaml entries explicitly. Filtering them out here
    instead would let a `.yml` file sit in the repo being silently ignored by
    every check and never deployed, which is indistinguishable from working.
    """
    rules_dir = root / "rules"
    if not rules_dir.is_dir():
        return []
    return sorted(p for p in rules_dir.rglob("*") if p.is_file() or p.is_symlink())


def rule_files(root: Path) -> list[Path]:
    return [p for p in rule_entries(root) if p.suffix == ".yaml"]


def flattened_key(root: Path, path: Path) -> str:
    """The ConfigMap data key the chart will generate for this file."""
    return str(path.relative_to(root / "rules")).replace("/", "-")


def check_layout(root: Path) -> list[str]:
    findings: list[str] = []
    for path in rule_entries(root):
        rel = path.relative_to(root)
        parts = path.relative_to(root / "rules").parts

        if path.is_symlink():
            findings.append(f"{rel}: symlink; rule files must be regular files")
            continue

        if path.suffix != ".yaml":
            findings.append(f"{rel}: every file under rules/ must end in .yaml")
            continue

        if len(parts) < 3:
            findings.append(
                f"{rel}: expected rules/<team>/<target>/<file>.yaml, got {len(parts)} path segments"
            )
            continue

        team, target = parts[0], parts[1]
        filename = parts[-1]

        if target not in TARGETS:
            findings.append(
                f"{rel}: unknown target '{target}'; must be one of {', '.join(TARGETS)}"
            )
            continue

        for segment in parts[:-1]:
            if not DNS_LABEL_RE.match(segment):
                findings.append(
                    f"{rel}: directory segment '{segment}' must match {DNS_LABEL_RE.pattern}"
                )
            if len(segment.encode()) > MAX_SEGMENT_BYTES:
                findings.append(
                    f"{rel}: directory segment '{segment}' exceeds {MAX_SEGMENT_BYTES} bytes"
                )

        if not RULE_FILENAME_RE.match(filename):
            findings.append(
                f"{rel}: filename must match {RULE_FILENAME_RE.pattern}"
            )

        if target == "prometheus" and team != PLATFORM_TEAM:
            findings.append(
                f"{rel}: the prometheus target is reserved for the '{PLATFORM_TEAM}' team"
            )

        if filename.endswith("-tests.yaml") and target not in TEST_FIXTURE_TARGETS:
            findings.append(
                f"{rel}: test fixtures are only runnable under {', '.join(TEST_FIXTURE_TARGETS)}; "
                f"lokitool has no unit-test command"
            )

        key = flattened_key(root, path)
        if len(key.encode()) > MAX_NAME_BYTES:
            findings.append(
                f"{rel}: generated data key is {len(key.encode())} bytes, over the {MAX_NAME_BYTES} limit"
            )

    return findings


import yaml

SUMMARY_ALIASES = ("summary", "message", "description")
URL_ANNOTATIONS = ("runbook_url", "dashboard_url")


def load_groups(path: Path) -> tuple[list[dict], str | None]:
    """Return (groups, error). Malformed YAML yields ([], message)."""
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [], f"unreadable or unparseable YAML: {exc}"
    if not isinstance(doc, dict):
        return [], "expected a mapping at the document root"
    groups = doc.get("groups") or []
    if not isinstance(groups, list):
        return [], "'groups' must be a list"
    return groups, None


def iter_alerts(root: Path):
    """Yield (path, alert_dict) for every alerting rule, skipping fixtures."""
    for path in rule_files(root):
        if path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        groups, err = load_groups(path)
        if err:
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for rule in group.get("rules") or []:
                if isinstance(rule, dict) and "alert" in rule:
                    yield path, rule


def check_contract(root: Path) -> list[str]:
    findings: list[str] = []
    seen_names: dict[str, Path] = {}

    for path in rule_files(root):
        if path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        _, err = load_groups(path)
        if err:
            findings.append(f"{path.relative_to(root)}: {err}")

    for path, alert in iter_alerts(root):
        rel = path.relative_to(root)
        name = alert.get("alert")
        parts = path.relative_to(root / "rules").parts
        team = parts[0] if parts else "?"

        # A rule whose labels/annotations are a string or list is malformed, but
        # it must produce a finding rather than an AttributeError traceback.
        labels = alert.get("labels")
        labels = labels if isinstance(labels, dict) else {}
        annotations = alert.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}

        severity = labels.get("severity")
        if severity not in SEVERITIES:
            findings.append(
                f"{rel}: alert {name}: severity '{severity}' must be one of {', '.join(SEVERITIES)}"
            )

        owner = labels.get("owner")
        if owner != team:
            findings.append(
                f"{rel}: alert {name}: owner label is '{owner}' but the team folder is '{team}'"
            )

        if not any(annotations.get(a) for a in SUMMARY_ALIASES):
            findings.append(
                f"{rel}: alert {name}: needs one of {', '.join(SUMMARY_ALIASES)}"
            )

        if not any(annotations.get(a) for a in URL_ANNOTATIONS):
            findings.append(
                f"{rel}: alert {name}: needs one of {', '.join(URL_ANNOTATIONS)}"
            )

        # No `!= path` guard: two alerts sharing a name inside ONE file are just
        # as indistinguishable to Alertmanager as two in different files.
        if name in seen_names:
            findings.append(
                f"{rel}: alert name '{name}' is not unique; also defined in "
                f"{seen_names[name].relative_to(root)}. Alerts carry no namespace label, "
                f"so duplicates are indistinguishable to Alertmanager."
            )
        else:
            seen_names[name] = path

    return findings


CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
}


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    failed = False
    for name, fn in CHECKS.items():
        findings = fn(root)
        if findings:
            failed = True
            print(f"[{name}] {len(findings)} finding(s):", file=sys.stderr)
            for f in findings:
                print(f"  {f}", file=sys.stderr)
        else:
            print(f"[{name}] ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
