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


FIXTURE = """\
rule_files:
  - a-alerts.yaml

tests:
  - interval: 1m
    alert_rule_test:
      - eval_time: 5m
        alertname: PaymentsA
        exp_alerts: []
"""


def test_fixtures_accepts_a_fixture_that_tests_something(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml", FIXTURE)
    assert rulecheck.check_fixtures(tmp_path) == []


def test_fixtures_rejects_an_empty_tests_list(tmp_path):
    # `promtool test rules` prints SUCCESS and exits 0 for `tests: []`, so
    # executing a fixture proves nothing about whether it still tests anything.
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files:\n  - a-alerts.yaml\n\ntests: []\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("tests" in f for f in findings)


def test_fixtures_rejects_a_missing_tests_key(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files:\n  - a-alerts.yaml\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("tests" in f for f in findings)


def test_fixtures_rejects_a_null_tests_key(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files:\n  - a-alerts.yaml\ntests:\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("tests" in f for f in findings)


def test_fixtures_rejects_a_tests_key_that_is_not_a_list(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files:\n  - a-alerts.yaml\ntests: yes\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("tests" in f for f in findings)


def test_fixtures_requires_rule_files(tmp_path):
    # A fixture naming no rule file cannot be exercising any rule in this repo.
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml",
          "rule_files: []\n\ntests:\n  - interval: 1m\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("rule_files" in f for f in findings)


def test_fixtures_rejects_a_rule_files_entry_that_does_not_exist(tmp_path):
    # The rule file the fixture claims to exercise was deleted; the fixture
    # itself is still well-formed (non-empty 'tests', non-empty 'rule_files'),
    # so this is the only thing left that can catch the orphan.
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml", FIXTURE)
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("a-alerts.yaml" in f and "does not exist" in f for f in findings)


def test_fixtures_resolves_rule_files_relative_to_the_fixture_directory(tmp_path):
    # promtool resolves rule_files relative to the fixture's own directory
    # (scripts/check.sh: `cd "$(dirname "$f")"`), not the repository root, so
    # a same-named file elsewhere in the tree must not satisfy the check.
    write(tmp_path, "rules/platform/mimir/a-alerts.yaml")
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml", FIXTURE)
    findings = rulecheck.check_fixtures(tmp_path)
    assert any("a-alerts.yaml" in f and "does not exist" in f for f in findings)


def test_fixtures_accepts_a_rule_files_entry_that_exists(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml", FIXTURE)
    assert rulecheck.check_fixtures(tmp_path) == []


def test_fixtures_reports_unparseable_yaml(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts-tests.yaml", "tests: [\n")
    findings = rulecheck.check_fixtures(tmp_path)
    assert findings


def test_fixtures_ignores_non_fixture_files(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    assert rulecheck.check_fixtures(tmp_path) == []


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


def test_envmatcher_rejects_a_double_quoted_label_name(tmp_path):
    # Prometheus 3 accepts a quoted label name inside braces, verified with
    # promtool. Without this the matcher contract is bypassable in a form that
    # ships green through every syntax check.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{"deployment_environment"="prod"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_a_single_quoted_label_name(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule("up{'deployment_environment'='prod'} == 0"))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_a_backtick_quoted_label_name(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule("up{`deployment_environment`=\"prod\"} == 0"))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_rejects_a_quoted_label_name_with_the_canonical_operator(tmp_path):
    # The values are canonical; only the label-name quoting differs. That still
    # has to fail, or the selector stops being derivable by a simple parse.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{"deployment_environment"=~"staging|prod"} == 0'))
    findings = rulecheck.check_env_matchers(tmp_path)
    assert any("canonical" in f for f in findings)


def test_envmatcher_ignores_a_quoted_longer_label_with_the_same_suffix(tmp_path):
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml",
          expr_rule('up{"my_deployment_environment"="prod"} == 0'))
    assert rulecheck.check_env_matchers(tmp_path) == []


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


def test_codeowners_flags_a_dashboards_folder_missing_while_rules_remains(tmp_path):
    # The team's dashboards folder was deleted (or never created) while its
    # rules folder is real, and CODEOWNERS still names both. The per-path
    # probe loop further down stays silent on this: with no real files under
    # dashboards/payments/, its only evidence is a synthetic probe plus
    # CODEOWNERS's own entry, which "resolves" that probe to exactly the team
    # the entry names, so nothing looks wrong from that angle. Comparing real
    # folders against real entries, per parent, is what catches a directory
    # that used to have content and no longer does.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(
        "payments" in f and "dashboards/payments" in f and "no" in f.lower()
        for f in findings
    )


def test_codeowners_flags_a_rules_folder_missing_while_dashboards_remains(tmp_path):
    # Mirror of the case above: the rules folder is gone, the dashboards
    # folder is real, CODEOWNERS still names both.
    write(tmp_path, "dashboards/payments/overview.json", "{}")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(
        "payments" in f and "rules/payments" in f and "no" in f.lower()
        for f in findings
    )


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


def test_codeowners_rejects_a_governing_directory_stolen_without_trailing_slash(tmp_path):
    # A pattern with no trailing slash still claims everything beneath it on
    # GitHub (hmarr/codeowners match.go:169-172: "As there's no trailing slash
    # ... we need to match descendent paths"), so omitting the slash must not
    # be a way to bypass the governed-path check.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
        + "/scripts @org/payments\n"  # No trailing slash, still steals the directory
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("/scripts" in f for f in findings)


def codeowners(tmp_path, body: str) -> None:
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(body)


def governed_repo(tmp_path) -> None:
    """A minimal repo holding one real file under each governed directory."""
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    write(tmp_path, "dashboards/payments/overview.json", "{}")
    write(tmp_path, "scripts/rulecheck.py", "# helper\n")
    write(tmp_path, "templates/configmaps.yaml", "# template\n")
    write(tmp_path, "tests/test_rulecheck.py", "# tests\n")
    write(tmp_path, "tools/checksums.txt", "abc  file\n")
    write(tmp_path, "Chart.yaml", "name: x\n")
    write(tmp_path, "Makefile", "check:\n")
    write(tmp_path, "requirements.txt", "PyYAML\n")


TEAM_ENTRIES = "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"


def test_codeowners_accepts_a_repo_where_the_default_owner_covers_governed_paths(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES)
    assert rulecheck.check_codeowners(tmp_path) == []


def test_codeowners_rejects_a_more_specific_file_pattern_under_a_governed_dir(tmp_path):
    # The exact bypass the final review reproduced: /scripts/ is platform-owned,
    # but a later, more specific pattern hands one file to another team and
    # GitHub resolves it last-match-wins.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/scripts/rulecheck.py @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/rulecheck.py" in f and "@org/payments" in f for f in findings)


def test_codeowners_rejects_a_more_specific_pattern_under_templates(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/templates/configmaps.yaml @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("templates/configmaps.yaml" in f for f in findings)


def test_codeowners_rejects_a_more_specific_pattern_under_dot_github(tmp_path):
    governed_repo(tmp_path)
    write(tmp_path, ".github/workflows/ci.yaml", "name: ci\n")
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/.github/workflows/ci.yaml @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(".github/workflows/ci.yaml" in f for f in findings)


def test_codeowners_rejects_a_wildcard_pattern_under_a_governed_dir(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "/scripts/* @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/rulecheck.py" in f for f in findings)


def test_codeowners_rejects_a_doublestar_pattern_under_a_governed_dir(tmp_path):
    governed_repo(tmp_path)
    write(tmp_path, "scripts/sub/helper.py", "# helper\n")
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "/scripts/** @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/sub/helper.py" in f for f in findings)


def test_codeowners_reclaiming_a_governed_dir_after_a_narrower_pattern_is_accepted(tmp_path):
    # Last match wins, so a trailing platform entry legitimately takes the file
    # back. Flagging this would be a false positive.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/scripts/rulecheck.py @org/payments\n/scripts/ @org/platform\n")
    assert rulecheck.check_codeowners(tmp_path) == []


def test_codeowners_single_star_does_not_cross_a_slash(tmp_path):
    # /scripts/*.py must not claim scripts/sub/helper.py, so the platform-owned
    # /scripts/ entry before it still governs that file.
    governed_repo(tmp_path)
    write(tmp_path, "scripts/sub/helper.py", "# helper\n")
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "/scripts/*.py @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/rulecheck.py" in f for f in findings)
    assert not any("scripts/sub/helper.py" in f for f in findings)


def test_codeowners_catches_a_pattern_for_a_file_that_does_not_exist_yet(tmp_path):
    # The pattern itself is the evidence: a path need not exist today for the
    # entry to take effect the moment someone adds it.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/scripts/not-created-yet.py @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/not-created-yet.py" in f for f in findings)


def test_codeowners_rejects_a_pattern_it_cannot_evaluate(tmp_path):
    # A check that cannot evaluate a pattern must say so, never pass it.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "scripts/ @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("cannot be evaluated" in f and "scripts/" in f for f in findings)


def test_codeowners_rejects_a_bracket_expression_pattern(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "/scripts/[abc].py @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("cannot be evaluated" in f for f in findings)


import pytest


@pytest.mark.parametrize(
    "entry,probe",
    [
        ("/Makefile", "Makefile"),
        ("/requirements.txt", "requirements.txt"),
        ("/tests/", "tests/test_rulecheck.py"),
        ("/tools/", "tools/checksums.txt"),
    ],
)
def test_codeowners_protects_every_check_governing_path(tmp_path, entry, probe):
    # CI's only step is `make check`, so the Makefile is CI; tools/checksums.txt
    # gates the binary supply chain; tests/ defines what passing means.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + f"{entry} @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(probe in f for f in findings), f"{entry} is not protected"


def test_platform_owned_paths_covers_the_check_governing_surface(tmp_path):
    for entry in ("/Makefile", "/requirements.txt", "/tests/", "/tools/"):
        assert entry in rulecheck.PLATFORM_OWNED_PATHS


def test_codeowners_rejects_an_owner_less_governed_path(tmp_path):
    # A pattern with no owners at all un-assigns the path on GitHub.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES + "/scripts/\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/" in f for f in findings)


def test_codeowners_rejects_a_team_folder_owned_by_another_team(tmp_path):
    # A team's rules are its own ownership boundary. Handing them to a different
    # team lets that team approve alerting changes for a service it does not run.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER
               + "/rules/payments/ @org/fraud\n/dashboards/payments/ @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("rules/payments" in f and "@org/fraud" in f for f in findings)


def test_codeowners_rejects_a_dashboards_folder_owned_by_another_team(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER
               + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/fraud\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("dashboards/payments" in f and "@org/fraud" in f for f in findings)


def test_codeowners_rejects_a_rules_entry_with_no_dashboards_entry(tmp_path):
    # Rules and dashboards are ONE boundary. Owning only the rules leaves the
    # dashboards falling through to the default owner.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + "/rules/payments/ @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(
        "dashboards/payments/overview.json" in f
        and "@org/platform" in f
        and "must resolve to @org/payments" in f
        for f in findings
    )


def test_codeowners_rejects_a_dashboards_entry_with_no_rules_entry(tmp_path):
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + "/dashboards/payments/ @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any(
        "rules/payments/mimir/a-alerts.yaml" in f
        and "@org/platform" in f
        and "must resolve to @org/payments" in f
        for f in findings
    )


def test_codeowners_rejects_a_narrower_pattern_stealing_one_team_file(tmp_path):
    # Last match wins here too: a later, more specific line takes a single rule
    # file out of the owning team's hands.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/rules/payments/mimir/a-alerts.yaml @org/fraud\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("rules/payments/mimir/a-alerts.yaml" in f and "@org/fraud" in f
               for f in findings)


def test_codeowners_rejects_a_team_folder_with_an_extra_co_owner(tmp_path):
    # Any co-owner can approve alone on GitHub, so a co-owner on a team folder
    # is the same bypass as a co-owner on a governing path.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER
               + "/rules/payments/ @org/payments @org/fraud\n"
               + "/dashboards/payments/ @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("rules/payments" in f and "@org/fraud" in f for f in findings)


def test_codeowners_rejects_a_team_pattern_for_a_file_that_does_not_exist_yet(tmp_path):
    # The line is the evidence: it mis-owns the file the moment it is created.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/rules/payments/mimir/not-created-yet.yaml @org/fraud\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("not-created-yet.yaml" in f for f in findings)


def test_codeowners_accepts_a_team_reclaiming_its_folder_after_a_narrower_pattern(tmp_path):
    # Last match wins, so a trailing team entry legitimately takes the file back.
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER
               + "/rules/payments/mimir/a-alerts.yaml @org/fraud\n" + TEAM_ENTRIES)
    assert rulecheck.check_codeowners(tmp_path) == []


def test_codeowners_rejects_a_governing_path_with_an_extra_co_owner(tmp_path):
    # On GitHub either co-owner can approve alone, so a governing path is only
    # actually protected when the platform team is the SOLE owner.
    write(tmp_path, "rules/payments/mimir/a-alerts.yaml")
    (tmp_path / ".github").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        CODEOWNERS_HEADER
        + "/rules/payments/ @org/payments\n/dashboards/payments/ @org/payments\n"
        + "/scripts/ @org/platform @org/payments\n"  # Co-owner can approve alone
    )
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("scripts/" in f and "@org/payments" in f for f in findings)


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


def test_dashboards_rejects_non_object_json(tmp_path):
    write(tmp_path, "dashboards/payments/overview.json", "[]")
    findings = rulecheck.check_dashboards(tmp_path)
    assert any("must be an object" in f for f in findings)


def test_dashboards_reports_an_unresolvable_base_ref(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    write(tmp_path, "dashboards/payments/overview.json", dash("some-uid"))
    findings = rulecheck.check_dashboards(tmp_path, base_ref="nonexistent-ref")
    assert any("could not be resolved" in f for f in findings)


REPO_ROOT = Path(__file__).resolve().parents[1]


def ownership(tmp_path, body: str) -> None:
    (tmp_path / rulecheck.OWNERSHIP_FILE).write_text(body)


CONFIGURED = 'configured: true\norg: "@acme"\n'
UNCONFIGURED = 'configured: false\norg: "@org"\n'


def test_ownership_accepts_a_configured_org(tmp_path):
    ownership(tmp_path, CONFIGURED)
    assert rulecheck.check_ownership(tmp_path) == []
    assert rulecheck.ownership_warnings(tmp_path) == []


def test_ownership_rejects_configured_true_with_the_shipped_placeholder(tmp_path):
    # The whole point: '@org/platform' does not exist on GitHub, GitHub silently
    # ignores unknown owners, so claiming to be configured while still naming the
    # placeholder means nobody reviews anything.
    ownership(tmp_path, 'configured: true\norg: "@org"\n')
    findings = rulecheck.check_ownership(tmp_path)
    assert any("placeholder" in f for f in findings)


def test_ownership_warns_but_does_not_fail_when_unconfigured(tmp_path):
    # An example repository openly declaring itself unconfigured is honest; it
    # must still say loudly that it governs nothing.
    ownership(tmp_path, UNCONFIGURED)
    assert rulecheck.check_ownership(tmp_path) == []
    warnings = rulecheck.ownership_warnings(tmp_path)
    assert any("UNCONFIGURED" in w for w in warnings)


def test_ownership_warning_calls_the_shipped_org_a_placeholder(tmp_path):
    # configured: false with the shipped '@org' placeholder: the placeholder
    # language is true here, so the warning may say the org does not exist.
    ownership(tmp_path, UNCONFIGURED)
    warnings = rulecheck.ownership_warnings(tmp_path)
    assert any("placeholder organisation that does" in w for w in warnings)
    assert not any("not yet declared configured" in w for w in warnings)


def test_ownership_warning_does_not_call_a_real_org_a_placeholder(tmp_path):
    # configured: false but 'org' already points at a real-looking organisation:
    # the adopter has migrated their handles but not yet flipped the flag. The
    # warning must not claim '@acme' is fake, only that it is not yet enforced.
    ownership(tmp_path, 'configured: false\norg: "@acme"\n')
    warnings = rulecheck.ownership_warnings(tmp_path)
    assert any("not yet declared configured" in w for w in warnings)
    assert not any("is a placeholder organisation that does" in w for w in warnings)
    assert not any(
        "'@acme/...' is a placeholder organisation" in w for w in warnings
    )


def test_ownership_rejects_a_missing_file(tmp_path):
    findings = rulecheck.check_ownership(tmp_path)
    assert any(rulecheck.OWNERSHIP_FILE in f for f in findings)


def test_ownership_rejects_a_non_boolean_configured(tmp_path):
    # 'configured: maybe' must not be read as truthy or as false; either way the
    # repository's own declaration is unreadable.
    ownership(tmp_path, 'configured: maybe\norg: "@acme"\n')
    findings = rulecheck.check_ownership(tmp_path)
    assert any("configured" in f for f in findings)


def test_ownership_rejects_an_org_without_an_at_sign(tmp_path):
    ownership(tmp_path, 'configured: true\norg: "acme"\n')
    findings = rulecheck.check_ownership(tmp_path)
    assert any("org" in f for f in findings)


def test_ownership_rejects_an_org_containing_a_team(tmp_path):
    # 'org' names the organisation only; the team is appended per folder.
    ownership(tmp_path, 'configured: true\norg: "@acme/platform"\n')
    findings = rulecheck.check_ownership(tmp_path)
    assert any("org" in f for f in findings)


def test_ownership_rejects_unparseable_yaml(tmp_path):
    ownership(tmp_path, "configured: [\n")
    assert rulecheck.check_ownership(tmp_path) != []


def test_ownership_rejects_placeholder_handles_left_in_codeowners(tmp_path):
    # Half-migrated is the dangerous state: configured says yes, but the file
    # GitHub actually reads still names the organisation that does not exist.
    ownership(tmp_path, CONFIGURED)
    codeowners(tmp_path, "* @acme/platform\n/scripts/ @org/platform\n")
    findings = rulecheck.check_ownership(tmp_path)
    assert any("@org/platform" in f for f in findings)


def test_codeowners_enforces_the_configured_org(tmp_path):
    # Once configured, '@org/payments' is just another wrong owner.
    governed_repo(tmp_path)
    ownership(tmp_path, CONFIGURED)
    codeowners(tmp_path, CODEOWNERS_HEADER.replace("@org/", "@acme/")
               + "/rules/payments/ @org/payments\n/dashboards/payments/ @acme/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("rules/payments" in f and "@acme/payments" in f for f in findings)


def test_codeowners_accepts_a_fully_configured_org(tmp_path):
    governed_repo(tmp_path)
    ownership(tmp_path, CONFIGURED)
    codeowners(tmp_path, CODEOWNERS_HEADER.replace("@org/", "@acme/")
               + "/rules/payments/ @acme/payments\n/dashboards/payments/ @acme/payments\n")
    assert rulecheck.check_codeowners(tmp_path) == []


def test_ownership_file_is_itself_platform_owned(tmp_path):
    # It decides which handle every other ownership check enforces, so a team
    # that could edit it could redirect ownership of everything.
    assert f"/{rulecheck.OWNERSHIP_FILE}" in rulecheck.PLATFORM_OWNED_PATHS


def test_shipped_repository_has_no_ownership_failures():
    assert rulecheck.check_ownership(REPO_ROOT) == []


def test_publishability_config_is_platform_owned():
    # It configures Gate 1, so a team that could edit it could disable or
    # narrow the patterns that keep personal paths out of a published repo.
    assert f"/{rulecheck.PUBLISHABILITY_FILE}" in rulecheck.PLATFORM_OWNED_PATHS


def test_publishability_is_registered():
    assert "publishability" in rulecheck.CHECKS


def test_codeowners_gives_publishability_to_platform(tmp_path):
    """A team must not be able to take ownership of the gate that governs it."""
    governed_repo(tmp_path)
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/publishability.yaml @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("publishability" in f for f in findings)


def test_codeowners_publishability_resists_a_wildcard_override(tmp_path):
    governed_repo(tmp_path)
    (tmp_path / "publishability.yaml").write_text("version: 1\npatterns: []\n")
    codeowners(tmp_path, CODEOWNERS_HEADER + TEAM_ENTRIES
               + "/publishability.yaml @org/platform\n/publish*.yaml @org/payments\n")
    findings = rulecheck.check_codeowners(tmp_path)
    assert any("publishability" in f and "@org/payments" in f for f in findings)
