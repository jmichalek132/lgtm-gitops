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


ALERT = """\
groups:
  - name: g
    rules:
      - alert: {name}
        expr: vector(1)
        labels:
          severity: {severity}
          owner: {owner}
        annotations:
          summary: A summary.
          runbook_url: https://runbooks.internal/x
"""


def test_contract_accepts_a_valid_alert(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          ALERT.format(name="PaymentsA", severity="warning", owner="payments"))
    assert rulecheck.check_contract(tmp_path) == []


def test_contract_rejects_owner_not_matching_folder(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          ALERT.format(name="PaymentsA", severity="warning", owner="platform"))
    findings = rulecheck.check_contract(tmp_path)
    assert any("owner" in f for f in findings)


def test_contract_rejects_unknown_severity(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          ALERT.format(name="PaymentsA", severity="page", owner="payments"))
    findings = rulecheck.check_contract(tmp_path)
    assert any("severity" in f for f in findings)


def test_contract_requires_a_summary(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: vector(1)
        labels: {severity: warning, owner: payments}
        annotations: {runbook_url: https://runbooks.internal/x}
""")
    findings = rulecheck.check_contract(tmp_path)
    assert any("summary" in f for f in findings)


def test_contract_accepts_description_as_a_summary_alias(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: vector(1)
        labels: {severity: warning, owner: payments}
        annotations:
          description: Explained here instead.
          runbook_url: https://runbooks.internal/x
""")
    assert rulecheck.check_contract(tmp_path) == []


def test_contract_requires_a_url_annotation(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: vector(1)
        labels: {severity: warning, owner: payments}
        annotations: {summary: A summary.}
""")
    findings = rulecheck.check_contract(tmp_path)
    assert any("runbook_url" in f for f in findings)


def test_contract_rejects_duplicate_alert_names_across_teams(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          ALERT.format(name="SharedName", severity="warning", owner="payments"))
    write(tmp_path, "rules/fraud/mimir/b-alerts.yaml",
          ALERT.format(name="SharedName", severity="warning", owner="fraud"))
    findings = rulecheck.check_contract(tmp_path)
    assert any("SharedName" in f and "unique" in f for f in findings)


def test_contract_rejects_duplicate_alert_names_within_one_file(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: SameName
        expr: vector(1)
        labels: {severity: warning, owner: payments}
        annotations: {summary: One., runbook_url: https://runbooks.internal/x}
      - alert: SameName
        expr: vector(2)
        labels: {severity: warning, owner: payments}
        annotations: {summary: Two., runbook_url: https://runbooks.internal/x}
""")
    findings = rulecheck.check_contract(tmp_path)
    assert any("SameName" in f and "unique" in f for f in findings)


def test_contract_survives_malformed_labels(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: vector(1)
        labels: "not-a-mapping"
        annotations: {summary: S., runbook_url: https://runbooks.internal/x}
""")
    findings = rulecheck.check_contract(tmp_path)  # must not raise
    assert any("severity" in f for f in findings)


def test_contract_ignores_recording_rules(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-rules.yaml", """\
groups:
  - name: g
    rules:
      - record: job:x:sum
        expr: sum(x)
""")
    assert rulecheck.check_contract(tmp_path) == []


def test_contract_skips_test_fixtures(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files: [a-alerts.yaml]\ntests: []\n")
    assert rulecheck.check_contract(tmp_path) == []
