import base64
import os
import stat as stdlib_stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import scripts.privatescan as privatescan
from scripts.privatescan import (
    DENYLIST_PLACEHOLDER,
    REDACTED_PATH,
    DenylistError,
    apply_mask,
    candidate_views,
    eligible_masks,
    find_term,
    iter_scannable_files,
    load_denylist,
    normalise,
    scan_repository,
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
        (
            "base64 url-safe",
            # A payload that actually needs the url-safe alphabet. Encoding the
            # bare term yields bytes identical to the standard alphabet, so the
            # url-safe decoder branch would never be reached.
            "blob: " + base64.urlsafe_b64encode(b"\xff\xfe" + TERM.encode()).decode(),
        ),
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


def test_mask_is_applied_to_the_term_not_just_the_candidate():
    """The candidate and the term must be masked with the SAME transform. A
    term that itself contains punctuation must be reduced the same way the
    candidate is, or an asymmetric implementation (mask the candidate, leave
    the term raw) would miss this match: the masked candidate never contains
    the unmasked term's punctuation, so comparison would silently fail."""
    hyphenated_term = normalise("zephyr-gate")
    assert find_term("prefix zephyr_gate suffix", hyphenated_term) is not None


def test_digit_bearing_term_excludes_digit_masks():
    """Erasing a meaningful digit from the term makes every mask over-match."""
    masks = eligible_masks(normalise("a1b"))
    assert all("D" not in m for m in masks)
    assert eligible_masks(normalise("ab")) != masks


def test_finding_reports_view_and_mask():
    view, mask = find_term("zephyr-gate", normalise(TERM))
    assert view == "source"
    assert mask == ("P",)


@pytest.mark.parametrize(
    "text, expected_mask",
    [
        ("zephyr-\ngate", ("P", "L")),
        ("zephyr1\n2gate", ("L", "D")),
        ("z-1e2p\n3h4y5r6g7a8t9e", ("P", "L", "D")),
    ],
)
def test_every_eligible_mask_has_a_concrete_input(text, expected_mask):
    """Without these three, ('P','L'), ('L','D') and ('P','L','D') are never
    produced by any test in the suite."""
    assert find_term(text, normalise(TERM)) == ("source", expected_mask)


def test_decoders_are_not_applied_to_another_decoders_output():
    """One layer only. Double-encoding is outside the guarantee and section 9
    says so; silently recursing would make the guarantee unbounded."""
    once = base64.b64encode(TERM.encode()).decode()
    twice = base64.b64encode(once.encode()).decode()
    assert find_term("blob: " + twice, normalise(TERM)) is None


def test_term_deriving_to_empty_under_a_mask_raises_not_matches():
    """A term that derives to the empty string under some eligible mask must
    raise, not match. '---' under punctuation-deletion becomes empty, and an
    empty needle is a substring of every candidate, so this must be an error
    rather than a universal match."""
    with pytest.raises(DenylistError, match="empty"):
        find_term("completely unrelated text with no private term in it", normalise("---"))


def test_empty_deriving_term_raises_even_when_an_earlier_mask_would_match():
    """A term deriving to empty under some mask must raise regardless of
    whether an earlier, non-emptying mask would have found a literal match
    first. Order must not matter: this is a property of the TERM, not of
    which candidate happens to be scanned first. Distinct from the test
    above, which uses text with no literal match under any mask and so does
    not exercise the order-dependence bug on its own."""
    with pytest.raises(DenylistError, match="empty"):
        find_term("yaml doc:\n--- \nfoo: bar", normalise("---"))


def test_candidate_views_yields_only_source_percent_escape_and_base64():
    """Scope boundary for the view generator: these five view kinds, and
    nothing else, are ever produced. source, percent and escape are
    unconditional; the two base64 views are not (they depend on the input
    containing a decodable run)."""
    names = {name for name, _ in candidate_views("plain text")}
    assert {"source", "percent", "escape"} <= names
    assert names <= {"source", "percent", "escape", "base64-standard", "base64-urlsafe"}


# Task 7: the escape view. Decodes one non-recursive layer of \uXXXX, \xXX,
# \UXXXXXXXX and an adjacent UTF-16 surrogate pair. Backslash parity decides
# whether an escape begins at all (an even-length run of backslashes is all
# literal; an odd-length run's last backslash may start one); an invalid
# escape must advance scanning by exactly one character so it cannot mask a
# later valid one.
#
# Several of these deliberately call privatescan._escape_view directly
# rather than going through find_term. For the "stays literal" claims,
# find_term's None is not precise enough on its own: a bug that quietly
# turns an invalid escape into some *other* single character (instead of
# leaving the source text untouched) would also produce None, since that
# wrong character still would not spell part of "zephyrgate". Only checking
# _escape_view's exact output proves nothing was decoded.


def test_json_unicode_escape_is_decoded():
    escaped = '"zephyr' + chr(92) + 'u0067ate"'
    assert find_term(escaped, normalise(TERM)) is not None


def test_hex_escape_is_decoded():
    assert find_term(r'"zephyr\x67ate"', normalise(TERM)) is not None


def test_long_unicode_escape_is_decoded():
    assert find_term(r'"zephyr\U00000067ate"', normalise(TERM)) is not None


def test_surrogate_pair_is_decoded():
    """A valid adjacent UTF-16 surrogate pair combines into U+1F600. This one
    is checked on _escape_view directly rather than through find_term: the P
    mask deletes Symbol-category characters as punctuation, and an emoji is
    category So, so a needle built around one can match straight through the
    mask without any decoding happening at all (confirmed empirically: an
    earlier version of this test using find_term passed before _escape_view
    even existed, because the P-masked needle degraded to its plain ASCII
    suffix, which was already sitting in the raw hex digits). That is a real
    property of the masking design, not a bug, but it makes an emoji-bearing
    needle useless as find_term bait for this specific behaviour."""
    combined = privatescan._escape_view(chr(92) + "ud83d" + chr(92) + "ude00")
    assert combined == "\U0001F600"


def test_unpaired_high_surrogate_stays_literal():
    """The same high surrogate as above with no low surrogate adjacent:
    pairing must not happen speculatively off half a pair."""
    literal = chr(92) + "ud83d"
    assert privatescan._escape_view(literal) == literal


def test_escape_after_odd_backslash_run_is_literal():
    r"""In \\u0067 the backslash is itself escaped, so no escape begins. A
    scan that looks for a bare \u pattern without tracking backslash parity
    would find an "escape" starting at the second backslash and wrongly
    decode 'g'."""
    assert find_term(r'"zephyr\\u0067ate"', normalise(TERM)) is None


def test_invalid_escape_advances_by_exactly_one_character():
    r"""A width-sized skip on failure, instead of a single-character one,
    would swallow the backslash that starts the very next escape. \uZ is an
    invalid \u escape (its 4-digit window runs into the following
    backslash), and its own literal filler ('u', 'Z') is shorter than \u's
    4-digit width, so the two behaviors diverge: single-character
    advancement lands exactly on the next backslash and decodes the
    following g to 'g'; a width-sized jump overshoots into that
    escape's own digits and never recognises it as a fresh escape start."""
    probe = chr(92) + "uZ" + chr(92) + "u0067"
    expected = chr(92) + "uZg"
    assert privatescan._escape_view(probe) == expected


def test_out_of_range_long_escape_stays_literal():
    assert find_term(r'"zephyr\U0011FFFFate"', normalise(TERM)) is None


def test_lone_low_surrogate_stays_literal():
    """DC00-DFFF is a low surrogate: valid hex, in chr()'s range, but not a
    Unicode scalar value on its own."""
    assert privatescan._escape_view(r"\udc00") == r"\udc00"


def test_long_escape_landing_in_surrogate_range_stays_literal():
    r"""\U can spell a surrogate value (D800-DFFF) directly in its 8 hex
    digits; it must stay literal exactly like a lone \u surrogate does."""
    assert privatescan._escape_view(r"\U0000D800") == r"\U0000D800"


# Regression tests for false negatives found in fix round 1. Each of these
# was confirmed to FAIL (return None where a match is required) against the
# code as originally committed, before the corresponding fix was applied.
# See task-6-report.md for the pre-fix run transcripts.


@pytest.mark.parametrize(
    "text",
    [
        "blob:\nemVwaHly\nZ2F0ZQ==",
        "-----BEGIN X-----\nemVwaHly\nZ2F0ZQ==\n-----END X-----",
    ],
)
def test_base64_wrapped_across_a_line_break_is_caught(text):
    """PEM blocks, MIME 76-column wrapping, folded YAML scalars and wrapped
    JSON all break a base64 payload across lines. Alphabet-run extraction
    happens on raw text before any mask, so the L mask (which only applies
    after decoding) cannot repair a run that was severed before decoding."""
    assert find_term(text, normalise(TERM)) is not None


def test_control_character_split_is_caught():
    assert find_term("zephyr\tgate", normalise(TERM)) is not None


@pytest.mark.parametrize(
    "text",
    [
        "/search?q=zephyr+gate",
        "| zephyr | gate |",
    ],
)
def test_symbol_split_is_caught(text):
    """'+' (Sm) and '|' (Sm) are symbols, not punctuation, so the P mask as
    originally written (category P and Z only) left them untouched."""
    assert find_term(text, normalise(TERM)) is not None


def test_combining_mark_split_is_caught():
    """U+0333 COMBINING DOUBLE LOW LINE (category Mn) sits between the two
    halves of the term. The P mask as originally written did not strip
    combining marks."""
    assert find_term("zephyr̳gate", normalise(TERM)) is not None


def test_scanning_a_large_file_is_not_quadratic():
    """The committed pre-fix code took 137 seconds on an 11KB file and would
    have taken hours on the largest file in this repository."""
    import time
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    big = (repo_root / "docs" / "superpowers" / "specs"
           / "2026-08-14-publishable-example-design.md").read_text(encoding="utf-8")
    start = time.perf_counter()
    find_term(big, normalise(TERM))
    assert time.perf_counter() - start < 5.0


# Task 5: load_denylist and its supporting validation.
#
# The property that matters most here is confidentiality of diagnostics: until
# the denylist has parsed successfully, nothing derived from the
# environment-supplied path, or from the file's own content, may appear in a
# raised DenylistError. Every test below that exercises a pre-success error
# path asserts the placeholder is present AND that the concrete secret-shaped
# input is absent, not just that some expected word matched.

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


def test_empty_variable_fails_the_same_way_as_unset():
    """A present-but-empty value is exactly as unusable as an absent one;
    env.get returning '' must not slip past the falsy check."""
    with pytest.raises(DenylistError, match="PUBLISHABILITY_TERMS_FILE"):
        load_denylist({"PUBLISHABILITY_TERMS_FILE": ""}, REPO_ROOT)


def test_missing_file_fails_without_printing_the_path(tmp_path):
    secret_path = tmp_path / "alphaterm-denylist.yaml"
    with pytest.raises(DenylistError) as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(secret_path)}, REPO_ROOT)
    assert DENYLIST_PLACEHOLDER in str(exc.value)
    assert "alphaterm" not in str(exc.value)
    assert str(secret_path) not in str(exc.value)


def test_denylist_inside_the_repository_fails(tmp_path):
    inside = REPO_ROOT / "alphaterm-denylist.yaml"
    inside.write_text(VALID, encoding="utf-8")
    try:
        with pytest.raises(DenylistError, match="inside the repository") as exc:
            load_denylist({"PUBLISHABILITY_TERMS_FILE": str(inside)}, REPO_ROOT)
        assert DENYLIST_PLACEHOLDER in str(exc.value)
        assert str(inside) not in str(exc.value)
        assert "alphaterm" not in str(exc.value)
    finally:
        inside.unlink()


def test_symlinked_denylist_fails(tmp_path):
    real = write_denylist(tmp_path, VALID, "alphaterm-real.yaml")
    link = tmp_path / "alphaterm-link.yaml"
    link.symlink_to(real)
    with pytest.raises(DenylistError, match="symlink") as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(link)}, REPO_ROOT)
    assert DENYLIST_PLACEHOLDER in str(exc.value)
    assert str(link) not in str(exc.value)
    assert str(real) not in str(exc.value)
    assert "alphaterm" not in str(exc.value)


def test_symlink_rejection_does_not_trust_an_earlier_stat(tmp_path, monkeypatch):
    """lstat is a first, cheap check, not the guarantee. If it is bypassed (a
    real validation-then-read race swaps the file between the lstat and the
    open; here the check itself is replaced with one that always says "not a
    symlink", scoped to this module only so nothing else in the test run is
    affected), the file must still be refused. The only thing that can still
    catch it is the kernel-level O_NOFOLLOW on the real open() call plus the
    fstat on the descriptor that call actually returns: an implementation
    that only lstat-checked before opening would let this symlink straight
    through once its lstat-based guard stops saying so."""
    real = write_denylist(tmp_path, VALID, "real.yaml")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)

    class _LyingStat:
        S_ISREG = staticmethod(stdlib_stat.S_ISREG)
        S_ISLNK = staticmethod(lambda mode: False)

    monkeypatch.setattr(privatescan, "stat", _LyingStat)
    with pytest.raises(DenylistError):
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(link)}, REPO_ROOT)


def test_open_no_symlink_raises_denylisterror_not_oserror_when_the_file_vanishes(tmp_path):
    """The validate-then-read race can also run the other way: the file is
    gone by the time _open_no_symlink's own lstat runs, after an earlier
    existence check already passed. That lstat call must convert OSError to
    DenylistError like every other filesystem call in this module, not let a
    bare FileNotFoundError escape as an uncaught traceback."""
    missing = tmp_path / "gone" / "alphaterm-denylist.yaml"
    with pytest.raises(DenylistError) as exc:
        privatescan._open_no_symlink(missing)
    assert DENYLIST_PLACEHOLDER in str(exc.value)
    assert str(missing) not in str(exc.value)


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
        ("version: 1\nterms: [1, 2, 3]\n", "exactly"),
        ("version: 1\nterms: [{id: 123, value: x}]\n", "id"),
        ("version: 1\nterms: [{id: private-term-01, value: 123}]\n", "value"),
        ("version: true\nterms: [{id: private-term-01, value: x}]\n", "version"),
        ("version: 1\nterms: 'not-a-list'\n", "non-empty"),
        (
            "version: 1\nterms: [{id: private-term-01, value: x}]\n"
            "---\nversion: 1\nterms: [{id: private-term-02, value: y}]\n",
            "exactly one YAML document",
        ),
        ("version: 1\nterms: [{id: private-term-01, value: 'x'\n", "not valid YAML"),
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


def test_duplicate_key_error_does_not_echo_the_key_name(tmp_path):
    """The shared loader's duplicate-key detector fires during YAML
    construction, before this module's own schema check runs, so it can see
    an arbitrary top-level key in a badly malformed file. Its message must
    still not be forwarded verbatim: the key position is not guaranteed
    non-secret just because a well-formed file only ever puts
    'version'/'terms'/'id'/'value' there."""
    path = write_denylist(
        tmp_path,
        "zzzinventedsecretkey: 1\nzzzinventedsecretkey: 2\nversion: 1\nterms: []\n",
    )
    with pytest.raises(DenylistError, match="duplicate") as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
    assert "zzzinventedsecretkey" not in str(exc.value)


def test_rejected_term_id_is_not_echoed(tmp_path):
    """A term id is meant to be an opaque private-term-NN identifier, never
    the term itself, but nothing stops a malformed file from putting
    arbitrary text there instead. The rejection message must not echo it
    back, on the same theory as the path and the YAML parser exception: the
    error that reports a problem with the file must not become a new way to
    read the file."""
    path = write_denylist(
        tmp_path,
        "version: 1\nterms: [{id: 'zzzleakyidvalue', value: 'x'}]\n",
    )
    with pytest.raises(DenylistError, match="id") as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
    assert "zzzleakyidvalue" not in str(exc.value)


def test_non_utf8_denylist_fails_without_printing_the_bytes(tmp_path):
    path = tmp_path / "denylist.yaml"
    path.write_bytes(b"version: 1\nterms:\n  - id: private-term-01\n    value: \xff\xfe\n")
    with pytest.raises(DenylistError, match="UTF-8") as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
    assert DENYLIST_PLACEHOLDER in str(exc.value)


def test_permission_denied_denylist_fails_without_printing_the_path(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    path = write_denylist(tmp_path, VALID, "alphaterm-noperm.yaml")
    path.chmod(0o000)
    try:
        with pytest.raises(DenylistError, match="could not be opened") as exc:
            load_denylist({"PUBLISHABILITY_TERMS_FILE": str(path)}, REPO_ROOT)
        assert str(path) not in str(exc.value)
    finally:
        path.chmod(0o644)


def test_directory_as_denylist_path_fails(tmp_path):
    d = tmp_path / "alphaterm-dir"
    d.mkdir()
    with pytest.raises(DenylistError, match="not a regular file") as exc:
        load_denylist({"PUBLISHABILITY_TERMS_FILE": str(d)}, REPO_ROOT)
    assert str(d) not in str(exc.value)


# Task 8: file discovery, path scanning and path redaction.
#
# iter_scannable_files is the shared discovery primitive: Gate 1
# (scripts/rulecheck.py's check_publishability) and Gate 2 (scan_repository
# below) both consume it. The "{path}: ..." prefix convention it produces on
# a problem path is what check_publishability uses to tell a discovery
# finding from real file content (text.startswith(f"{path}:"), never
# isinstance: both are str, and that was a live defect earlier in this plan
# where the isinstance branch never fired and every discovery error was
# silently pattern-matched as content).
#
# Two of the plan's own Step-3 tests are adapted here rather than copied
# verbatim. The originals filtered with `if isinstance(f, str)`, which is
# always true given the interface contract (a problem path never yields
# anything but a str), so the filter was vacuous; it is dropped below so the
# assertion tests the actual behaviour instead of a condition that can never
# be false.


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_discovery_failure_is_a_finding_not_an_empty_scan(tmp_path, monkeypatch):
    """An unchecked git ls-files returning empty is a scanner that passes by
    scanning zero files."""

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(2, "git")

    monkeypatch.setattr(privatescan.subprocess, "run", boom)
    findings = scan_repository(tmp_path, [{"id": "private-term-01", "value": "x"}])
    assert findings and "discovery" in findings[0]


def test_discovery_failure_does_not_leak_the_repository_root_path(tmp_path):
    """A real (unmocked) `git ls-files` failure raises CalledProcessError,
    and that exception's own str() embeds its full argv, which always
    includes the repository root path (`_git_paths` passes it to `-C`). The
    finding for this failure is keyed to REPO_MARKER, not to the root path,
    so scan_repository's per-candidate redaction never inspects the root
    path at all: if the exception's raw text leaked into the finding
    verbatim, a term embedded in the repository's own checkout path would
    reach the output regardless of what any file in the tree contains.
    Confirmed against the unmocked path: a real, not simulated, discovery
    failure (this directory is never git-initialised)."""
    repo = tmp_path / "zephyrgate-checkout"
    repo.mkdir()
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings
    for finding in findings:
        assert "zephyrgate" not in finding


def test_nul_byte_is_a_finding_not_a_skip(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"harmless\x00content")
    findings = list(iter_scannable_files(repo))
    assert any("NUL" in text for _, text in findings)


def test_symlink_is_a_finding(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "real.txt").write_text("hello", encoding="utf-8")
    (repo / "link.txt").symlink_to(repo / "real.txt")
    findings = list(iter_scannable_files(repo))
    assert any("symlink" in text for _, text in findings)


def test_a_file_literally_named_repo_marker_is_rejected_not_scanned(tmp_path):
    """REPO_MARKER ("<repository>") is the key this generator uses for its
    own whole-repository findings, not a reserved path. A real file with
    that exact name must not be scanned as ordinary content: DiscoveryFinding
    already stops its content from being mistaken for a discovery finding,
    but a genuine whole-repository finding and one about this file would
    still collide on the same key. Reject the path instead."""
    repo = _git_repo(tmp_path)
    (repo / privatescan.REPO_MARKER).write_text("clean", encoding="utf-8")
    findings = dict(iter_scannable_files(repo))
    assert privatescan.REPO_MARKER in findings[privatescan.REPO_MARKER]
    assert isinstance(findings[privatescan.REPO_MARKER], privatescan.DiscoveryFinding)


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


def test_zero_discovered_files_is_a_finding_not_a_clean_scan(tmp_path):
    """Discovery that succeeds and returns nothing is a scan of zero files
    reported as clean, the exact failure both gates exist to prevent. An
    initialised repository with no tracked and no untracked files must
    still produce a finding, not an empty (and therefore passing) result."""
    repo = _git_repo(tmp_path)  # initialised, no tracked and no untracked files
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings and "no files at all" in findings[0]


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
        assert REDACTED_PATH in finding


def test_findings_never_contain_the_term(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "clean-name.txt").write_text("a zephyrgate b", encoding="utf-8")
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings
    for finding in findings:
        assert "zephyrgate" not in finding
        assert "private-term-01" in finding


def test_content_that_mimics_a_discovery_finding_is_still_scanned(tmp_path):
    """A file whose first bytes are its own path and a colon must be scanned
    as CONTENT, and its content must never be printed. Under the old
    startswith() discriminator this file was classified as a discovery
    finding: neither gate scanned it, and Gate 2 appended its entire raw
    content, term included, as the finding."""
    repo = _git_repo(tmp_path)
    (repo / "c.md").write_text(
        "c.md: symlink, which is not scannable and not allowed\nzephyrgate\n",
        encoding="utf-8",
    )
    findings = scan_repository(repo, [{"id": "private-term-01", "value": "zephyrgate"}])
    assert findings
    for finding in findings:
        assert "zephyrgate" not in finding
    assert any("private-term-01" in f for f in findings)


# Beyond the plan's Step-1 tests: four gaps an earlier review found in the
# Task 4 stand-in, which this task's implementation must close.


def test_duplicate_path_is_reported_not_silently_collapsed(tmp_path, monkeypatch):
    """The Task 4 stand-in walked sorted(set(paths)), so a path discovery
    returned twice just vanished into one entry with no trace. A double
    report (from git, or a future caller bug) must surface as its own
    finding instead of disappearing into an ordinary scan."""
    repo = _git_repo(tmp_path)
    (repo / "dup.txt").write_text("clean", encoding="utf-8")

    def fake_git_paths(root, *args):
        return ["dup.txt"]

    monkeypatch.setattr(privatescan, "_git_paths", fake_git_paths)
    texts = [text for _, text in iter_scannable_files(repo)]
    assert sum("duplicate" in t for t in texts) == 1


def test_path_escaping_the_repository_root_is_a_finding(tmp_path, monkeypatch):
    """A path discovery reports that resolves outside the repository root (a
    literal `..` component, or in practice a symlinked ancestor directory)
    must be a finding rather than silently read from wherever it actually
    points."""
    repo = _git_repo(tmp_path)

    def fake_git_paths(root, *args):
        return ["../escape.txt"] if args and args[0] == "--cached" else []

    monkeypatch.setattr(privatescan, "_git_paths", fake_git_paths)
    findings = list(iter_scannable_files(repo))
    assert any("escapes" in text for _, text in findings)


def test_discovered_but_missing_is_distinct_from_non_regular_file(tmp_path):
    """A path git reports that is gone by read time (staged, then deleted
    from the working tree without `git rm`) is a different problem from a
    path that exists but is not a regular file, and must not share a
    message with it."""
    repo = _git_repo(tmp_path)
    (repo / "gone.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "gone.txt"], check=True)
    (repo / "gone.txt").unlink()

    findings = dict(iter_scannable_files(repo))
    assert "missing" in findings["gone.txt"]
    assert "non-regular" not in findings["gone.txt"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_non_regular_file_is_distinct_from_missing(tmp_path, monkeypatch):
    """A FIFO exists on disk but is neither a symlink nor a regular file:
    the 'gitlink or non-regular file' branch, not 'discovered but missing'.

    `git ls-files --others` itself never reports a FIFO (verified: git
    silently omits non-regular, non-symlink dirents from untracked-file
    discovery), so this cannot be reached by creating one loose in a real
    repository the way the symlink and NUL-byte cases can. A real gitlink
    (a submodule reference) hits the same branch but needs a second
    repository and remote to construct. Discovery is faked instead, the
    same technique used for the duplicate-path and path-escape tests above,
    so the filesystem-facing branch itself is exercised directly."""
    repo = _git_repo(tmp_path)
    os.mkfifo(repo / "pipe")

    def fake_git_paths(root, *args):
        return ["pipe"] if args and args[0] == "--cached" else []

    monkeypatch.setattr(privatescan, "_git_paths", fake_git_paths)
    findings = dict(iter_scannable_files(repo))
    assert "non-regular" in findings["pipe"]
    assert "missing" not in findings["pipe"]


def test_unreadable_file_message_uses_strerror_not_the_exception_repr(tmp_path):
    """The unreadable-file finding must report OSError.strerror ('Permission
    denied'), a stable, non-secret-shaped OS message, rather than the whole
    exception object, whose repr includes the offending path a second time
    and, on some platforms, other incidental detail."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions")
    repo = _git_repo(tmp_path)
    path = repo / "noperm.txt"
    path.write_text("x", encoding="utf-8")
    path.chmod(0o000)
    try:
        findings = dict(iter_scannable_files(repo))
        text = findings["noperm.txt"]
        assert "Permission denied" in text
        assert "PermissionError" not in text
    finally:
        path.chmod(0o644)


def test_path_matching_is_not_repeated_per_term(tmp_path, monkeypatch):
    """Performance guard: find_term is called per file and per term, and a
    whole-repository scan costs seconds per term. The redaction decision and
    the per-term path finding must share a single pass over
    find_term(path, ...): computing it once for redaction and again inside
    the per-term reporting loop would double the cost of exactly the part
    Task 8 was told not to duplicate. One clean file, two terms: find_term
    must be called on the path exactly twice, not four times."""
    repo = _git_repo(tmp_path)
    (repo / "clean.txt").write_text("clean", encoding="utf-8")

    calls: list[str] = []
    real_find_term = privatescan.find_term

    def counting_find_term(text, needle):
        calls.append(text)
        return real_find_term(text, needle)

    monkeypatch.setattr(privatescan, "find_term", counting_find_term)
    terms = [
        {"id": "private-term-01", "value": "zephyrgate"},
        {"id": "private-term-02", "value": "unrelatedword"},
    ]
    scan_repository(repo, terms)
    path_calls = [c for c in calls if c == "clean.txt"]
    assert len(path_calls) == len(terms)


def test_scan_repository_whole_repo_completes_quickly():
    """Whole-repository scan cost, recorded for the task report: 45 tracked
    files, one term, must stay well inside a sane budget rather than
    regressing toward the 137s/file quadratic blowup Task 6 measured and
    fixed for find_term itself."""
    import time

    start = time.perf_counter()
    scan_repository(REPO_ROOT, [{"id": "private-term-01", "value": "zephyrgate"}])
    elapsed = time.perf_counter() - start
    assert elapsed < 15.0


# Task 9: the CLI entry point (main), wired into scripts/check.sh's Gate 2
# stage. Gate 2 is fail-closed: an absent or unusable denylist must be a
# non-zero exit, never a zero exit and never "no findings".
#
# These invoke the real script as a subprocess rather than calling main()
# in-process, on purpose: a direct `python3 scripts/privatescan.py ...`
# invocation puts scripts/ on sys.path[0], not the repository root (unlike
# pytest's own import machinery), so this is the only way to exercise the
# `sys.path` bootstrap that load_denylist's deferred `from scripts.rulecheck
# import ...` needs once a denylist actually parses far enough to reach it
# (see scripts/rulecheck.py, which needed the identical fix in Task 8).

CLI = REPO_ROOT / "scripts" / "privatescan.py"

VALID_TERM_DENYLIST = (
    'version: 1\nterms:\n  - id: private-term-01\n    value: "zephyrgate"\n'
)


def test_cli_exits_non_zero_when_denylist_is_unset(tmp_path):
    result = subprocess.run(
        [sys.executable, str(CLI), str(tmp_path)],
        capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
    )
    assert result.returncode != 0
    assert "PUBLISHABILITY_TERMS_FILE" in result.stderr


def test_cli_clean_scan_exits_zero_and_prints_ok(tmp_path, tmp_path_factory):
    """Exercises the success path end-to-end, including the point where
    load_denylist's deferred rulecheck import actually runs (an unset
    denylist, by contrast, never reaches that import at all)."""
    repo = _git_repo(tmp_path)
    (repo / "clean.txt").write_text("nothing sensitive here", encoding="utf-8")
    denylist = tmp_path_factory.mktemp("denylist") / "denylist.yaml"
    denylist.write_text(VALID_TERM_DENYLIST, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CLI), str(repo)],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "PUBLISHABILITY_TERMS_FILE": str(denylist)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[privatescan] ok" in result.stdout


def test_cli_reports_findings_and_exits_non_zero(tmp_path, tmp_path_factory):
    repo = _git_repo(tmp_path)
    (repo / "leaky.txt").write_text("zephyrgate is mentioned here", encoding="utf-8")
    subprocess.run(["git", "add", "leaky.txt"], cwd=repo, check=True)
    denylist = tmp_path_factory.mktemp("denylist") / "denylist.yaml"
    denylist.write_text(VALID_TERM_DENYLIST, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CLI), str(repo)],
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "PUBLISHABILITY_TERMS_FILE": str(denylist)},
    )
    assert result.returncode != 0
    assert "[privatescan] ok" not in result.stdout
    assert "finding(s)" in result.stderr
    # The module's own contract: never print a term or a matched substring.
    assert "zephyrgate" not in result.stdout + result.stderr


def test_cli_defaults_root_to_cwd_when_no_argument_given(tmp_path, tmp_path_factory):
    repo = _git_repo(tmp_path)
    (repo / "clean.txt").write_text("nothing sensitive here", encoding="utf-8")
    denylist = tmp_path_factory.mktemp("denylist") / "denylist.yaml"
    denylist.write_text(VALID_TERM_DENYLIST, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CLI)],
        cwd=repo,
        capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "PUBLISHABILITY_TERMS_FILE": str(denylist)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[privatescan] ok" in result.stdout
