import base64
import os
import stat as stdlib_stat
import textwrap
from pathlib import Path

import pytest

import scripts.privatescan as privatescan
from scripts.privatescan import (
    DENYLIST_PLACEHOLDER,
    DenylistError,
    apply_mask,
    candidate_views,
    eligible_masks,
    find_term,
    load_denylist,
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
