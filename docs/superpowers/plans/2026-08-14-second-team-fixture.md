# Second-Team Fixture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** add a second team to the tree so the per-team model this repository is built around is exercised by real content rather than only by synthetic unit tests.

**Architecture:** a `payments` team owning `rules/payments/` and `dashboards/payments/`, with sole-owner CODEOWNERS entries for both, and assertions that the multi-team paths genuinely depend on that content.

**Tech Stack:** Helm, promtool unit tests, Grafana dashboard JSON, pytest, bash.

**Spec:** `docs/superpowers/specs/2026-08-14-publishable-example-design.md` section 4.

**Independence:** this plan does not depend on the publishability gates and may run before or after them. It must land before the content freeze.

## Global Constraints

- No em dashes anywhere: code, comments, docs, commit messages or test names.
- Documented placeholder URLs, hostnames and labels only. No real datasource uid, hostname or tenant.
- New content satisfies every existing check with no exemptions: the `rules/<team>/<target>/<service>[-<type>]-alerts.yaml` naming contract, the canonical `deployment_environment=~"..."` matcher form, a passing promtool unit test, and a dashboard uid unique across the tree.
- macOS ships bash 3.2, which has no `mapfile`.
- Run the suite as: `cd <repo> && PATH="$PWD/.venv/bin:$PATH" make check`

## File Structure

| File | Responsibility |
| --- | --- |
| `rules/payments/mimir/checkout-alerts.yaml` (create) | The second team's alert rule |
| `rules/payments/mimir/checkout-alerts-tests.yaml` (create) | Its promtool fixture, one firing and one non-firing case |
| `dashboards/payments/checkout-overview.json` (create) | The second team's dashboard |
| `.github/CODEOWNERS` (modify) | Sole-owner entries for both payments paths |
| `tests/chart_test.sh` (modify) | Name the payments ConfigMap explicitly; assert no repository-wide total |
| `tests/test_second_team.py` (create) | Mutation assertions: deleting the content breaks a test |

---

### Task 1: Add the payments rule, fixture and ownership

**Files:**
- Create: `rules/payments/mimir/checkout-alerts.yaml`, `rules/payments/mimir/checkout-alerts-tests.yaml`
- Modify: `.github/CODEOWNERS`

**Interfaces:**
- Produces: a second rendered ConfigMap named `platform-payments-mimir-checkout-alerts`, where `platform` is the tenant prefix from `values.yaml` and `payments` is the team directory.

- [ ] **Step 1: Confirm the naming contract and the canonical matcher form**

Read the existing rule to copy its shape exactly rather than guessing:

```bash
cat rules/platform/mimir/deadman-alerts.yaml
cat rules/platform/mimir/deadman-alerts-tests.yaml
```

Note the canonical environment matcher: `deployment_environment=~"dev|staging|prod"`. Any other spelling fails the `envmatcher` check.

- [ ] **Step 2: Write the rule**

Create `rules/payments/mimir/checkout-alerts.yaml`:

```yaml
groups:
  - name: checkout
    rules:
      - alert: CheckoutErrorRateHigh
        expr: |
          sum by (service) (
            rate(checkout_requests_total{deployment_environment=~"dev|staging|prod",status=~"5.."}[5m])
          )
          /
          sum by (service) (
            rate(checkout_requests_total{deployment_environment=~"dev|staging|prod"}[5m])
          )
          > 0.05
        for: 10m
        labels:
          severity: warning
          team: payments
        annotations:
          summary: "Checkout error rate above 5 percent for {{ $labels.service }}"
          description: >-
            More than 5 percent of checkout requests have failed for 10 minutes.
            Check the upstream payment provider before rolling back.
          runbook_url: "https://runbooks.internal/payments/checkout-error-rate"
```

- [ ] **Step 3: Write the promtool fixture with one firing and one non-firing case**

Create `rules/payments/mimir/checkout-alerts-tests.yaml`:

```yaml
rule_files:
  - checkout-alerts.yaml

evaluation_interval: 1m

tests:
  # Firing: 10 percent of requests fail, which is above the 5 percent threshold.
  - interval: 1m
    input_series:
      - series: 'checkout_requests_total{deployment_environment="prod",service="checkout",status="500"}'
        values: '0+1x20'
      - series: 'checkout_requests_total{deployment_environment="prod",service="checkout",status="200"}'
        values: '0+9x20'
    alert_rule_test:
      - eval_time: 15m
        alertname: CheckoutErrorRateHigh
        exp_alerts:
          - exp_labels:
              severity: warning
              team: payments
              service: checkout
            exp_annotations:
              summary: "Checkout error rate above 5 percent for checkout"
              description: >-
                More than 5 percent of checkout requests have failed for 10 minutes.
                Check the upstream payment provider before rolling back.
              runbook_url: "https://runbooks.internal/payments/checkout-error-rate"

  # Not firing: 1 percent of requests fail, which is below the threshold.
  - interval: 1m
    input_series:
      - series: 'checkout_requests_total{deployment_environment="prod",service="checkout",status="500"}'
        values: '0+1x20'
      - series: 'checkout_requests_total{deployment_environment="prod",service="checkout",status="200"}'
        values: '0+99x20'
    alert_rule_test:
      - eval_time: 15m
        alertname: CheckoutErrorRateHigh
        exp_alerts: []
```

- [ ] **Step 4: Run promtool directly and confirm both cases behave**

Run: `promtool test rules rules/payments/mimir/checkout-alerts-tests.yaml`
Expected: `SUCCESS`. If the firing case does not fire, the `for: 10m` window and `eval_time` disagree; raise `eval_time` rather than lowering `for`.

- [ ] **Step 5: Add sole-owner CODEOWNERS entries**

Add to `.github/CODEOWNERS`, after the platform entries so that last-match-wins resolves them to payments:

```
/rules/payments/ @org/payments
/dashboards/payments/ @org/payments
```

Both entries are required. A team's rules and dashboards are one ownership boundary, and the owning team must be the sole owner, because on GitHub any co-owner can approve alone.

- [ ] **Step 6: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" make check`
Expected: PASS. If `tests/chart_test.sh` fails with a ConfigMap count assertion, that is Task 2's subject; note the failure and continue to Task 2 rather than weakening the rule.

- [ ] **Step 7: Commit**

```bash
git add rules/payments .github/CODEOWNERS
git commit -m "feat: add the payments team rule, fixture and ownership

A second team in the tree, so the per-team model is exercised by real
content rather than only by synthetic CODEOWNERS fixtures."
```

---

### Task 2: Make the chart test name ConfigMaps instead of counting them

The most consequential defect found in this repository's construction was that adding a second Mimir rule always failed CI, because a test asserted the entire render contained exactly one ConfigMap. It survived every review because nobody had used the repository for its stated purpose.

**Files:**
- Modify: `tests/chart_test.sh`
- Create: `dashboards/payments/checkout-overview.json`

- [ ] **Step 1: Find any repository-wide count assertion**

Run: `rg -n 'kind: ConfigMap' tests/chart_test.sh`

Any assertion of the form `assert_count "$OUT" "kind: ConfigMap" <N>` is repository-wide and breaks the moment a team is added. Replace it with per-ConfigMap name assertions.

- [ ] **Step 2: Replace the count with explicit names**

```bash
assert_count "$OUT" "name: platform-platform-mimir-deadman-alerts" 1 \
  "platform deadman rule renders exactly one ConfigMap"
assert_count "$OUT" "name: platform-payments-mimir-checkout-alerts" 1 \
  "payments checkout rule renders exactly one ConfigMap"
```

- [ ] **Step 3: Add the payments dashboard**

Create `dashboards/payments/checkout-overview.json`. Copy the structure of `dashboards/platform/delivery-canary.json` and change the uid, title and query. The uid must be unique across the tree:

```bash
rg -n '"uid"' dashboards/ | head
```

Pick a uid not in that output, for example `payments-checkout-ov`.

- [ ] **Step 4: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" make check`
Expected: PASS, including the dashboards check with no duplicate uid.

- [ ] **Step 5: Commit**

```bash
git add tests/chart_test.sh dashboards/payments
git commit -m "test: assert ConfigMaps by name, not by repository-wide count

A total-count assertion breaks the moment a second team is added, which
is exactly the defect that made adding a second Mimir rule always fail
CI while every review passed."
```

---

### Task 3: Prove the fixture is load-bearing, not ornamental

Example content that no test depends on is decoration. These assertions fail if the content is deleted, which is what makes the fixture a regression test.

**Files:**
- Create: `tests/test_second_team.py`

- [ ] **Step 1: Write the mutation tests**

```python
"""The payments fixture exists to be depended upon.

Each test here fails if a piece of the second team is removed. Without them,
the fixture is decoration that a future refactor would delete without noticing.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_checks(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "scripts/rulecheck.py", str(root)],
        cwd=root, capture_output=True, text=True,
    )


@pytest.fixture
def repo_copy(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT, dest,
        ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__",
                                      ".superpowers"),
    )
    return dest


def test_baseline_is_clean(repo_copy):
    assert run_checks(repo_copy).returncode in (0, 3)


def test_deleting_the_payments_rule_is_detected(repo_copy):
    (repo_copy / "rules" / "payments" / "mimir" / "checkout-alerts.yaml").unlink()
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "deleting the payments rule left every check green, so the fixture is ornamental"
    )


def test_deleting_the_payments_dashboard_is_detected(repo_copy):
    shutil.rmtree(repo_copy / "dashboards" / "payments")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "deleting the payments dashboard left every check green"
    )


def test_removing_the_rules_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/rules/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3)


def test_removing_the_dashboards_ownership_entry_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    kept = [ln for ln in path.read_text().splitlines() if "/dashboards/payments/" not in ln]
    path.write_text("\n".join(kept) + "\n")
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3)


def test_giving_payments_paths_to_another_team_is_detected(repo_copy):
    path = repo_copy / ".github" / "CODEOWNERS"
    text = path.read_text() + "\n/rules/payments/ @org/platform\n"
    path.write_text(text)
    result = run_checks(repo_copy)
    assert result.returncode not in (0, 3), (
        "a co-owner on a team's own rules was accepted, and on GitHub any "
        "co-owner can approve alone"
    )
```

Note the `returncode in (0, 3)` baseline: 3 is the repository's `EXIT_UNCONFIGURED`, which the shipped placeholder ownership produces and which is not a failure.

- [ ] **Step 2: Run and confirm every mutation is detected**

Run: `PATH="$PWD/.venv/bin:$PATH" python -m pytest tests/test_second_team.py -v`
Expected: PASS. Any test that fails here means the corresponding deletion goes unnoticed, and the fixture does not yet earn its place. Fix the check, not the test.

- [ ] **Step 3: Run the full suite**

Run: `PATH="$PWD/.venv/bin:$PATH" make check`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_second_team.py
git commit -m "test: prove the payments fixture is load-bearing

Each test fails if a piece of the second team is deleted. Example content
no test depends on is decoration, and decoration is what gets removed by
a refactor without anyone noticing the model is no longer exercised."
```

---

## Self-Review

**Spec coverage.** Section 4's seven acceptance criteria map as follows: sole ownership of both paths, Task 1 Step 5 and Task 3; both source paths rendering exactly once, Task 2 Step 2; `render_assert.py` reconciliation, exercised by `make check` in Task 2 Step 4; no repository-wide ConfigMap count, Task 2 Step 2; one firing and one non-firing evaluation, Task 1 Step 3; unique dashboard uid with no private values, Task 2 Step 3; and the mutation criterion covering the rule, the dashboard and both ownership entries, Task 3.

**Placeholder scan.** The dashboard body in Task 2 Step 3 is the one place this plan says "copy the neighbouring file and change three fields" rather than printing JSON. That is deliberate: the existing dashboard is the schema of record, and transcribing a full panel JSON into a plan invites it to drift from the file it is supposed to match. The three fields to change and the uniqueness check are both explicit.

**Type consistency.** The ConfigMap name asserted in Task 2 is `platform-payments-mimir-checkout-alerts`, matching the template's `printf "%s-%s" $tenant ($key | trimSuffix ".yaml")` with `tenant: platform` from `values.yaml` and `$key` derived as `payments-mimir-checkout-alerts` from the source path.
