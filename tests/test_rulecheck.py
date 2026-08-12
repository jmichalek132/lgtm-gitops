import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rulecheck


def write(root: Path, rel: str, body: str = "groups: []\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_layout_accepts_a_valid_tree(tmp_path):
    write(tmp_path, "rules/payments/mimir/checkout-alerts.yaml")
    write(tmp_path, "rules/payments/mimir/checkout/latency-alerts.yaml")
    write(tmp_path, "rules/platform/prometheus/meta-alerts.yaml")
    assert rulecheck.check_layout(tmp_path) == []


def test_layout_rejects_unknown_target(tmp_path):
    write(tmp_path, "rules/payments/metrics/checkout-alerts.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("metrics" in f for f in findings)


def test_layout_rejects_bad_filename(tmp_path):
    write(tmp_path, "rules/payments/mimir/Checkout_Alerts.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("filename" in f.lower() for f in findings)


def test_layout_rejects_bad_team_segment(tmp_path):
    write(tmp_path, "rules/Payments/mimir/checkout-alerts.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("Payments" in f for f in findings)


def test_layout_rejects_prometheus_outside_platform(tmp_path):
    write(tmp_path, "rules/payments/prometheus/meta-alerts.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("prometheus" in f for f in findings)


def test_layout_rejects_test_fixture_under_loki(tmp_path):
    write(tmp_path, "rules/payments/loki/errors-alerts-tests.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("tests" in f for f in findings)


def test_layout_rejects_generated_name_over_253_bytes(tmp_path):
    deep = "a" * 60
    write(tmp_path, f"rules/payments/mimir/{deep}/{deep}/{deep}/{deep}-alerts.yaml")
    findings = rulecheck.check_layout(tmp_path)
    assert any("253" in f for f in findings)


def test_layout_rejects_yml_extension(tmp_path):
    write(tmp_path, "rules/payments/mimir/checkout-alerts.yml")
    findings = rulecheck.check_layout(tmp_path)
    assert any(".yaml" in f for f in findings)


def test_layout_rejects_a_symlink(tmp_path):
    real = write(tmp_path, "rules/payments/mimir/real-alerts.yaml")
    link = tmp_path / "rules" / "payments" / "mimir" / "link-alerts.yaml"
    link.symlink_to(real)
    findings = rulecheck.check_layout(tmp_path)
    assert any("symlink" in f.lower() for f in findings)
