"""Gate 2: scan the repository for terms held in an out-of-repository denylist.

Nothing derived from a private term is ever committed. This module reads its
denylist from a path given by PUBLISHABILITY_TERMS_FILE and never prints a term,
a matched substring, or the denylist path.
"""
from __future__ import annotations

import base64
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Iterator, Mapping

import yaml

# Make `scripts.rulecheck` importable when this file is run directly, e.g.
# `python3 scripts/privatescan.py .`, and not only via pytest. A direct
# script invocation puts this file's own directory (scripts/) on
# sys.path[0], not the repository root, so load_denylist's deferred
# `from scripts.rulecheck import ...` below would otherwise raise
# ModuleNotFoundError once a denylist actually parses far enough to reach
# it, outside a test run where the root is already on sys.path some other
# way. scripts/rulecheck.py needed the identical fix for the mirror-image
# import, `from scripts.privatescan import iter_scannable_files`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DenylistError(Exception):
    """The private denylist is unusable. Always fatal: a scan that could not
    run must never be reportable as a scan that found nothing."""


LINE_BREAKS = {"\r", "\n", "\x85", " ", " "}
_BASE64_ALPHABETS = {
    "base64-standard": re.compile(r"[A-Za-z0-9+/]{2,}={0,2}"),
    "base64-urlsafe": re.compile(r"[A-Za-z0-9_-]{2,}={0,2}"),
}
_PERCENT_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_WHITESPACE = re.compile(r"\s+")


def normalise(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def _op_p(s: str) -> str:
    return "".join(
        c for c in s
        if not (
            unicodedata.category(c)[0] in "PZSM"
            or unicodedata.category(c) == "Cf"
        )
    )


def _op_l(s: str) -> str:
    return "".join(
        c for c in s
        if not (c in LINE_BREAKS or unicodedata.category(c) == "Cc")
    )


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
                    except ValueError:  # binascii.Error subclasses ValueError
                        continue
                    if not decoded:
                        continue
                    yield name, decoded


_ESCAPE_WIDTHS = {"u": 4, "x": 2, "U": 8}
_HEX_DIGITS = "0123456789abcdefABCDEF"


def _escape_view(raw: str) -> str:
    r"""Decode exactly one non-recursive layer of \uXXXX, an adjacent UTF-16
    surrogate pair, \UXXXXXXXX or \xXX.

    A backslash starts an escape only when it is the last of an odd-length
    run of consecutive backslashes: each preceding pair is an already
    escaped backslash and stays untouched, so \\u0062 (an even-length run)
    is left as two literal backslashes followed by literal 'u0062' and
    decodes nothing. After an invalid escape, scanning advances by exactly
    one character, never by the escape's nominal width, so a corrupt escape
    cannot consume, and thereby suppress, a later valid one.

    Builds the result with a list plus one join, and never backtracks over
    a character once consumed, so this is linear in len(raw) regardless of
    how many escapes or backslash runs it contains.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != "\\":
            out.append(raw[i])
            i += 1
            continue
        j = i
        while j < n and raw[j] == "\\":
            j += 1
        run = j - i
        if run % 2 == 0:
            out.append(raw[i:j])
            i = j
            continue
        out.append(raw[i:j - 1])  # the escaped-backslash pairs, unchanged
        i = j - 1  # the last, operative backslash of the run
        kind = raw[i + 1] if i + 1 < n else ""
        width = _ESCAPE_WIDTHS.get(kind)
        if width is None:
            out.append(raw[i])
            i += 1
            continue
        digits = raw[i + 2:i + 2 + width]
        if len(digits) != width or any(c not in _HEX_DIGITS for c in digits):
            out.append(raw[i])
            i += 1
            continue
        value = int(digits, 16)
        if kind == "u" and 0xD800 <= value <= 0xDBFF:
            tail = raw[i + 6:i + 12]
            low_digits = tail[2:]
            if (
                len(tail) == 6
                and tail[:2] == "\\u"
                and all(c in _HEX_DIGITS for c in low_digits)
            ):
                low = int(low_digits, 16)
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


def candidate_views(raw: str) -> Iterator[tuple[str, str]]:
    yield "source", normalise(raw)
    yield "percent", normalise(_percent_view(raw))
    yield "escape", normalise(_escape_view(raw))
    unwrapped = _WHITESPACE.sub("", raw)
    sources = (raw, unwrapped) if unwrapped != raw else (raw,)
    seen: set[str] = set()
    for text in sources:
        for name, view in _base64_views(text):
            if view in seen:
                continue
            seen.add(view)
            yield name, normalise(view)


def find_term(raw: str, normalised_term: str) -> tuple[str, tuple[str, ...]] | None:
    """Return (view_name, mask) for the first match, or None.

    The mask is applied to BOTH the candidate and the term. Comparing a
    transformed candidate against an untransformed term is what makes a term
    with a meaningful digit over-match.

    Every eligible mask is validated (and the term derived) before any
    candidate view is even generated, so an empty-deriving mask always
    raises, regardless of whether an earlier mask would otherwise have
    matched first.
    """
    needles = []
    for mask in eligible_masks(normalised_term):
        needle = apply_mask(mask, normalised_term)
        if not needle:
            raise DenylistError("term derives to the empty string, which matches everything")
        needles.append((mask, needle))
    views = list(candidate_views(raw))
    for mask, needle in needles:
        for view_name, view in views:
            if needle in apply_mask(mask, view):
                return view_name, mask
    return None


DENYLIST_ENV = "PUBLISHABILITY_TERMS_FILE"
DENYLIST_PLACEHOLDER = "<denylist-path>"
TERM_ID_RE = re.compile(r"^private-term-[0-9]{2,}$")


def _open_no_symlink(path: Path) -> int:
    """Open a regular file, refusing a symlink in a way that survives a
    validation-then-read race.

    lstat is a first, cheap check, not the guarantee: the file at `path`
    could be replaced between that lstat and the open below. The actual
    guarantee is O_NOFOLLOW on the open itself (the kernel refuses to
    traverse a symlink for the final path component, race-free by
    construction) together with fstat on the descriptor that call actually
    returned, never on a stat taken beforehand.

    Linux and macOS both define os.O_NOFOLLOW, but on a platform that does
    not, `getattr(os, "O_NOFOLLOW", 0)` silently falls back to plain
    O_RDONLY and the open no longer refuses a symlink at the kernel level;
    the race-closing guarantee this function exists for is weaker there,
    resting only on the earlier lstat.
    """
    try:
        lst = path.lstat()
    except OSError as exc:
        raise DenylistError(
            f"{DENYLIST_PLACEHOLDER} could not be checked: {exc.strerror}"
        ) from exc
    if stat.S_ISLNK(lst.st_mode):
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is a symlink, which is not allowed")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise DenylistError(
            f"{DENYLIST_PLACEHOLDER} could not be opened: {exc.strerror}"
        ) from exc
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} is not a regular file")
    return fd


def load_denylist(env: Mapping[str, str], repo_root: Path) -> list[dict]:
    """Load and validate the out-of-repository denylist.

    Until this returns successfully, the scanner has not checked
    PUBLISHABILITY_TERMS_FILE against the denylist's own terms, so a path
    that itself happens to contain a term could be echoed by the very error
    meant to report the problem. Every diagnostic below therefore prints
    DENYLIST_PLACEHOLDER, never the environment-supplied path, and never
    forwards a parser exception's text verbatim: a YAML error can embed a
    source line, and a source line can embed a term.
    """
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

    # Deferred, not module-level: scripts/rulecheck.py is expected to import
    # iter_scannable_files from this module at module level once that
    # consolidation lands (see its TEMPORARY stand-in docstring), which would
    # make a module-level import back into rulecheck here a circular import.
    # A deferred import breaks the cycle regardless of which module a caller
    # imports first: by the time load_denylist actually runs, both modules
    # have finished their own top-level execution, so the name lookup below
    # always succeeds. Verified for both import orders before relying on it.
    from scripts.rulecheck import PublishabilityConfigError, _NoDuplicateKeyLoader

    try:
        docs = list(yaml.load_all(text, Loader=_NoDuplicateKeyLoader))
    except PublishabilityConfigError:
        # The shared loader's duplicate-key constructor raises this directly,
        # not a yaml.YAMLError, so it needs its own branch: falling through to
        # the except below would report "not valid YAML" and lose the word
        # "duplicate" a caller may be matching on. Its own message names only
        # a YAML mapping key, never a source line, but that text is still not
        # forwarded: a badly malformed file can put arbitrary content in a
        # key position too, and a key is not guaranteed non-secret just
        # because a well-formed file only ever puts a fixed vocabulary there.
        raise DenylistError(f"{DENYLIST_PLACEHOLDER} has a duplicate key") from None
    except Exception:
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
                f"a term id does not match {TERM_ID_RE.pattern!r}. Ids are opaque "
                f"on purpose: a descriptive id re-creates the crib it exists to avoid, "
                f"so a rejected id is not echoed back here."
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


REPO_MARKER = "<repository>"
REDACTED_PATH = "<redacted-path>"


class DiscoveryFinding(str):
    """An already-formatted discovery finding yielded by iter_scannable_files.

    A str subclass, so every caller keeps treating it as text, but a DISTINCT
    TYPE so a caller can tell it from file content without inspecting the
    text. The previous discriminator, `text.startswith(f"{path}:")`, was
    content-controlled: a file whose first bytes are its own
    repository-relative path followed by a colon was classified as a
    discovery finding, so neither gate ever scanned it and Gate 2 appended
    its ENTIRE raw content as a finding, which is the one thing Gate 2 must
    never print. It also let file content forge a discovery diagnostic,
    including a fake `[privatescan] ok` line, and let a real file named
    '<repository>' forge a total-discovery-failure report.

    This is NOT the earlier `isinstance(x, str)` defect: that branch could
    never fire, because content is a str too. DiscoveryFinding is a type no
    content path ever constructs.
    """


def _escape_path(path: str) -> str:
    """Render a path safe to print. A filename can contain control
    characters, and an unescaped one can rewrite the diagnostic that reports
    it. Duplicated from scripts/rulecheck.py's escape_path rather than
    imported: that module imports iter_scannable_files from this one at
    module level, and this is small enough that pulling the reverse
    dependency in just for it is not worth the coupling."""
    return path.encode("unicode_escape").decode("ascii")


def _safe_error_detail(exc: BaseException) -> str:
    """A short description of exc that is safe to print as-is.

    Never exc's own str(): a CalledProcessError's default message embeds its
    full argv, which here always includes the repository root path
    (`_git_paths` passes it to `-C`), and a bare OSError's embeds the
    filename it failed on. Either can defeat every redaction and escaping
    this module does elsewhere, since neither the repository root nor a
    resolve-time OSError's filename ever goes through the path-checking
    scan_repository otherwise applies to every candidate before printing it.
    """
    if isinstance(exc, subprocess.CalledProcessError):
        return f"exit status {exc.returncode}"
    strerror = getattr(exc, "strerror", None)
    return strerror or type(exc).__name__


def _git_paths(root: Path, *args: str) -> list[str]:
    """Run a git ls-files variant and CHECK ITS EXIT STATUS. An unchecked
    subprocess that returns empty is a scan of zero files reported as
    clean."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", *args],
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def iter_scannable_files(root: Path) -> Iterator[tuple[str, str]]:
    """Yield (path, text) for every file both gates should read.

    `text` is either the file's decoded UTF-8 content, a plain `str`, or a
    `DiscoveryFinding` (a `str` subclass): an already formatted finding of
    the form "{path}: ..." that a caller appends directly as-is. Every
    discovery, filesystem or decoding problem becomes a finding here, never
    a silent skip and never an uncaught exception. This is the single
    discovery primitive shared by Gate 1 (scripts/rulecheck.py's
    check_publishability) and Gate 2 (scan_repository below); both tell a
    discovery finding from real content with `isinstance(text,
    DiscoveryFinding)`, a TYPE check, never a text-prefix check. File
    CONTENT is attacker-controlled, so any discriminator based on the text
    itself (the previous one compared `text.startswith(f"{path}:")`) can be
    forged by a file whose own content happens to match it; a type no
    content path ever constructs cannot be forged that way.

    Every discovered path is accounted for and none is read from twice
    under a false name: a duplicate report from discovery, and a path that
    resolves outside the repository root, are findings in their own right,
    not silently deduplicated or silently followed.
    """
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        detail = _safe_error_detail(exc)
        yield REPO_MARKER, DiscoveryFinding(
            f"{REPO_MARKER}: repository root could not be resolved ({detail})"
        )
        return

    try:
        paths = _git_paths(root, "--cached") + _git_paths(root, "--others", "--exclude-standard")
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = _safe_error_detail(exc)
        yield REPO_MARKER, DiscoveryFinding(
            f"{REPO_MARKER}: file discovery failed, so nothing was scanned ({detail})"
        )
        return

    seen: set[str] = set()
    for rel in sorted(paths):
        if rel in seen:
            yield rel, DiscoveryFinding(f"{rel}: duplicate path returned by discovery")
            continue
        seen.add(rel)

        full = root / rel
        try:
            resolved = full.resolve()
        except OSError as exc:
            yield rel, DiscoveryFinding(
                f"{rel}: path could not be resolved ({_safe_error_detail(exc)})"
            )
            continue

        # Checked before the escape test, not after: every symlink is
        # rejected regardless of where it points, so one pointing outside
        # root is reported as a symlink, the more specific and more useful
        # diagnostic, rather than as an escape. A non-symlink leaf sitting
        # inside a symlinked ANCESTOR directory still reaches the escape
        # check below, since is_symlink() here only inspects the leaf.
        if full.is_symlink():
            yield rel, DiscoveryFinding(f"{rel}: symlink, which is not scannable and not allowed")
            continue

        if resolved != root_resolved and root_resolved not in resolved.parents:
            yield rel, DiscoveryFinding(f"{rel}: path escapes the repository root")
            continue

        # "discovered but missing" (git listed it, it is gone by read time)
        # and "gitlink or non-regular file" (it exists but is not a regular
        # file: a gitlink, a FIFO, a device) are different problems and are
        # reported as two separate findings rather than folded into one.
        if not full.exists():
            yield rel, DiscoveryFinding(f"{rel}: discovered but missing")
            continue
        if not full.is_file():
            yield rel, DiscoveryFinding(f"{rel}: gitlink or non-regular file, which is not allowed")
            continue

        try:
            data = full.read_bytes()
        except OSError as exc:
            yield rel, DiscoveryFinding(f"{rel}: unreadable ({_safe_error_detail(exc)})")
            continue

        if b"\x00" in data:
            yield rel, DiscoveryFinding(f"{rel}: contains a NUL byte, so it is binary and is rejected")
            continue

        try:
            yield rel, data.decode("utf-8")
        except UnicodeDecodeError:
            yield rel, DiscoveryFinding(f"{rel}: not valid UTF-8")

    # Discovery that succeeds and returns nothing is a scan of zero files
    # reported as clean, which is the failure both gates exist to prevent.
    # `seen` holds every path this generator accepted; empty means git
    # answered, answered with nothing, and both gates were about to pass on
    # an unexamined tree. Reached by a wrong root, an emptied index, or a
    # `.gitignore` that excludes everything untracked.
    if not seen:
        yield REPO_MARKER, DiscoveryFinding(
            f"{REPO_MARKER}: discovery returned no files at all, so nothing was scanned"
        )


def scan_repository(root: Path, terms: list[dict]) -> list[str]:
    """Scan every discovered file's content, and every discovered path, for
    every term.

    Before any path is printed, in a term finding or in a discovery finding,
    it is checked against the complete matching algorithm: if it matches any
    term, the whole path is replaced by REDACTED_PATH so the finding meant to
    report a term does not itself disclose one. A non-matching path is still
    escaped before printing, so a control character in a filename cannot
    rewrite the finding that names it.

    This stops direct emission of a matching path's characters. It does NOT
    stop inference from the existence, count, order or grouping of findings
    when the rest of the tree is otherwise known: a two-file tree with one
    named finding and one REDACTED_PATH finding identifies the second file by
    elimination. That is why every finding this returns, not only the ones
    that look redacted, is confidential.

    find_term is called at most once per (file, term) for the path and once
    per (file, term) for the content. The per-term path findings and the
    redaction decision share that single pass over the path rather than
    matching it twice, which would double the path-matching share of a scan
    whose content-matching cost already runs to seconds per term.
    """
    normalised = [(t["id"], normalise(t["value"])) for t in terms]

    findings: list[str] = []
    for path, payload in iter_scannable_files(root):
        path_hits = [(term_id, find_term(path, needle)) for term_id, needle in normalised]
        safe = REDACTED_PATH if any(hit is not None for _, hit in path_hits) else _escape_path(path)

        if isinstance(payload, DiscoveryFinding):
            findings.append(payload.replace(path, safe, 1))
            continue

        for term_id, hit in path_hits:
            if hit is not None:
                findings.append(f"{safe}: {term_id} (path, view={hit[0]}, mask={hit[1]})")
        for term_id, needle in normalised:
            hit = find_term(payload, needle)
            if hit is not None:
                findings.append(f"{safe}: {term_id} (view={hit[0]}, mask={hit[1]})")
    return findings


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
