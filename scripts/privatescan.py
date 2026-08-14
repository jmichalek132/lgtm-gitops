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
import unicodedata
from pathlib import Path
from typing import Iterator, Mapping

import yaml


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
