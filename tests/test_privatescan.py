import base64

import pytest

from scripts.privatescan import (
    DenylistError,
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


def test_candidate_views_yields_only_source_percent_and_base64():
    """Escape-view decoding (\\uXXXX, surrogate pairs, \\UXXXXXXXX, \\xXX) is
    Task 7, not this task. This module must not yield an 'escape' view yet."""
    names = {name for name, _ in candidate_views("plain text")}
    assert names <= {"source", "percent", "base64-standard", "base64-urlsafe"}
    assert "escape" not in names
