import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_assert


def test_source_findings_accepts_an_exact_match():
    files = {"rules/payments/mimir/a-alerts.yaml"}
    assert render_assert.source_findings(files, files) == []


def test_source_findings_reports_a_file_that_never_rendered():
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/a-alerts.yaml", "rules/payments/loki/b-alerts.yaml"},
        rendered={"rules/payments/mimir/a-alerts.yaml"},
    )
    assert len(findings) == 1
    assert "rules/payments/loki/b-alerts.yaml" in findings[0]
    assert "absent from every rendered ConfigMap" in findings[0]


def test_source_findings_reports_a_rendered_source_that_is_not_in_the_repository():
    # A symlinked directory under rules/ renders a ConfigMap whose source lies
    # outside the repository. check_layout rejects the symlink, but stage 6 must
    # not depend on another stage to notice that it rendered something it cannot
    # account for.
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/a-alerts.yaml"},
        rendered={"rules/payments/mimir/a-alerts.yaml", "rules/payments/mimir/linked/x-alerts.yaml"},
    )
    assert len(findings) == 1
    assert "rules/payments/mimir/linked/x-alerts.yaml" in findings[0]
    assert "not a deployable rule file in the repository" in findings[0]


def test_source_findings_reports_both_directions_at_once():
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/missing-alerts.yaml"},
        rendered={"rules/payments/mimir/extra-alerts.yaml"},
    )
    assert len(findings) == 2
    joined = "\n".join(findings)
    assert "missing-alerts.yaml" in joined
    assert "extra-alerts.yaml" in joined


def test_source_findings_does_not_expect_test_fixtures_to_render(tmp_path):
    # deployable_sources is the definition of "expected", so the exclusion of
    # -tests.yaml lives in one place rather than being restated per caller.
    for rel in ("rules/p/mimir/a-alerts.yaml", "rules/p/mimir/a-alerts-tests.yaml"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("groups: []\n")
    assert render_assert.deployable_sources(tmp_path) == {"rules/p/mimir/a-alerts.yaml"}
