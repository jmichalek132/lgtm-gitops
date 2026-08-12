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

import json
import re
import subprocess
import sys
from pathlib import Path

TARGETS = ("mimir", "loki", "prometheus")
ENVIRONMENTS = ("dev", "staging", "prod")
SEVERITIES = ("info", "warning", "error", "critical")
PLATFORM_TEAM = "platform"
PLATFORM_OWNER = f"@org/{PLATFORM_TEAM}"

# Targets whose rules promtool can unit-test. Loki has no LogQL unit-test
# command, so a fixture there would never run and must not be committed.
TEST_FIXTURE_TARGETS = ("mimir", "prometheus")

RULE_FILENAME_RE = re.compile(r"^[a-z0-9-]+\.yaml$")
DASHBOARD_FILENAME_RE = re.compile(r"^[a-z0-9-]+\.json$")
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


# Any appearance of the label, in any matcher form, so non-canonical usage is
# caught rather than skipped. Three details are load-bearing:
#
#   (?<![a-zA-Z0-9_])   word boundary, so my_deployment_environment is not matched
#   "..." | '...' | `...`  PromQL accepts single quotes and backticks as string
#                       delimiters, verified with promtool. Matching only double
#                       quotes would let deployment_environment='prod' bypass the
#                       contract silently, the worst possible failure for a check
#                       whose entire purpose is making the environment set derivable.
#   (?:=~|!~|=|!=)      every operator, so non-canonical ones are reported, not skipped
ENV_ANY_RE = re.compile(
    r"""(?<![a-zA-Z0-9_])deployment_environment\s*(?:=~|!~|=|!=)\s*"""
    r"""(?:"[^"]*"|'[^']*'|`[^`]*`)"""
)
# The one permitted form. No \s*, double quotes only: whitespace and alternative
# delimiters fail this by construction and are reported as non-canonical.
ENV_CANONICAL_RE = re.compile(r'deployment_environment=~"([a-z|]+)"')


def iter_expressions(root: Path):
    """Yield (path, rule_name, expr) for every rule that has an expression.

    Only the `expr` field is examined, so a matcher inside an annotation,
    a summary string or a YAML comment cannot influence the result.
    """
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
                if not isinstance(rule, dict):
                    continue
                expr = rule.get("expr")
                if isinstance(expr, str):
                    yield path, rule.get("alert") or rule.get("record"), expr


def check_env_matchers(root: Path) -> list[str]:
    findings: list[str] = []
    for path, name, expr in iter_expressions(root):
        rel = path.relative_to(root)
        # finditer, not findall: the pattern has no capture group, so we want the
        # whole matched text of each occurrence in order to compare them literally.
        raw = [m.group(0) for m in ENV_ANY_RE.finditer(expr)]
        if not raw:
            continue

        if len(set(raw)) > 1:
            findings.append(
                f"{rel}: {name}: deployment_environment matchers must be byte-identical "
                f"within one expression; found {sorted(set(raw))}"
            )

        for occurrence in sorted(set(raw)):
            canonical = ENV_CANONICAL_RE.fullmatch(occurrence)
            if not canonical:
                findings.append(
                    f"{rel}: {name}: '{occurrence}' is not the canonical form. "
                    f'Use deployment_environment=~"staging|prod": =~ only, double quotes, '
                    f"no whitespace, no negation."
                )
                continue

            values = canonical.group(1).split("|")
            unknown = [v for v in values if v not in ENVIRONMENTS]
            if unknown:
                findings.append(
                    f"{rel}: {name}: unknown environment(s) {unknown}; "
                    f"known values are {', '.join(ENVIRONMENTS)}"
                )
                continue

            if len(set(values)) != len(values):
                findings.append(f"{rel}: {name}: duplicate environment values in '{occurrence}'")
                continue

            expected = [e for e in ENVIRONMENTS if e in values]
            if values != expected:
                findings.append(
                    f"{rel}: {name}: environments must be in list order "
                    f"({', '.join(ENVIRONMENTS)}); expected \"{'|'.join(expected)}\""
                )

    return findings


# Paths that govern the checks themselves. If a team could approve changes to
# these, the contract would be self-modifiable.
PLATFORM_OWNED_PATHS = (
    "/Chart.yaml",
    "/values.yaml",
    "/values.schema.json",
    "/templates/",
    "/validation.yaml",
    "/scripts/",
    "/.github/",
)


def team_folders(root: Path) -> set[str]:
    teams: set[str] = set()
    for parent in ("rules", "dashboards"):
        base = root / parent
        if base.is_dir():
            teams |= {d.name for d in base.iterdir() if d.is_dir()}
    return teams


def codeowners_entries(root: Path) -> tuple[set[str], dict[str, list[str]]]:
    """Return (team names claimed under rules/ or dashboards/, pattern -> owners list).

    For each pattern, the owners list contains all handles on that line.
    If a pattern appears multiple times, last occurrence wins (GitHub semantics).
    """
    path = root / ".github" / "CODEOWNERS"
    teams: set[str] = set()
    pattern_to_owners: dict[str, list[str]] = {}
    if not path.is_file():
        return teams, pattern_to_owners
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        pattern = parts[0]
        owners = parts[1:]  # All tokens after the pattern are owner handles
        pattern_to_owners[pattern] = owners  # Last occurrence wins
        for parent in ("/rules/", "/dashboards/"):
            if pattern.startswith(parent):
                remainder = pattern[len(parent):].strip("/")
                if remainder:
                    teams.add(remainder.split("/")[0])
    return teams, pattern_to_owners


def check_codeowners(root: Path) -> list[str]:
    findings: list[str] = []
    if not (root / ".github" / "CODEOWNERS").is_file():
        return [".github/CODEOWNERS is missing"]

    owned_teams, pattern_to_owners = codeowners_entries(root)
    actual_teams = team_folders(root)

    for team in sorted(actual_teams - owned_teams):
        findings.append(
            f"team '{team}' has folders but no CODEOWNERS entry; "
            f"add '/rules/{team}/ @org/{team}'"
        )

    for team in sorted(owned_teams - actual_teams):
        findings.append(
            f"CODEOWNERS claims team '{team}' but no rules/ or dashboards/ folder exists"
        )

    for required in PLATFORM_OWNED_PATHS:
        owners = pattern_to_owners.get(required, [])
        if not owners:
            findings.append(
                f"CODEOWNERS must assign the platform team to '{required}', "
                f"otherwise a team can approve changes to the checks that govern it"
            )
        elif PLATFORM_OWNER not in owners:
            findings.append(
                f"CODEOWNERS assigns '{required}' to {owners} but the platform team must own it; "
                f"otherwise a team can approve changes to the checks that govern it"
            )

    return findings


def dashboard_files(root: Path) -> list[Path]:
    base = root / "dashboards"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.json") if p.is_file())


def _uids_at_ref(root: Path, ref: str) -> dict[str, str] | None:
    """Map relative path -> uid as of the given git ref. Missing files are skipped.

    Returns None if the base ref could not be resolved; {} if resolved but no dashboards.
    """
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref, "dashboards/"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    except subprocess.CalledProcessError:
        return None

    uids: dict[str, str] = {}
    for rel in listing:
        if not rel.endswith(".json"):
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", str(root), "show", f"{ref}:{rel}"],
                capture_output=True, text=True, check=True,
            ).stdout
            doc = json.loads(blob)
            if isinstance(doc, dict):
                uid = doc.get("uid")
                if uid:
                    uids[rel] = uid
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
    return uids


def check_dashboards(root: Path, base_ref: str | None = None) -> list[str]:
    findings: list[str] = []
    seen_uids: dict[str, Path] = {}
    current: dict[str, str] = {}

    for path in dashboard_files(root):
        rel = path.relative_to(root)

        if not DASHBOARD_FILENAME_RE.match(path.name):
            findings.append(f"{rel}: filename must match {DASHBOARD_FILENAME_RE.pattern}")

        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            findings.append(f"{rel}: invalid JSON: {exc}")
            continue

        if not isinstance(doc, dict):
            findings.append(f"{rel}: dashboard JSON must be an object, got {type(doc).__name__}")
            continue

        uid = doc.get("uid")
        if not uid:
            findings.append(f"{rel}: dashboard must declare a 'uid'")
            continue

        current[str(rel)] = uid

        if uid in seen_uids:
            findings.append(
                f"{rel}: uid '{uid}' is not unique; also used by "
                f"{seen_uids[uid].relative_to(root)}"
            )
        else:
            seen_uids[uid] = path

    if base_ref:
        base_uids = _uids_at_ref(root, base_ref)
        if base_uids is None:
            findings.append(
                f"base ref '{base_ref}' could not be resolved; uid-change detection did not run. "
                f"CI needs full history (git clone without --depth) to detect dashboard uid changes."
            )
        else:
            for rel, old_uid in base_uids.items():
                new_uid = current.get(rel)
                if new_uid and new_uid != old_uid:
                    findings.append(
                        f"{rel}: uid changed from '{old_uid}' to '{new_uid}'. This orphans the "
                        f"live dashboard and breaks every link and annotation pointing at it. "
                        f"If deliberate, say so explicitly in the pull request."
                    )

    return findings


CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
    "envmatcher": check_env_matchers,
    "codeowners": check_codeowners,
    "dashboards": check_dashboards,
}


def main(argv: list[str]) -> int:
    import os

    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    base_ref = os.environ.get("BASE_REF") or None

    failed = False
    for name, fn in CHECKS.items():
        findings = fn(root, base_ref) if name == "dashboards" else fn(root)
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
