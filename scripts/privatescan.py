"""Gate 2: scan the repository for terms held in an out-of-repository denylist.

Nothing derived from a private term is ever committed. This module reads its
denylist from a path given by PUBLISHABILITY_TERMS_FILE and never prints a term,
a matched substring, or the denylist path.
"""
from __future__ import annotations

import base64
import re
import unicodedata
from typing import Iterator


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


def candidate_views(raw: str) -> Iterator[tuple[str, str]]:
    yield "source", normalise(raw)
    yield "percent", normalise(_percent_view(raw))
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
