import sys
from pathlib import Path
import json
import subprocess

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


def expr_rule(expr: str) -> str:
    return f"""\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: {expr}
        labels: {{severity: warning, owner: payments}}
        annotations:
          summary: A summary.
          runbook_url: https://runbooks.internal/x
"""


def test_envmatcher_allows_no_matcher_at_all(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", expr_rule("up == 0"))
    assert rulecheck.check_env_matchers(tmp_path) == []


def test_envmatcher_accepts_canonical_form(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment=~"staging|prod"} == 0'))
    assert rulecheck.check_env_matchers(tmp_path) == []


def test_envmatcher_accepts_a_single_value(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment=~"prod"} == 0'))
    assert rulecheck.check_env_matchers(tmp_path) == []


def test_envmatcher_rejects_plain_equals(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment="prod"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_negation(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment!="dev"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_whitespace_around_operator(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment =~ "prod"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_unknown_environment(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment=~"perf"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("perf" in f for f in findings)


def test_envmatcher_rejects_out_of_order_values(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment=~"prod|staging"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("order" in f for f in findings)


def test_envmatcher_rejects_duplicate_values(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{deployment_environment=~"prod|prod"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("duplicate" in f for f in findings)


def test_envmatcher_rejects_inconsistent_occurrences(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", expr_rule(
        'sum(up{deployment_environment=~"prod"}) / '
        'sum(up{deployment_environment=~"staging|prod"})'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("identical" in f for f in findings)


def test_envmatcher_rejects_single_quoted_matcher(tmp_path):
    # PromQL accepts single quotes, so this is valid but non-canonical. If it
    # slipped through, the environment set would stop being derivable.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule("up{deployment_environment='prod'} == 0"))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_backtick_matcher(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule("up{deployment_environment=`prod`} == 0"))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_ignores_a_longer_label_with_the_same_suffix(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{my_deployment_environment="prod"} == 0'))
    assert rulecheck.check_env_matchers(tmp_path) == []


def test_envmatcher_ignores_matchers_in_annotations(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml", """\
groups:
  - name: g
    rules:
      - alert: PaymentsA
        expr: up == 0
        labels: {severity: warning, owner: payments}
        annotations:
          summary: 'Prose mentioning deployment_environment="prod" harmlessly.'
          runbook_url: https://runbooks.internal/x
""")
    assert rulecheck.check_env_matchers(tmp_path) == []


CODEOWNERS_HEADER = """\
* @org/platform
/Chart.yaml @org/platform
/values.yaml @org/platform
/values.schema.json @org/platform
/templates/ @org/platform
/validation.yaml @org/platform
/scripts/ @org/platform
/.github/ @org/platform
"""


def test_codeowners_accepts_matching_sets(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    write(tmp_path, "dashboards/payments/overview.json", "{}")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
    )
    assert rulecheck.check_codeowners(tmp_path) == []


def test_codeowners_flags_a_team_folder_with_no_entry(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(CODEOWNERS_HEADER)
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("payments" in f and "no CODEOWNERS" in f for f in findings)


def test_codeowners_flags_an_entry_with_no_folder(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/rules/ghost/ @org/ghost\n"
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("ghost" in f for f in findings)


def test_codeowners_requires_platform_owned_paths(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "/rules/payments/ @org/payments\n"
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("templates/" in f for f in findings)


def test_codeowners_rejects_a_governing_path_reassigned_to_another_team(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
        + "/scripts/ @org/payments\n"  # Reassign /scripts/ to another team
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("/scripts/" in f for f in findings)


def dash(uid: str, title: str = "T") -> str:
    return json.dumps({"uid": uid, "title": title, "panels": []})


def test_dashboards_accepts_valid_files(tmp_path):
    write(tmp_path, "dashboards/payments/overview.json", dash("payments-overview"))
    assert rulecheck.check_dashboards(tmp_path) == []


def test_dashboards_rejects_malformed_json(tmp_path):
    write(tmp_path, "dashboards/payments/overview.json", "{not json")
    findings = rulecheck.check_dashboards(tmp_path)
    assert any("JSON" in f for f in findings)


def test_dashboards_requires_a_uid(tmp_path):
    write(tmp_path, "dashboards/payments/overview.json", json.dumps({"title": "T"}))
    findings = rulecheck.check_dashboards(tmp_path)
    assert any("uid" in f for f in findings)


def test_dashboards_rejects_duplicate_uids(tmp_path):
    write(tmp_path, "dashboards/payments/a.json", dash("same-uid"))
    write(tmp_path, "dashboards/fraud/b.json", dash("same-uid"))
    findings = rulecheck.check_dashboards(tmp_path)
    assert any("same-uid" in f and "unique" in f for f in findings)


def test_dashboards_rejects_bad_filename(tmp_path):
    write(tmp_path, "dashboards/payments/Overview_Panel.json", dash("x"))
    findings = rulecheck.check_dashboards(tmp_path)
    assert any("filename" in f.lower() for f in findings)


def test_dashboards_detects_a_changed_uid_against_the_base_ref(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    write(tmp_path, "dashboards/payments/overview.json", dash("original-uid"))
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    write(tmp_path, "dashboards/payments/overview.json", dash("changed-uid"))
    findings = rulecheck.check_dashboards(tmp_path, base_ref="HEAD")
    assert any("original-uid" in f for f in findings)
