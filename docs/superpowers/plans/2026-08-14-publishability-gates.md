# Publishability Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** build the two publishability gates so that a private term or personal path cannot enter this repository without a check failing.

**Architecture:** Gate 1 is a public pattern check registered in the existing `CHECKS` dict in `scripts/rulecheck.py`, driven by a committed `publishability.yaml` holding only readable, non-secret regexes. Gate 2 is a standalone scanner, `scripts/privatescan.py`, whose denylist of plaintext terms lives outside the repository and is never committed; `scripts/check.sh` invokes it directly and fails non-zero when it cannot run. CI must never hold the private denylist, so it selects an explicit named skip.

**Tech Stack:** Python 3.12, PyYAML, pytest, bash 3.2 compatible shell, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-14-publishable-example-design.md` sections 3, 7 and 8.

## Global Constraints

- No em dashes anywhere: in code, comments, docs, commit messages or test names. Use a comma, semicolon, colon, parentheses, or rewrite.
- **Never write a private term into any tracked file, test fixture, commit message or error string.** Tests use synthetic terms only. The spec's placeholders are `TERM-A` and `TERM-B`, and no document may state what category of word either is.
- Gate 2 output and any freeze artifact are confidential: they stay outside the repository and are never attached to public CI, issues or pull requests.
- macOS ships bash 3.2, which has no `mapfile`. Shell code must work on bash 3.2.
- A check that could not run must never be reportable as a check that found nothing.
- Every discovery, configuration and decoding error becomes a finding or a non-zero exit, never an empty result and never an uncaught traceback.
- The repository has no binary tracked files. Binary content is rejected, not skipped.
- Run the suite as: `cd <repo> && PATH="$PWD/.venv/bin:$PATH" make check`

## File Structure

| File | Responsibility |
| --- | --- |
| `publishability.yaml` (create) | Gate 1 config: public, readable, non-secret patterns only |
| `scripts/rulecheck.py` (modify) | Add `check_publishability`, register it, add `/publishability.yaml` to `PLATFORM_OWNED_PATHS` |
| `scripts/privatescan.py` (create) | Gate 2: denylist loading, normalisation, masks, decoder views, discovery, reporting |
| `scripts/check.sh` (modify) | Invoke Gate 2, handle the single named skip, fix the caveat sentence |
| `.github/workflows/ci.yaml` (modify) | Relocate tool downloads out of the working tree; assert no untracked paths remain |
| `.github/CODEOWNERS` (modify) | Add `/publishability.yaml` under the platform owner |
| `Makefile` (modify) | No new target; Gate 2 runs inside `check` |
| `tests/test_publishability.py` (create) | Gate 1 tests |
| `tests/test_privatescan.py` (create) | Gate 2 tests |

---

### Task 1: Relocate CI tool downloads out of the working tree

Gate 1's fail-closed discovery scans untracked, non-ignored files. The current workflow downloads three archives into the repository root and extracts them there before `make check`, so enabling discovery without this change fails CI on its own build artifacts. Verified: all six resulting paths report `NOT ignored` under `git check-ignore`.

Gitignoring them is explicitly **not** acceptable. An ignored installation artifact becomes invisible to the scanner by construction, which is the failure mode this repository exists to prevent.

**Files:**
- Modify: `.github/workflows/ci.yaml:47-62`

**Interfaces:**
- Produces: a CI job that leaves zero untracked, non-ignored paths before `make check` runs.

- [ ] **Step 1: Read the current step**

`tools/checksums.txt` lists bare relative filenames:

```
9a9d1e115d1745826b13aec3f1409780b9fcf1d4206746cb4faee46ca5add70c  prometheus.tar.gz
3ba2179193cbdf830451aae071c4377ca8995fc3858c45762929c81aacacc6fc  promruval.tar.gz
7a93647eabe8ab9a7a91db909307c755890230ff88a8b431571a93e8e1b1265b  lokitool.zip
```

Because the names are relative, `sha256sum --check` resolves them against the current directory. Running it from `$RUNNER_TEMP` while reading the checksum file by absolute path therefore works unchanged.

- [ ] **Step 2: Replace the install step**

Replace lines 47-62 of `.github/workflows/ci.yaml` with:

```yaml
      - name: Install pinned tools
        run: |
          set -euo pipefail
          # Downloads and extraction happen OUTSIDE $GITHUB_WORKSPACE. The
          # publishability scan treats every untracked, non-ignored path in the
          # working tree as a candidate and rejects binary content, so build
          # artifacts must not land there. Adding them to .gitignore would be
          # worse: an ignored artifact is invisible to the scanner by
          # construction.
          workdir="$RUNNER_TEMP/tools"
          mkdir -p "$workdir"
          cd "$workdir"
          curl -sSfL --retry 3 -o prometheus.tar.gz \
            "https://github.com/prometheus/prometheus/releases/download/v${PROMTOOL_VERSION}/prometheus-${PROMTOOL_VERSION}.linux-amd64.tar.gz"
          curl -sSfL --retry 3 -o promruval.tar.gz \
            "https://github.com/fusakla/promruval/releases/download/v${PROMRUVAL_VERSION}/promruval_${PROMRUVAL_VERSION}_linux_amd64.tar.gz"
          curl -sSfL --retry 3 -o lokitool.zip \
            "https://github.com/grafana/loki/releases/download/v${LOKITOOL_VERSION}/lokitool-linux-amd64.zip"
          # Checksums are relative names, so they resolve against $workdir while
          # the manifest itself is read from the checkout by absolute path.
          sha256sum --check --strict "$GITHUB_WORKSPACE/tools/checksums.txt"
          tar -xzf prometheus.tar.gz
          sudo install "prometheus-${PROMTOOL_VERSION}.linux-amd64/promtool" /usr/local/bin/promtool
          tar -xzf promruval.tar.gz
          sudo install promruval /usr/local/bin/promruval
          unzip -q lokitool.zip
          sudo install lokitool-linux-amd64 /usr/local/bin/lokitool

      - name: Assert tool installation left the working tree clean
        run: |
          set -euo pipefail
          # This assertion is the point of the previous step. If it ever fails,
          # a tool install has started writing into the checkout again and the
          # publishability scan would fail for a reason unrelated to content.
          leftover="$(git status --porcelain --untracked-files=normal)"
          if [ -n "$leftover" ]; then
            printf 'tool installation left paths in the working tree:\n%s\n' "$leftover" >&2
            exit 1
          fi
```

- [ ] **Step 3: Verify the assertion catches the old behaviour**

Locally, simulate what the old step did and confirm the assertion would have failed:

```bash
cd /path/to/repo
touch prometheus.tar.gz
git status --porcelain --untracked-files=normal   # expect: "?? prometheus.tar.gz"
rm prometheus.tar.gz
git status --porcelain --untracked-files=normal   # expect: empty
```

Expected: the first command prints one line, the second prints nothing. That is exactly the signal the CI step keys on.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: install pinned tools outside the working tree

Gate 1 discovery scans untracked non-ignored paths and rejects binary
content. Downloads and extraction move under RUNNER_TEMP so build
artifacts never enter the checkout, with an assertion that the tree is
clean before make check. Gitignoring them was rejected: an ignored
artifact is invisible to the scanner by construction."
```

---

### Task 2: Gate 1 configuration and strict schema loader

**Files:**
- Create: `publishability.yaml`
- Modify: `scripts/rulecheck.py`
- Test: `tests/test_publishability.py`

**Interfaces:**
- Produces: `load_publishability_config(root: Path) -> list[dict]` raising `PublishabilityConfigError(str)` on any schema violation. Each returned dict has keys `id`, `regex`, `message`.

- [ ] **Step 1: Write the config file**

Create `publishability.yaml`:

```yaml
# Gate 1: public, readable, NON-SECRET patterns only.
#
# Never put a private term here, hashed or otherwise. A committed digest of a
# secret word is an offline confirmation oracle, not a redaction: an unsalted
# SHA-256 of a dictionary word falls to a ten-million-entry wordlist in about
# three seconds on one CPU core. Private terms belong in the out-of-repository
# denylist that scripts/privatescan.py reads. See section 3 of
# docs/superpowers/specs/2026-08-14-publishable-example-design.md.
#
# Patterns are written so they do not match their own configuration line. That
# is why the leading slash is bracketed: a naive pattern with a bare leading
# slash before "Users" or "home" would match the very line that defines it,
# which is what made an earlier design exempt this file from its own scan.
# There is no exemption now.
version: 1
patterns:
  - id: macos-home-path
    regex: '[/]Users[/][^/ \t\r\n]+[/]'
    message: "personal absolute path"
  - id: linux-home-path
    regex: '[/]home[/][^/ \t\r\n]+[/]'
    message: "personal absolute path"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_publishability.py`:

```python
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
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_publishability.py -v`
Expected: FAIL with `ImportError: cannot import name 'PublishabilityConfigError'`.

- [ ] **Step 4: Implement the loader**

Add to `scripts/rulecheck.py`, near the other module-level constants and before `CHECKS`:

```python
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
```

Move the mid-file `import yaml` at `scripts/rulecheck.py:125` up to the top-level imports as part of this task; the loader needs it at module scope.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_publishability.py -v`
Expected: PASS, all cases.

- [ ] **Step 6: Commit**

```bash
git add publishability.yaml scripts/rulecheck.py tests/test_publishability.py
git commit -m "feat: Gate 1 configuration and strict schema loader

Public patterns only, never a private term or a digest of one. Patterns
are written to not match their own configuration line, which is what
removes the need for the self-exemption an earlier design had and that
could have hidden a personal path in a comment."
```

---

### Task 3: Gate 1 scan with a killable execution deadline

Python `re` has no matching timeout, so an advisory elapsed-time check after `re.search` returns cannot bound a catastrophic backtracking case: the call never returns. Matching therefore runs in a killable worker process.

**Files:**
- Modify: `scripts/rulecheck.py`
- Test: `tests/test_publishability.py`

**Interfaces:**
- Consumes: `load_publishability_config(root) -> list[dict]`
- Produces: `check_publishability(root: Path) -> list[str]`, findings formatted `path:line: message`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_publishability.py`:

```python
from scripts.rulecheck import check_publishability, scan_text_with_patterns


def test_pattern_hit_is_reported_with_message_not_matched_text():
    patterns = [{"id": "probe", "regex": "SECRETSHAPE-[0-9]+", "message": "probe shape"}]
    findings = scan_text_with_patterns("x.txt", "a SECRETSHAPE-42 b", patterns)
    assert findings == ["x.txt:1: probe shape"]
    assert "SECRETSHAPE-42" not in findings[0]


def test_multiple_lines_report_correct_line_numbers():
    patterns = [{"id": "probe", "regex": "INTERNAL-[0-9]+", "message": "probe"}]
    findings = scan_text_with_patterns("x.txt", "clean\nINTERNAL-7\nclean\n", patterns)
    assert findings == ["x.txt:2: probe"]


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


def test_gate1_passes_on_the_real_repository():
    assert check_publishability(REPO_ROOT) == []
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_publishability.py -k "pattern_hit or backtracking" -v`
Expected: FAIL with `ImportError: cannot import name 'scan_text_with_patterns'`.

- [ ] **Step 3: Implement the scan**

Add to `scripts/rulecheck.py`:

```python
import multiprocessing
import unicodedata

PUBLISHABILITY_DEADLINE_SECONDS = 1.0


def escape_path(path: str) -> str:
    """Render a path safe to print. A filename can contain control characters,
    and an unescaped one can rewrite the diagnostic that reports it."""
    return path.encode("unicode_escape").decode("ascii")


def _search_worker(regex: str, text: str, queue) -> None:
    matched = [m for m in re.compile(regex).finditer(text)]
    queue.put([text.count("\n", 0, m.start()) + 1 for m in matched])


def _search_with_deadline(regex: str, text: str, deadline: float):
    """Return a list of 1-based line numbers, or None if the deadline expired.

    re has no matching timeout, so the only way to bound a catastrophic
    backtracking case is to run it somewhere killable.
    """
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_search_worker, args=(regex, text, queue), daemon=True)
    proc.start()
    proc.join(deadline)
    if proc.is_alive():
        proc.kill()
        proc.join()
        return None
    try:
        return queue.get_nowait()
    except Exception:
        return []


def scan_text_with_patterns(
    path: str, text: str, patterns: list[dict], deadline: float = PUBLISHABILITY_DEADLINE_SECONDS
) -> list[str]:
    findings: list[str] = []
    safe_path = escape_path(path)
    for pattern in patterns:
        lines = _search_with_deadline(pattern["regex"], text, deadline)
        if lines is None:
            findings.append(
                f"{safe_path}: pattern {pattern['id']} exceeded its {deadline}s matching "
                f"deadline and was disabled for the remaining files"
            )
            pattern["_disabled"] = True
            continue
        for line_no in lines:
            findings.append(f"{safe_path}:{line_no}: {pattern['message']}")
    return findings


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
```

`iter_scannable_files` is delivered by Task 8 and shared with Gate 2. Until then, this task's tests exercise `scan_text_with_patterns` directly, and `test_gate1_passes_on_the_real_repository` is expected to fail until Task 8 lands. Mark it:

```python
@pytest.mark.xfail(reason="iter_scannable_files arrives in Task 8", strict=True)
def test_gate1_passes_on_the_real_repository():
    assert check_publishability(REPO_ROOT) == []
```

Task 6 removes the `xfail` marker.

- [ ] **Step 4: Run and confirm pass**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_publishability.py -v`
Expected: PASS, with one `xfail`.

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_publishability.py
git commit -m "feat: Gate 1 pattern scan with a killable matching deadline

re has no matching timeout, so an elapsed-time check after search returns
cannot bound catastrophic backtracking: the call never returns. Matching
runs in a killable worker with a 1.0s deadline, and a pattern that trips
it becomes a finding and is disabled rather than hanging the build."
```

---

### Task 4: Register Gate 1 and put its config under platform ownership

Adding a file that governs contributions without governing the file itself is the defect a prior review found four separate times in this repository.

**Files:**
- Modify: `scripts/rulecheck.py` (`CHECKS`, `PLATFORM_OWNED_PATHS`)
- Modify: `.github/CODEOWNERS`
- Test: `tests/test_rulecheck.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rulecheck.py`:

```python
def test_publishability_config_is_platform_owned():
    from scripts.rulecheck import PLATFORM_OWNED_PATHS

    assert "/publishability.yaml" in PLATFORM_OWNED_PATHS


def test_publishability_is_registered():
    from scripts.rulecheck import CHECKS

    assert "publishability" in CHECKS


def test_codeowners_gives_publishability_to_platform(tmp_path):
    """A team must not be able to take ownership of the gate that governs it."""
    from scripts.rulecheck import check_codeowners

    repo = _repo_with_codeowners(
        tmp_path,
        "/publishability.yaml @org/payments\n",
    )
    findings = check_codeowners(repo)
    assert any("publishability" in f for f in findings)
```

Reuse the existing `_repo_with_codeowners` helper in that file; if its name differs, use the existing fixture that builds a temporary repository with a CODEOWNERS file, and keep this test consistent with the neighbouring ownership tests.

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_rulecheck.py -k publishability -v`
Expected: FAIL on all three.

- [ ] **Step 3: Implement**

In `scripts/rulecheck.py`, add `"/publishability.yaml"` to `PLATFORM_OWNED_PATHS`, and add the check to the registry:

```python
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
```

In `.github/CODEOWNERS`, add alongside the other platform-owned paths:

```
/publishability.yaml @org/platform
```

- [ ] **Step 4: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py .github/CODEOWNERS tests/test_rulecheck.py
git commit -m "feat: register Gate 1 and put its config under platform ownership

A file that governs contributions must itself be governed. Adding one
without adding it to PLATFORM_OWNED_PATHS is the same defect this
repository has already found four times."
```

---

### Task 5: Gate 2 denylist loading, with confidential diagnostics

**Files:**
- Create: `scripts/privatescan.py`
- Test: `tests/test_privatescan.py`

**Interfaces:**
- Produces:
  - `class DenylistError(Exception)`
  - `load_denylist(env: Mapping[str, str], repo_root: Path) -> list[dict]` where each dict has `id` and `value`
  - Module constant `DENYLIST_PLACEHOLDER = "<denylist-path>"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_privatescan.py`:

```python
import os
import textwrap
from pathlib import Path

import pytest

from scripts.privatescan import DENYLIST_PLACEHOLDER, DenylistError, load_denylist

REPO_ROOT = Path(__file__).resolve().parents[1]

VALID = """\
version: 1
terms:
  - id: private-term-01
    value: "alphaterm"
"""


def write_denylist(tmp_path: Path, body: str, name: str = "denylist.yaml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_valid_denylist_loads(tmp_path):
    path = write_denylist(tmp_path, VALID)
    terms = load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
    assert [t["id"] for t in terms] == ["private-term-01"]


def test_unset_variable_fails():
    with pytest.raises(DenylistError, match="PUBLISHABILITY_TERMS_FILE"):
        load_denylist({}, REPO_ROOT)


def test_missing_file_fails_without_printing_the_path(tmp_path):
    secret_path = tmp_path / "alphaterm-denylist.yaml"
    with pytest.raises(DenylistError) as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(secret_path)}, REPO_ROOT)
    assert DENYLIST_PLACEHOLDER in str(exc.value)
    assert "alphaterm" not in str(exc.value)
    assert str(secret_path) not in str(exc.value)


def test_denylist_inside_the_repository_fails(tmp_path):
    inside = REPO_ROOT / "denylist.yaml"
    inside.write_text(VALID, encoding="utf-8")
    try:
        with pytest.raises(DenylistError, match="inside the repository"):
            load_denylist({"PUBLISHABILITY_TERMS_FILE": str(inside)}, REPO_ROOT)
    finally:
        inside.unlink()


def test_symlinked_denylist_fails(tmp_path):
    real = write_denylist(tmp_path, VALID, "real.yaml")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    with pytest.raises(DenylistError, match="symlink"):
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(link)}, REPO_ROOT)


@pytest.mark.parametrize(
    "body, expected",
    [
        ("[]", "mapping"),
        ("version: 1\n", "exactly"),
        ("version: 2\nterms: [{id: private-term-01, value: x}]\n", "version"),
        ("version: 1\nterms: []\n", "non-empty"),
        ("version: 1\nterms: [{id: bad-id, value: x}]\n", "id"),
        ("version: 1\nterms: [{id: private-term-01}]\n", "exactly"),
        ("version: 1\nterms: [{id: private-term-01, value: ''}]\n", "value"),
        (
            "version: 1\nterms: [{id: private-term-01, value: a},"
            " {id: private-term-01, value: b}]\n",
            "unique",
        ),
        (
            "version: 1\nterms: [{id: private-term-01, value: 'Ab'},"
            " {id: private-term-02, value: 'aB'}]\n",
            "collide",
        ),
        ("version: 1\nversion: 1\nterms: [{id: private-term-01, value: x}]\n", "duplicate"),
    ],
)
def test_malformed_denylist_fails(tmp_path, body, expected):
    path = write_denylist(tmp_path, body)
    with pytest.raises(DenylistError, match=expected):
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)


def test_term_deriving_to_empty_is_rejected(tmp_path):
    """An empty derived term matches every string, so it must invalidate the
    denylist rather than flag every file."""
    path = write_denylist(
        tmp_path,
        "version: 1\nterms: [{id: private-term-01, value: '---'}]\n",
    )
    with pytest.raises(DenylistError, match="empty"):
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)


def test_denylist_values_never_appear_in_errors(tmp_path):
    path = write_denylist(
        tmp_path,
        "version: 1\nterms: [{id: private-term-01, value: 'zebrasecret'},"
        " {id: private-term-01, value: 'zebrasecret'}]\n",
    )
    with pytest.raises(DenylistError) as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
    assert "zebrasecret" not in str(exc.value)
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.privatescan'`.

- [ ] **Step 3: Implement**

Create `scripts/privatescan.py` with the loader. Note the deliberate ordering: the environment-supplied path is never printed, because until the denylist parses the scanner cannot check that path against the terms, and a path can itself contain one.

```python
"""Gate 2: scan the repository for terms held in an out-of-repository denylist.

Nothing derived from a private term is ever committed. This module reads its
denylist from a path given by PUBLISHABILITY_TERMS_FILE and never prints a term,
a matched substring, or the denylist path.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Mapping

import yaml

DENYLIST_ENV = "PUBLISHABILITY_TERMS_FILE"
DENYLIST_PLACEHOLDER = "<denylist-path>"
TERM_ID_RE = __import__("re").compile(r"^private-term-[0-9]{2,}$")


class DenylistError(Exception):
    """The private denylist is unusable. Always fatal: a scan that could not
    run must never be reportable as a scan that found nothing."""


def _open_no_symlink(path: Path):
    """Open a regular file, refusing symlinks in a way that survives a
    validation-then-read race: lstat, then O_NOFOLLOW, then fstat the
    descriptor actually opened."""
    lst = path.lstat()
    if stat.S_ISLNK(lst.st_mode):
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is a symlink, which is not allowed")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} could not be opened: {exc.strerror}") from exc
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is not a regular file")
    return fd


def load_denylist(env: Mapping[str, str], repo_root: Path) -> list[dict]:
    raw_path = env.get(DENYLIST_ENV)
    if not raw_path:
        raise DenylistError(
            f"{DENYLIST_ENV} is not set. Gate 2 cannot run, and a scan that could "
            f"not run is not a scan that found nothing."
        )
    path = Path(raw_path)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} does not exist or is not readable") from None
    root = repo_root.resolve()
    if resolved == root or root in resolved.parents:
        raise DenylistError(
            f"{DENYLIST_PLACEHOLDER} is inside the repository. The denylist holds "
            f"plaintext terms and must never be committable."
        )
    fd = _open_no_symlink(path)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is not UTF-8") from None

    from scripts.rulecheck import _NoDuplicateKeyLoader  # duplicate-key rejection, shared

    try:
        docs = list(yaml.load_all(text, Loader=_NoDuplicateKeyLoader))
    except Exception:
        # A parser exception can embed a source line, which can embed a term.
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is not valid YAML") from None
    if len(docs) != 1:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} must contain exactly one YAML document")
    doc = docs[0]
    if not isinstance(doc, dict):
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} must be a mapping")
    if set(doc) != {"version", "terms"}:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} root must have exactly 'version' and 'terms'")
    version = doc["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} version must be the integer 1")
    terms = doc["terms"]
    if not isinstance(terms, list) or not terms:
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} terms must be a non-empty list")

    seen_ids: set[str] = set()
    seen_norm: set[str] = set()
    for entry in terms:
        if not isinstance(entry, dict) or set(entry) != {"id", "value"}:
            raise DenylistError("each term must have exactly 'id' and 'value'")
        tid, value = entry["id"], entry["value"]
        if not isinstance(tid, str) or not TERM_ID_RE.match(tid):
            raise DenylistError(
                f"term id {tid!r} must match ^private-term-[0-9]{{2,}}$. Ids are opaque "
                f"on purpose: a descriptive id re-creates the crib it exists to avoid."
            )
        if not isinstance(value, str) or not value:
            raise DenylistError(f"term {tid} value must be a non-empty string")
        if tid in seen_ids:
            raise DenylistError(f"term ids must be unique, {tid!r} repeats")
        normalised = normalise(value)
        if normalised in seen_norm:
            raise DenylistError("two terms collide after normalisation")
        for mask in eligible_masks(normalised):
            if not apply_mask(mask, normalised):
                raise DenylistError(
                    f"term {tid} derives to the empty string under mask {mask}, which "
                    f"would match every file"
                )
        seen_ids.add(tid)
        seen_norm.add(normalised)
    return terms
```

`normalise`, `eligible_masks` and `apply_mask` arrive in Task 6; this task's tests that need them are the empty-derivation and collision cases, so implement Task 6's four functions first if running tasks strictly in order, or accept those two tests failing until Task 6 lands and mark them `xfail(strict=True)` exactly as Task 3 did.

- [ ] **Step 4: Run and confirm pass**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/privatescan.py tests/test_privatescan.py
git commit -m "feat: Gate 2 denylist loading with confidential diagnostics

The environment-supplied path is never printed. Until the denylist
parses, the scanner cannot check that path against the terms, so a path
containing one would be echoed by the error meant to report the problem.
Symlink rejection uses lstat, O_NOFOLLOW and fstat so it survives a
validation-then-read race."
```

---

### Task 6: Gate 2 normalisation, deletion masks and decoder views

This is the security-critical core. The algorithm below was prototyped and verified against thirteen cases before this plan was written; the table in Step 1 is that prototype's actual output.

**Files:**
- Modify: `scripts/privatescan.py`
- Test: `tests/test_privatescan.py`

**Interfaces:**
- Produces:
  - `normalise(s: str) -> str` (NFKC then casefold)
  - `eligible_masks(normalised_term: str) -> list[tuple[str, ...]]`
  - `apply_mask(mask: tuple[str, ...], s: str) -> str`
  - `candidate_views(raw: str) -> Iterator[tuple[str, str]]` yielding `(view_name, text)`
  - `find_term(raw: str, normalised_term: str) -> tuple[str, tuple[str, ...]] | None` returning `(view_name, mask)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_privatescan.py`:

```python
import base64

from scripts.privatescan import (
    apply_mask,
    candidate_views,
    eligible_masks,
    find_term,
    normalise,
)

TERM = "zephyrgate"  # synthetic. Never use a real private term in a test.


@pytest.mark.parametrize(
    "label, text",
    [
        ("plain", "see zephyrgate here"),
        ("embedded identifier", "zephyrgateAlerting"),
        ("hyphen split", "zephyr-gate"),
        ("space split", "zephyr gate"),
        ("underscore split", "ZEPHYR_GATE"),
        ("newline split", "zephyr\ngate"),
        ("digit padded", "z1e2p3h4y5r6g7a8t9e"),
        ("percent encoded", "https://x/zephyr%67ate"),
        ("base64 standard", "blob: " + base64.b64encode(TERM.encode()).decode()),
        ("base64 url-safe", "blob: " + base64.urlsafe_b64encode(TERM.encode()).decode()),
        ("base64 inside a longer run", "xx" + base64.b64encode(TERM.encode()).decode() + "yy"),
    ],
)
def test_evasions_are_caught(label, text):
    assert find_term(text, normalise(TERM)) is not None, label


@pytest.mark.parametrize("text", ["observability rules repo", "the gate is a zephyr of sorts"])
def test_clean_text_is_not_flagged(text):
    assert find_term(text, normalise(TERM)) is None


def test_base64_is_decoded_before_case_folding():
    """Base64 is case-sensitive. Normalising first destroys the payload, which
    was a deterministic false negative in an earlier design."""
    encoded = base64.b64encode(b"ExampleX").decode()
    assert encoded != encoded.casefold()
    assert find_term("blob: " + encoded, normalise("ExampleX")) is not None


def test_short_encoding_under_eight_characters_is_found():
    encoded = base64.b64encode(b"abc").decode()  # four characters
    assert len(encoded) == 4
    assert find_term("x " + encoded + " y", normalise("abc")) is not None


def test_mask_alignment_requires_the_same_mask_on_both_sides():
    """Term 'ab' against 'a-1b' matches under neither deletion alone."""
    assert find_term("a-1b", normalise("ab")) is not None
    assert apply_mask(("P",), "a-1b") == "a1b"
    assert apply_mask(("D",), "a-1b") == "a-b"
    assert apply_mask(("P", "D"), "a-1b") == "ab"


def test_digit_bearing_term_excludes_digit_masks():
    """Erasing a meaningful digit from the term makes every mask over-match."""
    masks = eligible_masks(normalise("a1b"))
    assert all("D" not in m for m in masks)
    assert eligible_masks(normalise("ab")) != masks


def test_finding_reports_view_and_mask():
    view, mask = find_term("zephyr-gate", normalise(TERM))
    assert view == "source"
    assert mask == ("P",)


def test_decoders_are_not_applied_to_another_decoders_output():
    """One layer only. Double-encoding is outside the guarantee and section 9
    says so; silently recursing would make the guarantee unbounded."""
    once = base64.b64encode(TERM.encode()).decode()
    twice = base64.b64encode(once.encode()).decode()
    assert find_term("blob: " + twice, normalise(TERM)) is None
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -k "evasion or base64 or mask" -v`
Expected: FAIL with `ImportError: cannot import name 'normalise'`.

- [ ] **Step 3: Implement**

Add to `scripts/privatescan.py`. This code was verified by prototype; the ordering of normalisation relative to decoding is the load-bearing detail.

```python
import base64
import re
import unicodedata
from typing import Iterator

LINE_BREAKS = {"\r", "\n", "", " ", " "}
_BASE64_ALPHABETS = {
    "base64-standard": re.compile(r"[A-Za-z0-9+/]{2,}={0,2}"),
    "base64-urlsafe": re.compile(r"[A-Za-z0-9_-]{2,}={0,2}"),
}
_PERCENT_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def normalise(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def _op_p(s: str) -> str:
    return "".join(
        c for c in s
        if not (unicodedata.category(c)[0] in "PZ" or unicodedata.category(c) == "Cf")
    )


def _op_l(s: str) -> str:
    return "".join(c for c in s.replace("\r\n", "") if c not in LINE_BREAKS)


def _op_d(s: str) -> str:
    return "".join(c for c in s if not (c.isascii() and c.isdigit()))


_OPS = {"P": _op_p, "L": _op_l, "D": _op_d}
_BASE_MASKS = [(), ("P",), ("L",), ("P", "L")]


def eligible_masks(normalised_term: str) -> list[tuple[str, ...]]:
    masks = list(_BASE_MASKS)
    if not any(c.isascii() and c.isdigit() for c in normalised_term):
        masks += [m + ("D",) for m in _BASE_MASKS]
    return masks


def apply_mask(mask: tuple[str, ...], s: str) -> str:
    for op in ("P", "L", "D"):  # fixed order, each applied at most once
        if op in mask:
            s = _OPS[op](s)
    return s


def _percent_view(raw: str) -> str:
    def decode(match: re.Match) -> str:
        token = match.group(0)
        octets = bytes(int(token[i + 1:i + 3], 16) for i in range(0, len(token), 3))
        return octets.decode("utf-8", "replace")

    return _PERCENT_RUN.sub(decode, raw)


def _base64_views(raw: str) -> Iterator[tuple[str, str]]:
    """Yield one view per decodable candidate.

    Operates on RAW text. Base64 is case-sensitive, so normalising or case
    folding before decoding changes the decoded bytes and produces a
    deterministic false negative.

    Trimming zero to three characters from each end means an invalid maximal
    run cannot suppress a valid candidate contained within it.
    """
    for name, pattern in _BASE64_ALPHABETS.items():
        for match in pattern.finditer(raw):
            run = match.group(0)
            for lead in range(4):
                for trail in range(4):
                    candidate = run[lead:len(run) - trail] if trail else run[lead:]
                    core = candidate.rstrip("=")
                    if len(core) < 2:
                        continue
                    explicit_padding = candidate[len(core):]
                    body = core + (explicit_padding or "=" * (-len(core) % 4))
                    if len(body) % 4:
                        continue
                    try:
                        decoder = (
                            base64.b64decode if name == "base64-standard"
                            else base64.urlsafe_b64decode
                        )
                        decoded = decoder(body).decode("utf-8", "replace")
                    except Exception:
                        continue
                    if not decoded:
                        continue
                    start = match.start() + lead
                    yield name, raw[:start] + decoded + raw[start + len(candidate):]


def candidate_views(raw: str) -> Iterator[tuple[str, str]]:
    yield "source", normalise(raw)
    yield "percent", normalise(_percent_view(raw))
    for name, view in _base64_views(raw):
        yield name, normalise(view)


def find_term(raw: str, normalised_term: str):
    """Return (view_name, mask) for the first match, or None.

    The mask is applied to BOTH the candidate and the term. Comparing a
    transformed candidate against an untransformed term is what makes a term
    with a meaningful digit over-match.
    """
    for mask in eligible_masks(normalised_term):
        needle = apply_mask(mask, normalised_term)
        if not needle:
            raise DenylistError("term derives to the empty string, which matches everything")
        for view_name, view in candidate_views(raw):
            if needle in apply_mask(mask, view):
                return view_name, mask
    return None
```

The escape view (`\uXXXX`, surrogate pairs, `\UXXXXXXXX`, `\xXX`, with backslash-parity handling) is deliberately **not** in this task. It is Task 7, so that this task's diff stays reviewable and the escape grammar gets its own gate.

- [ ] **Step 4: Run and confirm pass**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/privatescan.py tests/test_privatescan.py
git commit -m "feat: Gate 2 normalisation, deletion masks and decoder views

Decoding precedes normalisation. Base64 is case-sensitive, so folding
first changes the decoded bytes: RXhhbXBsZVg= case-folded decodes to
something else entirely, a deterministic false negative in the gate's
central mechanism.

Masks apply to candidate and term alike, a term deriving to empty is
rejected rather than matching everything, and digit masks are excluded
for terms whose own digits carry meaning."
```

---

### Task 7: Gate 2 escape view

**Files:**
- Modify: `scripts/privatescan.py`
- Test: `tests/test_privatescan.py`

**Interfaces:**
- Consumes: `candidate_views` from Task 6.
- Produces: an additional `("escape", text)` view yielded by `candidate_views`.

- [ ] **Step 1: Write the failing tests**

```python
def test_json_unicode_escape_is_decoded():
    assert find_term(r'"zephyrgate"', normalise(TERM)) is not None


def test_hex_escape_is_decoded():
    assert find_term(r'"zephyr\x67ate"', normalise(TERM)) is not None


def test_long_unicode_escape_is_decoded():
    assert find_term(r'"zephyr\U00000067ate"', normalise(TERM)) is not None


def test_surrogate_pair_is_decoded():
    # U+1F600 as a UTF-16 surrogate pair, adjacent to the term.
    assert find_term(r'"😀zephyrgate"', normalise(TERM)) is not None


def test_escape_after_odd_backslash_run_is_literal():
    r"""In \\u0062 the backslash is itself escaped, so no escape begins."""
    assert find_term(r'"zephyr\\u0067ate"', normalise(TERM)) is None


def test_invalid_escape_does_not_suppress_a_later_valid_one():
    assert find_term(r'"\uZZZZ zephyrgate"', normalise(TERM)) is not None


def test_out_of_range_long_escape_stays_literal():
    assert find_term(r'"zephyr\U0011FFFFate"', normalise(TERM)) is None
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -k escape -v`
Expected: FAIL, the escaped forms are not decoded.

- [ ] **Step 3: Implement**

Add to `scripts/privatescan.py` and yield it from `candidate_views` after the percent view:

```python
def _escape_view(raw: str) -> str:
    r"""Decode exactly one non-recursive layer of \uXXXX, an adjacent UTF-16
    surrogate pair, \UXXXXXXXX or \xXX.

    A backslash starts an escape only when preceded by an even-length run of
    consecutive backslashes, so \\u0062 is a literal backslash followed by
    'u0062' and decodes to nothing. After an invalid escape, scanning advances
    by exactly one character so it cannot suppress a later valid escape.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != "\\":
            out.append(raw[i])
            i += 1
            continue
        run = 0
        j = i
        while j < n and raw[j] == "\\":
            run += 1
            j += 1
        if run % 2 == 0:
            out.append(raw[i:j])
            i = j
            continue
        out.append(raw[i:j - 1])  # the escaping backslashes, minus the operative one
        i = j - 1
        kind = raw[i + 1] if i + 1 < n else ""
        widths = {"u": 4, "x": 2, "U": 8}
        if kind not in widths:
            out.append(raw[i])
            i += 1
            continue
        width = widths[kind]
        digits = raw[i + 2:i + 2 + width]
        if len(digits) != width or any(c not in "0123456789abcdefABCDEF" for c in digits):
            out.append(raw[i])
            i += 1
            continue
        value = int(digits, 16)
        if kind == "u" and 0xD800 <= value <= 0xDBFF:
            tail = raw[i + 6:i + 12]
            if len(tail) == 6 and tail[:2] == "\\u":
                low = int(tail[2:], 16) if all(
                    c in "0123456789abcdefABCDEF" for c in tail[2:]
                ) else -1
                if 0xDC00 <= low <= 0xDFFF:
                    combined = 0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)
                    out.append(chr(combined))
                    i += 12
                    continue
            out.append(raw[i])
            i += 1
            continue
        if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            out.append(raw[i])
            i += 1
            continue
        out.append(chr(value))
        i += 2 + width
    return "".join(out)
```

In `candidate_views`, insert after the percent view:

```python
    yield "escape", normalise(_escape_view(raw))
```

- [ ] **Step 4: Run and confirm pass**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/privatescan.py tests/test_privatescan.py
git commit -m "feat: Gate 2 escape view

One non-recursive layer of \\uXXXX, surrogate pairs, \\UXXXXXXXX and
\\xXX. Backslash parity decides whether an escape begins at all, and an
invalid escape advances by one character so it cannot mask a later valid
one."
```

---

### Task 8: Fail-closed discovery, path scanning and redaction

**Files:**
- Modify: `scripts/privatescan.py`, `scripts/rulecheck.py`
- Test: `tests/test_privatescan.py`, `tests/test_publishability.py`

**Interfaces:**
- Produces: `iter_scannable_files(root: Path) -> Iterator[tuple[str, str | None]]` shared by both gates. Yields `(path, text)` for readable UTF-8 regular files, and `(path, None)` never; a problem path yields a pre-formatted finding string in place of text, which callers append directly.
- Produces: `scan_repository(root: Path, terms: list[dict]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

```python
import subprocess


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_discovery_failure_is_a_finding_not_an_empty_scan(tmp_path, monkeypatch):
    """An unchecked git ls-files returning empty is a scanner that passes by
    scanning zero files."""
    from scripts import privatescan

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(2, "git")

    monkeypatch.setattr(privatescan.subprocess, "run", boom)
    findings = privatescan.scan_repository(tmp_path, [{"id": "private-term-01", "value": "x"}])
    assert findings and "discovery" in findings[0]


def test_nul_byte_is_a_finding_not_a_skip(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"harmless\x00content")
    findings = list(iter_scannable_files(repo))
    assert any("NUL" in f for _, f in findings if isinstance(f, str))


def test_symlink_is_a_finding(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "real.txt").write_text("hello", encoding="utf-8")
    (repo / "link.txt").symlink_to(repo / "real.txt")
    findings = list(iter_scannable_files(repo))
    assert any("symlink" in f for _, f in findings if isinstance(f, str))


def test_untracked_non_ignored_file_is_scanned(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "new.txt").write_text("zephyrgate", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert any("new.txt" in f for f in findings)


def test_ignored_file_is_not_scanned(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    (repo / "secret.txt").write_text("zephyrgate", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert not any("secret.txt" in f for f in findings)


def test_term_in_a_path_name_is_found(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "zephyrgate-notes.txt").write_text("clean", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings


def test_matching_path_is_redacted_in_every_finding(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "zephyrgate-notes.txt").write_text("zephyrgate", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings
    for finding in findings:
        assert "zephyrgate" not in finding
        assert "<redacted-path>" in finding


def test_findings_never_contain_the_term(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "clean-name.txt").write_text("a zephyrgate b", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings
    for finding in findings:
        assert "zephyrgate" not in finding
        assert "private-term-01" in finding
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_privatescan.py -k "discovery or nul or symlink or redact or path" -v`
Expected: FAIL, the functions do not exist.

- [ ] **Step 3: Implement**

Add to `scripts/privatescan.py`:

```python
import subprocess

REDACTED_PATH = "<redacted-path>"


def _git_paths(root: Path, *args: str) -> list[str]:
    """Run a git ls-files variant and CHECK ITS EXIT STATUS. An unchecked
    subprocess that returns empty is a scan of zero files reported as clean."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def iter_scannable_files(root: Path):
    """Yield (path, text) for readable UTF-8 regular files.

    Every discovered path is accounted for. A path that cannot be scanned
    yields (path, finding_string) so the caller records it: silence is what a
    broken scanner and a clean repository have in common.
    """
    try:
        paths = _git_paths(root, "--cached") + _git_paths(root, "--others", "--exclude-standard")
    except (subprocess.CalledProcessError, OSError) as exc:
        yield "", f"file discovery failed, so nothing was scanned: {exc}"
        return
    seen: set[str] = set()
    for rel in sorted(set(paths)):
        if rel in seen:
            yield rel, f"{rel}: duplicate path returned by discovery"
            continue
        seen.add(rel)
        full = root / rel
        try:
            resolved = full.resolve()
            if root.resolve() not in resolved.parents and resolved != root.resolve():
                yield rel, f"{rel}: path escapes the repository root"
                continue
        except OSError:
            yield rel, f"{rel}: path could not be resolved"
            continue
        if full.is_symlink():
            yield rel, f"{rel}: symlink, which is not scannable and not allowed"
            continue
        if not full.exists():
            yield rel, f"{rel}: discovered but missing"
            continue
        if not full.is_file():
            yield rel, f"{rel}: gitlink or non-regular file, which is not allowed"
            continue
        try:
            data = full.read_bytes()
        except OSError as exc:
            yield rel, f"{rel}: unreadable ({exc.strerror})"
            continue
        if b"\x00" in data:
            yield rel, f"{rel}: contains a NUL byte, so it is binary and is rejected"
            continue
        try:
            yield rel, data.decode("utf-8")
        except UnicodeDecodeError:
            yield rel, f"{rel}: not valid UTF-8"


def scan_repository(root: Path, terms: list[dict]) -> list[str]:
    normalised = [(t["id"], normalise(t["value"])) for t in terms]

    def render(path: str) -> str:
        for _, needle in normalised:
            if find_term(path, needle) is not None:
                return REDACTED_PATH
        return path.encode("unicode_escape").decode("ascii")

    findings: list[str] = []
    for path, payload in iter_scannable_files(root):
        safe = render(path)
        if not isinstance(payload, str) or payload.startswith(f"{path}:"):
            findings.append(payload.replace(path, safe, 1) if payload else "")
            continue
        for term_id, needle in normalised:
            hit = find_term(path, needle)
            if hit is not None:
                findings.append(f"{safe}: {term_id} (path, view={hit[0]}, mask={hit[1]})")
            hit = find_term(payload, needle)
            if hit is not None:
                findings.append(f"{safe}: {term_id} (view={hit[0]}, mask={hit[1]})")
    return [f for f in findings if f]
```

Move `iter_scannable_files` into a place both modules can import. Put it in `scripts/privatescan.py` and import it from `scripts/rulecheck.py`:

```python
from scripts.privatescan import iter_scannable_files
```

Then remove the `xfail` marker from `test_gate1_passes_on_the_real_repository` in `tests/test_publishability.py`.

- [ ] **Step 4: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest -q`
Expected: PASS, no xfail remaining.

- [ ] **Step 5: Commit**

```bash
git add scripts/privatescan.py scripts/rulecheck.py tests/
git commit -m "feat: fail-closed discovery, path scanning and redaction

git ls-files exit status is checked, because an unchecked subprocess
returning empty is a scan of zero files reported as clean. A NUL byte,
symlink, gitlink, unreadable file, non-UTF-8 file or path escape is a
finding, never a skip.

Paths are scanned as candidates too, and a path that itself matches is
printed as <redacted-path> so the finding meant to report a term does not
disclose it."
```

---

### Task 9: Wire Gate 2 into check.sh, fail-closed, with one named skip

**Files:**
- Modify: `scripts/privatescan.py` (CLI entry point), `scripts/check.sh`
- Test: `tests/test_privatescan.py`, `tests/test_check_sh.py`

**Interfaces:**
- Consumes: `load_denylist`, `scan_repository`.
- Produces: `python3 scripts/privatescan.py <root>` exiting 0 only after a complete clean scan.

- [ ] **Step 1: Write the failing tests**

```python
def test_cli_exits_non_zero_when_denylist_is_unset(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "privatescan.py"), str(tmp_path)],
        capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
    )
    assert result.returncode != 0
    assert "PUBLISHABILITY_TERMS_FILE" in result.stderr
```

And in `tests/test_check_sh.py`, following the existing skip-if-tooling-missing pattern in that file:

```python
def test_check_sh_fails_when_denylist_is_absent(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env.pop("PUBLISHABILITY_PRIVATE_SCAN", None)
    result = run_check_sh(repo_copy, env)
    assert result.returncode != 0
    assert "CHECKS FAILED" in result.stdout + result.stderr


def test_check_sh_honours_the_named_skip_only_under_ci(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "skip-untrusted-ci"

    env["CI"] = "true"
    assert run_check_sh(repo_copy, env).returncode == 0

    env.pop("CI")
    assert run_check_sh(repo_copy, env).returncode != 0


def test_check_sh_rejects_any_other_skip_value(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "yes"
    env["CI"] = "true"
    assert run_check_sh(repo_copy, env).returncode != 0


def test_ci_sentence_does_not_contradict_a_deliberate_skip(repo_copy):
    env = dict(os.environ)
    env.pop("PUBLISHABILITY_TERMS_FILE", None)
    env["PUBLISHABILITY_PRIVATE_SCAN"] = "skip-untrusted-ci"
    env["CI"] = "true"
    out = run_check_sh(repo_copy, env).stdout
    assert "CHECKS INCOMPLETE" in out
    assert "CI must never end here" not in out
```

- [ ] **Step 2: Run and confirm failure**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_check_sh.py -k "denylist or skip or ci_sentence" -v`
Expected: FAIL.

- [ ] **Step 3: Implement the CLI**

Append to `scripts/privatescan.py`:

```python
def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    try:
        terms = load_denylist(os.environ, root)
    except DenylistError as exc:
        print(f"[privatescan] {exc}", file=sys.stderr)
        return 1
    findings = scan_repository(root, terms)
    if findings:
        print(f"[privatescan] {len(findings)} finding(s):", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("[privatescan] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Implement the check.sh wiring**

Add a stage to `scripts/check.sh`, after the existing rulecheck stage:

```bash
stage "8. private term scan (Gate 2)"
# Gate 2 is fail-closed. An absent denylist is a FAILURE, not a caveat: the
# caveat mechanism exits 0, so Make, Actions and pre-push hooks would all read
# "could not run" as success.
#
# CI=true is an ordinary environment value, not authentication. This skip stops
# an absent denylist from accidentally becoming a green local run; it cannot
# stop a deliberate bypass, and section 9 of the design says so.
if [ "${PUBLISHABILITY_PRIVATE_SCAN:-}" = "skip-untrusted-ci" ] && [ "${CI:-}" = "true" ]; then
  caveat 'the private term scan did NOT run: this job is public-checks only and deliberately holds no denylist.'
elif [ -n "${PUBLISHABILITY_PRIVATE_SCAN:-}" ] && [ "${PUBLISHABILITY_PRIVATE_SCAN}" != "skip-untrusted-ci" ]; then
  printf 'PUBLISHABILITY_PRIVATE_SCAN=%s is not a recognised value; the only accepted skip is skip-untrusted-ci under CI=true\n' \
    "${PUBLISHABILITY_PRIVATE_SCAN}" >&2
  STATUS=1
elif [ "${PUBLISHABILITY_PRIVATE_SCAN:-}" = "skip-untrusted-ci" ]; then
  printf 'PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci is only accepted when CI=true\n' >&2
  STATUS=1
elif require python3; then
  python3 scripts/privatescan.py . || STATUS=1
fi
```

Then change the final caveat sentence so it does not contradict a deliberate CI skip. Replace:

```bash
  printf 'This is not a clean bill of health. CI must never end here.\n'
```

with:

```bash
  printf 'This is not a clean bill of health. Only the deliberate public-checks CI job may end here.\n'
```

- [ ] **Step 5: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" make check`
Expected: exits non-zero with `CHECKS FAILED`, because no denylist is configured. That is the correct new default.

Then confirm the CI mode works:

```bash
PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci CI=true PATH="$PWD/.venv/bin:$PATH" make check
```
Expected: exit 0, `CHECKS INCOMPLETE`, naming the private scan as not performed.

- [ ] **Step 6: Update the CI workflow to select the skip**

In `.github/workflows/ci.yaml`, rename the job and set the skip:

```yaml
jobs:
  public-checks:
    runs-on: ubuntu-latest
```

and in the `make check` step:

```yaml
      - name: make check
        env:
          BASE_REF: ${{ github.event.pull_request.base.sha }}
          PUBLISHABILITY_PRIVATE_SCAN: skip-untrusted-ci
        run: make check
```

Update `docs/branch-protection.md` to require the `public-checks` context rather than `check`, and to state that this job is not private-term enforcement.

- [ ] **Step 7: Commit**

```bash
git add scripts/privatescan.py scripts/check.sh .github/workflows/ci.yaml docs/branch-protection.md tests/
git commit -m "feat: wire Gate 2 into check.sh, fail-closed, with one named skip

An absent denylist now fails non-zero. Routing it through the caveat
mechanism was wrong: that path prints CHECKS INCOMPLETE and exits 0, so
every consumer of an exit status reads it as success.

The single accepted skip is honoured only under CI=true, which is a
cooperative mode selector and not authentication. The CI job is renamed
public-checks so its name states what it actually verifies, and the final
caveat sentence no longer contradicts that deliberate result."
```

---

## Self-Review

**Spec coverage.** Section 3's security boundary is Task 2's config comment and Task 5's loader; Gate 1 is Tasks 2-4; Gate 2 is Tasks 5-8; "What runs where" is Task 9; registration and ownership is Task 4; the CI relocation that section 3 requires before discovery is Task 1. Section 7's build order maps to Tasks 1-9 in order. Section 8's test list is distributed across the task tests. Sections 4, 5 and 6 belong to the other three plans.

**Known gap, deliberate:** section 7 step 4 requires observing Gate 2 fail against the real denylist before the document rewrite. That observation belongs to the document-rewrite plan, because it is the red state that plan turns green, and it cannot be performed here: no real denylist exists until the operator creates one outside the repository.

**Type consistency.** `iter_scannable_files` is defined once in Task 8 and imported by `check_publishability` from Task 3; Task 3 therefore ships with an `xfail(strict=True)` that Task 8 removes, which is why the marker is `strict` rather than a bare skip. `find_term` returns `(view_name, mask)` throughout, matching the finding format in Task 8 and the test in Task 6. `normalise`, `eligible_masks` and `apply_mask` are defined in Task 6 and consumed by Task 5's loader, which is the one backward dependency in the plan and is called out explicitly in Task 5 Step 3.
