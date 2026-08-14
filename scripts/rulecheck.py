#!/usr/bin/env python3
"""Structure and contract checks for the observability-rules repository.

Query-language validity is NOT checked here: promtool and lokitool do that in
stage 4 of scripts/check.sh, and they are version-aligned with the deployed
backends. This helper covers what those tools cannot see, namely repository
layout, the label and annotation contract, the canonical environment matcher,
who owns what, CODEOWNERS agreement, and dashboard identity.

Exit codes: 0 clean, 1 findings, 3 clean but the repository still ships the
placeholder owner organisation and so enforces nothing (see check_ownership).

Every check_* function takes the repository root and returns a list of
human-readable findings. An empty list means the check passed.
"""

from __future__ import annotations

import json
import multiprocessing
import re
import subprocess
import sys
from pathlib import Path

import yaml

PUBLISHABILITY_FILE = "publishability.yaml"
PUBLISHABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PUBLISHABILITY_MAX_REGEX = 512
PUBLISHABILITY_MAX_MESSAGE = 200


class PublishabilityConfigError(Exception):
    """The Gate 1 configuration is unusable. Always fatal, never a warning."""


class _NoDuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently
    keeping the last one. A duplicate key is how a second, unreviewed value
    hides behind a reviewed one."""


def _no_duplicate_keys(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise PublishabilityConfigError(f"duplicate key {key!r} in {PUBLISHABILITY_FILE}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_NoDuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


def load_publishability_config(root: Path) -> list[dict]:
    path = root / PUBLISHABILITY_FILE
    if not path.is_file():
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} not found at {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} is not UTF-8") from exc
    try:
        docs = list(yaml.load_all(raw, Loader=_NoDuplicateKeyLoader))
    except yaml.YAMLError as exc:
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} is not valid YAML: {exc}") from exc
    if len(docs) != 1:
        raise PublishabilityConfigError(
            f"{PUBLISHABILITY_FILE} must contain exactly one YAML document, found {len(docs)}"
        )
    doc = docs[0]
    if not isinstance(doc, dict):
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} must be a mapping")
    if set(doc) != {"version", "patterns"}:
        raise PublishabilityConfigError(
            f"{PUBLISHABILITY_FILE} root must have exactly 'version' and 'patterns'"
        )
    version = doc["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} version must be the integer 1")
    patterns = doc["patterns"]
    if not isinstance(patterns, list) or not patterns:
        raise PublishabilityConfigError(f"{PUBLISHABILITY_FILE} patterns must be a non-empty list")

    seen_ids: set[str] = set()
    seen_regexes: set[str] = set()
    for entry in patterns:
        if not isinstance(entry, dict) or set(entry) != {"id", "regex", "message"}:
            raise PublishabilityConfigError(
                "each pattern must have exactly 'id', 'regex' and 'message'"
            )
        pid, regex, message = entry["id"], entry["regex"], entry["message"]
        if not isinstance(pid, str) or not PUBLISHABILITY_ID_RE.match(pid):
            raise PublishabilityConfigError(f"pattern id {pid!r} must match ^[a-z][a-z0-9-]{{0,63}}$")
        if not isinstance(regex, str) or not 1 <= len(regex) <= PUBLISHABILITY_MAX_REGEX:
            raise PublishabilityConfigError(
                f"pattern {pid} regex must be 1 to {PUBLISHABILITY_MAX_REGEX} code points"
            )
        if not isinstance(message, str) or not 1 <= len(message) <= PUBLISHABILITY_MAX_MESSAGE:
            raise PublishabilityConfigError(
                f"pattern {pid} message must be 1 to {PUBLISHABILITY_MAX_MESSAGE} code points"
            )
        if any(c == "\n" or c == "\r" or (ord(c) < 32) for c in message):
            raise PublishabilityConfigError(
                f"pattern {pid} message must contain no line break or control character"
            )
        try:
            compiled = re.compile(regex)
        except re.error as exc:
            raise PublishabilityConfigError(f"pattern {pid} regex does not compile: {exc}") from exc
        if compiled.search(""):
            raise PublishabilityConfigError(f"pattern {pid} regex matches the empty string")
        if pid in seen_ids:
            raise PublishabilityConfigError(f"pattern ids must be unique, {pid!r} repeats")
        if regex in seen_regexes:
            raise PublishabilityConfigError(f"pattern regexes must be unique, {pid!r} repeats one")
        seen_ids.add(pid)
        seen_regexes.add(regex)
    return patterns


PUBLISHABILITY_DEADLINE_SECONDS = 1.0


def escape_path(path: str) -> str:
    """Render a path safe to print. A filename can contain control characters,
    and an unescaped one can rewrite the diagnostic that reports it."""
    return path.encode("unicode_escape").decode("ascii")


def _search_worker(regex: str, text: str, queue) -> None:
    try:
        pattern = re.compile(regex)
        starts = [m.start() for m in pattern.finditer(text)]
        line_nos = []
        line_no = 1
        pos = 0
        for start in starts:
            line_no += text.count("\n", pos, start)
            pos = start
            line_nos.append(line_no)
        queue.put(("ok", line_nos))
    except BaseException as exc:
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def _search_with_deadline(regex: str, text: str, deadline: float):
    """Return ('ok', [1-based line numbers]), ('error', message) if the worker
    raised, or ('timeout', None) if the deadline expired.

    re has no matching timeout, so the only way to bound a catastrophic
    backtracking case is to run it somewhere killable. The result is read
    from the queue BEFORE joining the process: a worker producing a large
    result blocks writing to the queue's pipe until something reads it, and
    joining first would deadlock waiting for an exit that can only happen
    after the write it is blocking.
    """
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_search_worker, args=(regex, text, queue), daemon=True)
    proc.start()
    try:
        status, payload = queue.get(timeout=deadline)
    except Exception:
        proc.kill()
        proc.join()
        return "timeout", None
    proc.join()
    return status, payload


def scan_text_with_patterns(
    path: str, text: str, patterns: list[dict], deadline: float = PUBLISHABILITY_DEADLINE_SECONDS
) -> list[str]:
    """Scan `text` against every pattern, returning `path:line: message` findings.

    A pattern that trips the deadline is marked `pattern["_disabled"] = True`
    IN PLACE on the caller's dict, and this function does not itself check
    that flag: a still-disabled pattern passed in here would simply be
    scanned again. `check_publishability` is the one that filters on
    `_disabled` before calling this per file, since `load_publishability_config`
    returns a fresh list of dicts on every call. A future caller that caches
    the loaded config across scans instead of reloading it per call would
    reintroduce cross-call leakage of the `_disabled` flag; that caller, not
    this function, is the one responsible for deciding when to reset it.
    """
    findings: list[str] = []
    safe_path = escape_path(path)
    for pattern in patterns:
        status, payload = _search_with_deadline(pattern["regex"], text, deadline)
        if status == "timeout":
            findings.append(
                f"{safe_path}: pattern {pattern['id']} exceeded its {deadline}s matching "
                f"deadline and was disabled for the remaining files"
            )
            pattern["_disabled"] = True
            continue
        if status == "error":
            findings.append(
                f"{safe_path}: pattern {pattern['id']} could not be checked: {payload}"
            )
            continue
        for line_no in payload:
            findings.append(f"{safe_path}:{line_no}: {pattern['message']}")
    return findings


def _git_scannable_paths(root: Path) -> list[str] | None:
    """Every path `git ls-files` reports as tracked, or as untracked but not
    ignored. None, never [], on failure: an empty list here must not be
    mistaken by the caller for a repository that legitimately has nothing to
    scan."""
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            capture_output=True, check=True,
        )
        untracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard"],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    raw = tracked.stdout + b"\0" + untracked.stdout
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def iter_scannable_files(root: Path):
    """Yield (path, text) for every file Gate 1 should read.

    TEMPORARY: this is Task 4's minimal stand-in, not the shared
    implementation. The real `iter_scannable_files` ships in Task 8
    (scripts/privatescan.py, also consumed by Gate 2; see
    docs/superpowers/plans/2026-08-14-publishability-gates.md) and this
    function must be deleted outright, not extended, when that lands.

    It exists only because registering "publishability" in CHECKS (this
    task) makes `check_publishability` call this name the moment the check
    actually runs. Leaving the name undefined does not make the check
    inert: it makes `scripts/rulecheck.py` raise an uncaught NameError,
    which is worse than a finding, and returning an empty scan here without
    reading anything would make a check that cannot run indistinguishable
    from one that ran and found nothing clean, exactly the failure mode
    this repository exists to prevent. So this reads real tracked and
    untracked-but-not-ignored files with `git ls-files` and actually scans
    their content. It does not attempt Task 8's fuller edge-case handling
    (redaction, duplicate or escaped-path detection, gitlinks).
    """
    paths = _git_scannable_paths(root)
    if paths is None:
        yield "", "file discovery failed, so nothing was scanned (git ls-files did not run)"
        return
    for rel in sorted(set(paths)):
        full = root / rel
        if full.is_symlink() or not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except OSError as exc:
            yield rel, f"{rel}: unreadable ({exc})"
            continue
        if b"\x00" in data:
            yield rel, f"{rel}: contains a NUL byte, so it is binary and is skipped"
            continue
        try:
            yield rel, data.decode("utf-8")
        except UnicodeDecodeError:
            yield rel, f"{rel}: not valid UTF-8, skipped"


def check_publishability(root: Path) -> list[str]:
    try:
        patterns = load_publishability_config(root)
    except PublishabilityConfigError as exc:
        return [str(exc)]
    findings: list[str] = []
    for path, text in iter_scannable_files(root):
        if isinstance(text, str):
            active = [p for p in patterns if not p.get("_disabled")]
            findings.extend(scan_text_with_patterns(path, text, active))
        else:
            findings.append(text)  # a discovery finding, already formatted
    return findings


TARGETS = ("mimir", "loki", "prometheus")
ENVIRONMENTS = ("dev", "staging", "prod")
SEVERITIES = ("info", "warning", "error", "critical")
PLATFORM_TEAM = "platform"

# The organisation whose teams own paths here is CONFIGURATION, not a constant:
# this repository is published as a reusable example, so a real GitHub handle
# baked into the validator would be wrong for everyone who adopts it. It is read
# from ownership.yaml; '@org' is the shipped placeholder and the fallback used
# when that file cannot be read.
OWNERSHIP_FILE = "ownership.yaml"
PLACEHOLDER_OWNERS_ORG = "@org"
OWNERS_ORG = PLACEHOLDER_OWNERS_ORG

# Exit code for "every check passed, but this is still the shipped, unconfigured
# example, which enforces nothing on GitHub". Distinct from 1 so check.sh can say
# so without failing a build that has no defect in it.
EXIT_UNCONFIGURED = 3


def team_owner(team: str, org: str = OWNERS_ORG) -> str:
    """The GitHub handle that must own `team`'s rules and dashboards."""
    return f"{org}/{team}"


PLATFORM_OWNER = team_owner(PLATFORM_TEAM)

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


def check_fixtures(root: Path) -> list[str]:
    """Every *-tests.yaml fixture must actually assert something.

    `promtool test rules` prints SUCCESS and exits 0 for a fixture whose body is
    `tests: []`, so stage 5 executing a fixture proves only that the file parsed.
    A gutted fixture is precisely the stale fixture the spec promises cannot go
    unnoticed, and it is the easiest way to make a failing test "pass".
    """
    findings: list[str] = []
    for path in rule_files(root):
        if not path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        rel = path.relative_to(root)

        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            findings.append(f"{rel}: unreadable or unparseable YAML: {exc}")
            continue
        if not isinstance(doc, dict):
            findings.append(f"{rel}: expected a mapping at the document root")
            continue

        tests = doc.get("tests")
        if not isinstance(tests, list) or not tests:
            findings.append(
                f"{rel}: 'tests' is missing or empty, so this fixture asserts nothing. "
                f"promtool reports SUCCESS for it, which makes a gutted fixture "
                f"indistinguishable from a passing one."
            )

        rule_files_key = doc.get("rule_files")
        if not isinstance(rule_files_key, list) or not rule_files_key:
            findings.append(
                f"{rel}: 'rule_files' is missing or empty, so this fixture exercises "
                f"no rule file in this repository"
            )

    return findings


# Any appearance of the label, in any matcher form, so non-canonical usage is
# caught rather than skipped. Four details are load-bearing:
#
#   (?<![a-zA-Z0-9_])   word boundary, so my_deployment_environment is not matched
#   quoted label name   Prometheus 3 accepts a quoted label name inside braces
#                       (UTF-8 label support), so up{"deployment_environment"="prod"}
#                       is valid PromQL. Verified with promtool 3.13.2 for all three
#                       delimiters; the CI-pinned promtool 3.1.0 accepts the double
#                       quoted form too. A regex matching only the bare name let this
#                       ship green: a real bypass of the contract, found in review.
#   "..." | '...' | `...`  PromQL accepts single quotes and backticks as string
#                       delimiters, verified with promtool. Matching only double
#                       quotes would let deployment_environment='prod' bypass the
#                       contract silently, the worst possible failure for a check
#                       whose entire purpose is making the environment set derivable.
#   (?:=~|!~|=|!=)      every operator, so non-canonical ones are reported, not skipped
#
# The quoted label-name forms can never satisfy ENV_CANONICAL_RE, so matching them
# here is exactly what turns a silent pass into a reported non-canonical matcher.
ENV_LABEL_ALT = (
    r"""(?:(?<![a-zA-Z0-9_])deployment_environment|"deployment_environment"|"""
    r"""'deployment_environment'|`deployment_environment`)"""
)
ENV_ANY_RE = re.compile(
    ENV_LABEL_ALT + r"""\s*(?:=~|!~|=|!=)\s*"""
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
# these, the contract would be self-modifiable. The test of membership is not
# "does this file feel like infrastructure" but "could editing it change what
# passes": CI's only step is `make check`, so the Makefile IS the pipeline;
# requirements.txt chooses the Python that runs the checks; tools/checksums.txt
# is the only thing standing between a pinned download and an arbitrary binary;
# and tests/ defines what "passing" means for everything in scripts/.
PLATFORM_OWNED_PATHS = (
    "/Chart.yaml",
    "/values.yaml",
    "/values.schema.json",
    "/templates/",
    "/validation.yaml",
    "/scripts/",
    "/tests/",
    "/tools/",
    "/Makefile",
    "/requirements.txt",
    "/.github/",
    f"/{OWNERSHIP_FILE}",
    f"/{PUBLISHABILITY_FILE}",
)


ORG_HANDLE_RE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9-]*$")


def load_ownership(root: Path) -> tuple[str, bool, list[str]]:
    """Read ownership.yaml. Returns (org, configured, errors).

    On any error the org falls back to the shipped placeholder and configured to
    False, so every other check keeps running and reports against the placeholder
    rather than silently trusting a half-read file. The errors are reported by
    check_ownership, which is the one place that decides whether they fail a build.
    """
    path = root / OWNERSHIP_FILE
    if not path.is_file():
        return PLACEHOLDER_OWNERS_ORG, False, [
            f"{OWNERSHIP_FILE} is missing. It declares which GitHub organisation "
            f"owns this repository, and without it the ownership checks have "
            f"nothing to check CODEOWNERS against"
        ]

    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return PLACEHOLDER_OWNERS_ORG, False, [f"{OWNERSHIP_FILE}: unreadable or unparseable YAML: {exc}"]
    if not isinstance(doc, dict):
        return PLACEHOLDER_OWNERS_ORG, False, [f"{OWNERSHIP_FILE}: expected a mapping at the document root"]

    errors: list[str] = []

    org = doc.get("org")
    if not isinstance(org, str) or not ORG_HANDLE_RE.match(org):
        errors.append(
            f"{OWNERSHIP_FILE}: 'org' must be a GitHub organisation handle such as "
            f"'@acme': a leading '@', no team suffix and no slash, since the team "
            f"name is appended per folder. Got {org!r}"
        )
        org = PLACEHOLDER_OWNERS_ORG

    configured = doc.get("configured")
    if not isinstance(configured, bool):
        errors.append(
            f"{OWNERSHIP_FILE}: 'configured' must be true or false, got {configured!r}. "
            f"Whether this repository actually governs anything is not something to "
            f"be inferred from a truthy value"
        )
        configured = False

    return org, configured, errors


def check_ownership(root: Path) -> list[str]:
    """Fail when the repository claims an ownership it does not have.

    '@org/platform' does not exist on GitHub. GitHub silently ignores an owner it
    cannot resolve, so a CODEOWNERS naming it means NOBODY is required to review,
    while every check here treats the same string as authoritative. That gap is
    the bug. It is closed by making the org configuration and then refusing two
    states: a repository that says it is configured while still naming the
    placeholder, and one whose CODEOWNERS still carries placeholder handles.

    Shipping unconfigured is NOT one of those states. A published example that
    openly declares `configured: false` is telling the truth, and it is warned
    about loudly on every run (see ownership_warnings) rather than failed.
    """
    org, configured, findings = load_ownership(root)
    if not configured:
        return findings

    if org == PLACEHOLDER_OWNERS_ORG:
        # The stale-handle check below would compare the placeholder against
        # itself and print nonsense, and this finding already says everything.
        findings.append(
            f"{OWNERSHIP_FILE} says configured: true but 'org' is still the shipped "
            f"placeholder '{PLACEHOLDER_OWNERS_ORG}', which is not a real GitHub "
            f"organisation. GitHub silently ignores owners it cannot resolve, so "
            f"every CODEOWNERS rule here would require review from nobody. Point "
            f"this repository at a real organisation before it governs anything"
        )
        return findings

    stale = sorted(
        {
            owner
            for _pattern, owners in codeowners_entries(root)[1]
            for owner in owners
            if owner.startswith(f"{PLACEHOLDER_OWNERS_ORG}/")
        }
    )
    if stale:
        findings.append(
            f".github/CODEOWNERS still names the placeholder organisation: {stale}. "
            f"{OWNERSHIP_FILE} says this repository is configured as '{org}', so these "
            f"handles resolve to nobody on GitHub and the paths they name are "
            f"unreviewed. Replace every '{PLACEHOLDER_OWNERS_ORG}/' handle with '{org}/'"
        )

    return findings


def ownership_warnings(root: Path) -> list[str]:
    """Warnings for the shipped, unconfigured state. Not build failures.

    Kept separate from check_ownership because the two say different things. A
    finding means the repository is lying about what it enforces; this means it
    is honestly enforcing nothing yet, which is the correct state for an example
    nobody has adopted, and the wrong state for one governing production.
    """
    org, configured, errors = load_ownership(root)
    if errors or configured:
        return []
    if org == PLACEHOLDER_OWNERS_ORG:
        return [
            f"UNCONFIGURED: {OWNERSHIP_FILE} says configured: false, so this repository "
            f"is the shipped example. '{org}/...' is a placeholder organisation that does "
            f"not exist on GitHub, and GitHub silently ignores owners it cannot resolve, "
            f"so NO review is actually required for ANY path here. The ownership checks "
            f"verify that CODEOWNERS is internally consistent; they cannot make GitHub "
            f"enforce it. Set a real organisation in {OWNERSHIP_FILE} before this "
            f"repository governs anything."
        ]
    return [
        f"UNCONFIGURED: {OWNERSHIP_FILE} says configured: false, even though 'org' is "
        f"set to '{org}'. Ownership here is not yet declared configured, so the checks "
        f"that would confirm CODEOWNERS actually matches '{org}' are not enforced as a "
        f"build failure: whatever GitHub does with the handles in CODEOWNERS right now "
        f"is unverified by this run. Set 'configured: true' in {OWNERSHIP_FILE} once "
        f"CODEOWNERS matches '{org}' to make that enforcement real."
    ]


def team_folders(root: Path) -> set[str]:
    teams: set[str] = set()
    for parent in ("rules", "dashboards"):
        base = root / parent
        if base.is_dir():
            teams |= {d.name for d in base.iterdir() if d.is_dir()}
    return teams


def codeowners_entries(root: Path) -> tuple[set[str], list[tuple[str, list[str]]]]:
    """Return (team names claimed under rules/ or dashboards/, ordered entries).

    Entries are (pattern, owners) in FILE ORDER, duplicates included. Order is the
    whole point: GitHub resolves ownership by last matching pattern, so collapsing
    the file into a pattern -> owners mapping throws away the only information that
    decides who actually owns a path.
    """
    path = root / ".github" / "CODEOWNERS"
    teams: set[str] = set()
    entries: list[tuple[str, list[str]]] = []
    if not path.is_file():
        return teams, entries
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        pattern = parts[0]
        owners = parts[1:]  # All tokens after the pattern are owner handles
        entries.append((pattern, owners))
        for parent in ("/rules/", "/dashboards/"):
            if pattern.startswith(parent):
                remainder = pattern[len(parent):].strip("/")
                if remainder:
                    teams.add(remainder.split("/")[0])
    return teams, entries


# CODEOWNERS is evaluated as a restricted, fully anchored dialect. Anything
# outside it is REJECTED rather than guessed at, because a pattern this checker
# mis-evaluates is worse than one it refuses: it would report ownership the
# repository does not actually have.
#
# Supported:
#   *              the bare default-owner pattern, matching every path
#   /a/b/          anchored directory, matching everything beneath it
#   /a/b.txt       anchored path, matching exactly that path
#   *              inside an anchored pattern, matching within one segment
#   **             inside an anchored pattern, crossing / freely
#
# Rejected: unanchored patterns (gitignore lets them match at any depth, and
# that depth rule is subtle enough that implementing it from memory would be a
# guess), character classes, ?, ! negation and backslash escapes.
CODEOWNERS_UNSUPPORTED_CHARS = set("[]?!\\")
CODEOWNERS_PROBE = "__codeowners_probe__"


def codeowners_pattern_regex(pattern: str) -> re.Pattern[str] | None:
    """Compile a CODEOWNERS pattern to a whole-path regex, or None if unsupported."""
    if pattern == "*":
        return re.compile(r".*")
    if not pattern.startswith("/"):
        return None
    if CODEOWNERS_UNSUPPORTED_CHARS & set(pattern):
        return None

    body = pattern[1:]
    if not body:
        return None

    out: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    translated = "".join(out)

    # A trailing slash marks a directory: it owns everything beneath, not itself.
    if body.endswith("/"):
        return re.compile(rf"^{translated}.+$")
    last_seg = body.rsplit("/", 1)[-1]
    if last_seg in ("*", "**"):
        return re.compile(rf"^{translated}$")
    # No trailing slash: matches the path AND everything beneath it (a pattern
    # naming a directory claims its contents). hmarr/codeowners match.go:172.
    return re.compile(rf"^{translated}(?:/.*)?$")


def codeowners_pattern_witness(pattern: str) -> str | None:
    """A concrete path the pattern matches, used to probe patterns for paths that
    do not exist yet. A CODEOWNERS line takes effect the moment someone adds the
    file it names, so the line itself is evidence and must be evaluated now."""
    if pattern == "*" or not pattern.startswith("/"):
        return None
    witness = pattern[1:].replace("**", CODEOWNERS_PROBE).replace("*", CODEOWNERS_PROBE)
    if witness.endswith("/"):
        witness += CODEOWNERS_PROBE
    return witness or None


# Directories whose contents are build or tool droppings rather than repository
# content. Walking them would probe paths GitHub never sees.
UNTRACKED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}


def _files_under(root: Path, rel_dir: str) -> list[str]:
    base = root / rel_dir
    if not base.is_dir():
        return []
    found: list[str] = []
    for path in base.rglob("*"):
        if any(part in UNTRACKED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() or path.is_symlink():
            found.append(str(path.relative_to(root)))
    return sorted(found)


def governed_probe_paths(root: Path, entry: str, patterns: list[str]) -> list[str]:
    """Concrete repository paths that must resolve to the platform team for `entry`.

    Three sources, because each covers a hole the others leave:
      - every file that exists under the entry today
      - a synthetic path, so an empty or future directory is still evaluated
      - the witness path of every CODEOWNERS pattern that lands inside the entry,
        so a line naming a not-yet-created file cannot lie in wait
    """
    if entry.endswith("/"):
        rel_dir = entry.strip("/")
        probes = set(_files_under(root, rel_dir))
        probes.add(f"{rel_dir}/{CODEOWNERS_PROBE}")
        prefix = f"{rel_dir}/"
    else:
        rel_path = entry.lstrip("/")
        probes = {rel_path}
        prefix = None

    for pattern in patterns:
        witness = codeowners_pattern_witness(pattern)
        if witness and prefix and witness.startswith(prefix):
            probes.add(witness)

    return sorted(probes)


def resolve_owners(
    compiled: list[tuple[str, list[str], re.Pattern[str]]], probe: str
) -> tuple[str, list[str]] | None:
    """Who GitHub says owns `probe`, or None if no pattern matches it.

    GitHub resolves ownership by the LAST matching pattern, so a more specific
    line later in the file overrides an earlier directory entry.
    """
    winner = None
    for pattern, owners, regex in compiled:
        if regex.match(probe):
            winner = (pattern, owners)
    return winner


def describe_probe(probe: str) -> str:
    """A human-readable name for a probe path, which may be synthetic."""
    if probe.endswith(CODEOWNERS_PROBE):
        return f"any file under {probe[: -len(CODEOWNERS_PROBE)]}"
    return probe


def check_codeowners(root: Path) -> list[str]:
    findings: list[str] = []
    if not (root / ".github" / "CODEOWNERS").is_file():
        return [".github/CODEOWNERS is missing"]

    # The org is configuration; a repository pointed at a real organisation must
    # be checked against that one, not against the shipped placeholder.
    org, _configured, _errors = load_ownership(root)
    owned_teams, entries = codeowners_entries(root)
    actual_teams = team_folders(root)

    for team in sorted(actual_teams - owned_teams):
        findings.append(
            f"team '{team}' has folders but no CODEOWNERS entry; "
            f"add '/rules/{team}/ {team_owner(team, org)}' and "
            f"'/dashboards/{team}/ {team_owner(team, org)}'"
        )

    for team in sorted(owned_teams - actual_teams):
        findings.append(
            f"CODEOWNERS claims team '{team}' but no rules/ or dashboards/ folder exists"
        )

    compiled: list[tuple[str, list[str], re.Pattern[str]]] = []
    for pattern, owners in entries:
        regex = codeowners_pattern_regex(pattern)
        if regex is None:
            findings.append(
                f"CODEOWNERS pattern '{pattern}' cannot be evaluated by this check. "
                f"Anchor it with a leading '/' and use only literal segments, '*' and '**'; "
                f"character classes, '?', '!' and backslash escapes are not supported. "
                f"A pattern the ownership check cannot evaluate must not be assumed safe."
            )
            continue
        compiled.append((pattern, owners, regex))

    platform_owner = team_owner(PLATFORM_TEAM, org)
    patterns = [pattern for pattern, _ in entries]
    for entry in PLATFORM_OWNED_PATHS:
        for probe in governed_probe_paths(root, entry, patterns):
            winner = resolve_owners(compiled, probe)
            if winner is None:
                findings.append(
                    f"CODEOWNERS must assign the platform team to '{entry}': no pattern "
                    f"matches '{probe}', so nobody owns it and anyone can approve a change "
                    f"to the checks that govern them"
                )
                continue
            pattern, owners = winner
            if platform_owner not in owners:
                findings.append(
                    f"CODEOWNERS gives '{probe}' (governed by '{entry}') to "
                    f"{owners or 'nobody'} through pattern '{pattern}', which is the last "
                    f"pattern matching it and therefore the one GitHub applies. "
                    f"{platform_owner} must own it, otherwise a team can approve changes "
                    f"to the checks that govern it"
                )
            elif set(owners) != {platform_owner}:
                extra = sorted(set(owners) - {platform_owner})
                findings.append(
                    f"CODEOWNERS gives '{probe}' (governed by '{entry}') to "
                    f"{platform_owner} AND {extra} through pattern '{pattern}'. On "
                    f"GitHub any co-owner can approve alone, so {extra} can still "
                    f"approve changes to the checks that govern it; "
                    f"{platform_owner} must be the sole owner"
                )

    # A team's rules and its dashboards are ONE ownership boundary, and it is the
    # team's own. Reconciling the SET of team names against the SET of folders,
    # which is all this used to do, never asked the only question that matters:
    # does rules/<team>/ actually resolve to <team>? Two holes followed from that.
    # Another team could be named as the owner of your alerting rules, and a team
    # could own its rules while its dashboards quietly fell through to the default
    # owner. Both are checked here, per path, last-match-wins, exactly as GitHub
    # resolves them.
    #
    # Only teams that appear in CODEOWNERS are checked: a team with folders and no
    # entry at all is already reported above, and repeating it per path would bury
    # the one finding that says what to add.
    for team in sorted(owned_teams):
        expected = team_owner(team, org)
        for parent in ("rules", "dashboards"):
            entry = f"/{parent}/{team}/"
            for probe in governed_probe_paths(root, entry, patterns):
                winner = resolve_owners(compiled, probe)
                if winner is None:
                    findings.append(
                        f"CODEOWNERS leaves {describe_probe(probe)} unowned: no pattern "
                        f"matches it, so anyone can approve a change to team '{team}'s "
                        f"{parent}. {expected} must own it"
                    )
                    continue
                pattern, owners = winner
                if expected not in owners:
                    findings.append(
                        f"CODEOWNERS gives {describe_probe(probe)} to "
                        f"{owners or 'nobody'} through pattern '{pattern}', which is the "
                        f"last pattern matching it and therefore the one GitHub applies. "
                        f"A team's rules and dashboards are one ownership boundary and it "
                        f"is the team's own, so this must resolve to {expected}"
                    )
                elif set(owners) != {expected}:
                    extra = sorted(set(owners) - {expected})
                    findings.append(
                        f"CODEOWNERS gives {describe_probe(probe)} to {expected} AND "
                        f"{extra} through pattern '{pattern}'. On GitHub any co-owner can "
                        f"approve alone, so {extra} can still approve team '{team}'s "
                        f"{parent}; {expected} must be the sole owner"
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
    "fixtures": check_fixtures,
    "envmatcher": check_env_matchers,
    "ownership": check_ownership,
    "codeowners": check_codeowners,
    "dashboards": check_dashboards,
    "publishability": check_publishability,
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

    warnings = ownership_warnings(root)
    for w in warnings:
        print(f"[ownership] WARNING: {w}", file=sys.stderr)

    if failed:
        return 1
    return EXIT_UNCONFIGURED if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
