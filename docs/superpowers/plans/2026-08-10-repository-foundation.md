# Observability Rules: Repository Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the observability-rules repository itself: the Helm chart that turns rule files into ConfigMaps, the validation that keeps self-service contributors honest, and the CI that runs it, all verifiable on a laptop with no cluster.

**Architecture:** A Helm chart rooted at the repository, globbing `rules/<team>/<target>/**.yaml` into one labelled ConfigMap per rule file. A Python helper enforces structure, the label contract and the canonical environment matcher. Query-language syntax is delegated to `promtool` and `lokitool`, which are version-aligned with the deployed backends. One `scripts/check.sh` entrypoint runs identically locally and in GitHub Actions.

**Tech Stack:** Helm v3/v4, Python 3.11+ with PyYAML and pytest, promruval, promtool, lokitool, GitHub Actions, bash.

**Source spec:** `docs/superpowers/specs/2026-08-10-observability-rules-design.md`

## Scope

This plan covers Section 15's workstream 2 only: the repository, its chart, and its static CI. It deliberately excludes work that needs a cluster or another system, each of which gets its own plan:

| Follow-on plan | Covers | Blocked on |
| --- | --- | --- |
| Backend ruler wiring | `ruler_storage`, shared volumes, sidecars, reload alerts | cluster access |
| ArgoCD Applications | three Applications, end-to-end acceptance test | this plan + backend wiring |
| Grafana Git Sync | Repository resource, sync-failure alert | Grafana admin |
| Mixins | jsonnet toolchain, per-mixin subfolders, render, CI stage 7 | this plan |
| Phase 2 live validation | promruval against Mimir, constrained CI boundary | network path decision |

Everything in this plan is verifiable with `make check` on a laptop.

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include these.

- Rule filenames must match `^[a-z0-9-]+\.yaml$`. Dashboard filenames must match `^[a-z0-9-]+\.json$`.
- Every team and grouping directory segment must match `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` and be at most 63 bytes.
- Generated ConfigMap names and data keys must each be at most 253 bytes; the template fails rather than truncating.
- Targets are exactly `mimir`, `loki`, `prometheus`. Rules live at `rules/<team>/<target>/**.yaml`.
- `rules/<team>/prometheus/` is permitted only under the platform team folder, which is `platform`.
- Label `severity` is one of `info`, `warning`, `error`, `critical`.
- Label `owner` equals the containing team folder.
- Annotation `summary` is required; `message` and `description` are accepted aliases.
- At least one of annotation `runbook_url` or `dashboard_url` is required.
- Alert names are unique across the entire repository.
- The ordered environment list is `dev`, `staging`, `prod`. An expression with no `deployment_environment` matcher applies to all three.
- The only permitted environment matcher form is `deployment_environment=~"staging|prod"`: `=~`, double quotes, no whitespace around the operator, a non-empty subset of the ordered list in list order without duplicates, no negation, no plain `=`. Every occurrence within one expression must be byte-identical.
- Files ending `-tests.yaml` are unit-test fixtures: excluded from ConfigMaps, executed by `promtool test rules`, and permitted only under `mimir` and `prometheus` targets.
- The Helm chart never renders an empty manifest unless `allowEmpty=true` is passed explicitly.

## Design note: why Python and not a PromQL AST

The spec's Section 11 stage 1 says to enforce the environment matcher "via the PromQL/LogQL AST, not by grepping YAML". This plan parses the YAML properly and applies a strict regex to the extracted `expr` strings, rather than embedding a PromQL or LogQL parser.

The hazard being avoided is a matcher hiding in a comment, an annotation, or a summary string, and parsing YAML and looking only at `expr` fields eliminates exactly that. Actual query-language validity is delegated to `promtool check rules` and `lokitool rules check`, which are the version-aligned parsers and already run as stage 3. Embedding a real AST would mean a Go helper depending on `github.com/prometheus/prometheus` and `github.com/grafana/loki/v3`, whose module graph is disproportionate to one lint rule.

The residual weakness is a matcher inside a PromQL comment within an `expr`, which would be flagged even though it is inert. That is a false positive, not a false negative, and it fails safe. A reviewer who disagrees should say so before Task 6.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `Chart.yaml` | chart identity |
| `values.yaml` | `target`, `tenant`, `allowEmpty` |
| `values.schema.json` | rejects bad values before rendering |
| `templates/configmaps.yaml` | the entire chart: glob, guard, emit ConfigMaps |
| `rules/platform/{mimir,loki,prometheus}/deadman-alerts.yaml` | delivery canaries, one per target |
| `dashboards/platform/` | Git Sync watches this tree |
| `scripts/rulecheck.py` | layout, contract, env matcher, CODEOWNERS, dashboards |
| `scripts/check.sh` | orchestrator, seven stages |
| `tests/lib.sh` | shell assertion helpers |
| `tests/chart_test.sh` | chart rendering behaviour |
| `tests/test_rulecheck.py` | pytest suite for the helper |
| `validation.yaml` | promruval contract |
| `Makefile` | `make check`, `make test` |
| `.github/CODEOWNERS` | per-team and platform-owned paths |
| `.github/workflows/ci.yaml` | runs `make check` |
| `requirements.txt` | pinned PyYAML and pytest |
| `README.md` | contributor guide |
| `docs/branch-protection.md` | required settings, since CODEOWNERS alone is only routing |

---

## Task 1: Chart scaffold and first render

**Files:**
- Create: `Chart.yaml`, `values.yaml`, `values.schema.json`, `templates/configmaps.yaml`
- Create: `rules/platform/mimir/deadman-alerts.yaml`
- Create: `Makefile`, `.gitignore`, `requirements.txt`
- Test: `tests/lib.sh`, `tests/chart_test.sh`

**Interfaces:**
- Consumes: nothing
- Produces: a chart that renders `rules/<team>/<target>/**.yaml` into ConfigMaps named `<tenant>-<team>-<target>-<flattened-path>`, with data key `<team>-<target>-<flattened-path>.yaml`. Later tasks assert against these names.

- [ ] **Step 1: Write the failing test**

Create `tests/lib.sh`:

```bash
#!/usr/bin/env bash
# Minimal assertion helpers. No test framework dependency on purpose:
# the whole repo needs to be runnable with helm, python and bash alone.

FAILURES=0
PASSES=0

pass() { PASSES=$((PASSES + 1)); printf '  ok   %s\n' "$1"; }

fail() {
  FAILURES=$((FAILURES + 1))
  printf '  FAIL %s\n' "$1"
  [ -n "${2:-}" ] && printf '       %s\n' "$2"
}

assert_contains() {
  local haystack="$1" needle="$2" name="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    pass "$name"
  else
    fail "$name" "expected to find: $needle"
  fi
}

assert_not_contains() {
  local haystack="$1" needle="$2" name="$3"
  if printf '%s' "$haystack" | grep -qF -- "$needle"; then
    fail "$name" "expected NOT to find: $needle"
  else
    pass "$name"
  fi
}

assert_count() {
  local haystack="$1" needle="$2" expected="$3" name="$4"
  local actual
  actual=$(printf '%s' "$haystack" | grep -cF -- "$needle" || true)
  if [ "$actual" = "$expected" ]; then
    pass "$name"
  else
    fail "$name" "expected $expected occurrences of '$needle', got $actual"
  fi
}

# Asserts the command fails AND its stderr mentions the given text.
assert_fails_with() {
  local expected="$1" name="$2"; shift 2
  local output status
  output=$("$@" 2>&1) && status=0 || status=$?
  if [ "$status" -eq 0 ]; then
    fail "$name" "expected failure, but command succeeded"
  elif printf '%s' "$output" | grep -qF -- "$expected"; then
    pass "$name"
  else
    fail "$name" "failed as expected but message lacked: $expected"
  fi
}

summary() {
  printf '\n%s passed, %s failed\n' "$PASSES" "$FAILURES"
  [ "$FAILURES" -eq 0 ]
}
```

Create `tests/chart_test.sh`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
. tests/lib.sh

echo "chart: happy path"

OUT=$(helm template t . --set target=mimir --set tenant=platform)

assert_contains "$OUT" "name: platform-platform-mimir-deadman-alerts" \
  "ConfigMap name is <tenant>-<team>-<target>-<file>"
assert_contains "$OUT" "platform-mimir-deadman-alerts.yaml:" \
  "data key is the flattened path with .yaml"
assert_contains "$OUT" 'rules: "mimir"' "rules label is the target, quoted"
assert_contains "$OUT" 'team: "platform"' "team label is the folder, quoted"
assert_contains "$OUT" 'tenant: "platform"' "tenant label is quoted"
assert_contains "$OUT" 'k8s-sidecar-target-directory: "platform"' \
  "sidecar annotation carries the tenant"
assert_contains "$OUT" 'observability-rules/source-path: "rules/platform/mimir/deadman-alerts.yaml"' \
  "source-path annotation records the origin"
assert_contains "$OUT" "ObservabilityRulesMimirDeadman" "rule body is embedded"
assert_count "$OUT" "kind: ConfigMap" 1 "exactly one ConfigMap rendered"

summary
```

Make both executable: `chmod +x tests/lib.sh tests/chart_test.sh`

- [ ] **Step 2: Run test to verify it fails**

Run: `./tests/chart_test.sh`
Expected: FAIL. Helm errors because `Chart.yaml` does not exist.

- [ ] **Step 3: Write minimal implementation**

`Chart.yaml`:

```yaml
apiVersion: v2
name: observability-rules
description: Alerting and recording rules delivered as ConfigMaps to the Mimir, Loki and meta-Prometheus rulers
type: application
version: 0.1.0
```

`values.yaml`:

```yaml
# Which ruler evaluates the rules this release renders.
# One ArgoCD Application per value: mimir, loki, prometheus.
target: mimir

# The Mimir/Loki tenant. Becomes the sidecar's target directory (relative to its
# FOLDER) and the ConfigMap name prefix. Ignored as a directory for the
# prometheus target, which has no tenants, but still used as a name prefix.
tenant: platform

# Escape hatch for bootstrapping a target that has no rules yet. Leave false:
# an empty render plus ArgoCD prune deletes every ConfigMap for the target.
allowEmpty: false
```

`values.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["target", "tenant"],
  "properties": {
    "target": {
      "type": "string",
      "enum": ["mimir", "loki", "prometheus"]
    },
    "tenant": {
      "type": "string",
      "minLength": 1,
      "maxLength": 63,
      "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
    },
    "allowEmpty": {
      "type": "boolean",
      "default": false
    }
  }
}
```

`templates/configmaps.yaml`:

```gotemplate
{{- $target := required "values.target is required" .Values.target -}}
{{- $tenant := required "values.tenant is required" .Values.tenant -}}
{{- range $path, $_ := .Files.Glob (printf "rules/*/%s/**.yaml" $target) }}
  {{- if not (hasSuffix "-tests.yaml" (base $path)) }}
    {{- $key  := $path | trimPrefix "rules/" | replace "/" "-" }}
    {{- $team := index (splitList "/" $path) 1 }}
    {{- $name := printf "%s-%s" $tenant ($key | trimSuffix ".yaml") }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $name }}
  labels:
    rules: {{ $target | quote }}
    team: {{ $team | quote }}
    tenant: {{ $tenant | quote }}
  annotations:
    observability-rules/source-path: {{ $path | quote }}
  {{- if ne $target "prometheus" }}
    k8s-sidecar-target-directory: {{ $tenant | quote }}
  {{- end }}
data:
  {{ $key }}: |-
    {{- $.Files.Get $path | nindent 4 }}
  {{- end }}
{{- end }}
```

`rules/platform/mimir/deadman-alerts.yaml`:

```yaml
groups:
  - name: delivery-canary
    interval: 1m
    rules:
      - alert: ObservabilityRulesMimirDeadman
        expr: vector(1)
        labels:
          severity: info
          owner: platform
        annotations:
          summary: >-
            Always-firing canary proving the mimir rules delivery path is alive.
            Its absence, not its presence, is the alert.
          runbook_url: https://runbooks.internal/observability-rules/deadman
```

`Makefile`:

```makefile
.PHONY: check test lint

# Everything CI runs. Identical locally: CI invokes exactly this target and
# nothing else, so a green laptop run means a green pipeline.
check: test
	./scripts/check.sh

# Fast inner loop: chart behaviour plus helper unit tests.
test:
	./tests/chart_test.sh
	python3 -m pytest tests/ -q

lint:
	helm lint .
```

`requirements.txt`:

```
PyYAML==6.0.2
pytest==8.3.4
```

`.gitignore`:

```
.promruval_cache.json
__pycache__/
.pytest_cache/
*.pyc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./tests/chart_test.sh`
Expected: `9 passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add Chart.yaml values.yaml values.schema.json templates/ rules/ Makefile requirements.txt .gitignore tests/
git commit -m "feat: chart scaffold rendering rule files into labelled ConfigMaps"
```

---

## Task 2: Fail-closed guards in the template

Without these, a one-character typo in `target` renders an empty manifest and ArgoCD `prune: true` deletes every rule ConfigMap. This is the highest-severity defect the external review found, and the empty-match case is a second instance the review missed.

**Files:**
- Modify: `templates/configmaps.yaml`
- Test: `tests/chart_test.sh`

**Interfaces:**
- Consumes: the chart from Task 1
- Produces: rendering aborts with a named error for invalid target, invalid tenant, invalid team folder, overlong generated names, and zero matched files.

- [ ] **Step 1: Write the failing test**

Append to `tests/chart_test.sh`, before the final `summary` line:

```bash
echo "chart: fail-closed guards"

# --skip-schema-validation is deliberate: values.schema.json rejects these first,
# so without it we would be testing the schema and never reaching the template
# guard. Both layers matter, and this asserts the second one.
assert_fails_with "values.target must be mimir, loki or prometheus" \
  "template guard rejects an invalid target" \
  helm template t . --set target=mimr --set tenant=platform --skip-schema-validation

assert_fails_with "values.tenant must be a DNS label" \
  "template guard rejects an invalid tenant" \
  helm template t . --set target=mimir --set tenant=Prod_1 --skip-schema-validation

LONG_TENANT=$(printf 'a%.0s' $(seq 1 64))
assert_fails_with "values.tenant must be a DNS label of at most 63 bytes" \
  "template guard rejects an overlong tenant" \
  helm template t . --set target=mimir --set tenant="$LONG_TENANT" --skip-schema-validation

# The emptiness cases use a throwaway chart. Asserting them against the real
# repository would break the moment a later task adds a loki rule, which is
# exactly what happens in Task 3.
EMPTY_CHART=$(mktemp -d "${TMPDIR:-/tmp}/observability-rules-chart.XXXXXX")
trap 'rm -rf "$EMPTY_CHART"' EXIT
mkdir -p "$EMPTY_CHART/templates" "$EMPTY_CHART/rules/platform/loki"
cp Chart.yaml values.yaml values.schema.json "$EMPTY_CHART/"
cp templates/configmaps.yaml "$EMPTY_CHART/templates/"
printf '%s\n' 'rule_files: []' 'tests: []' > "$EMPTY_CHART/rules/platform/loki/only-tests.yaml"

assert_fails_with "matched no deployable rule files" \
  "a target holding only test fixtures refuses to render empty" \
  helm template t "$EMPTY_CHART" --set target=loki --set tenant=platform

OUT_EMPTY=$(helm template t "$EMPTY_CHART" \
  --set target=loki --set tenant=platform --set allowEmpty=true)
if [ -z "$(printf '%s' "$OUT_EMPTY" | tr -d '[:space:]')" ]; then
  pass "allowEmpty=true permits a deliberate empty render"
else
  fail "allowEmpty=true permits a deliberate empty render" "expected empty output"
fi

mkdir -p "$EMPTY_CHART/rules/Bad_Team/mimir"
printf '%s\n' 'groups: []' > "$EMPTY_CHART/rules/Bad_Team/mimir/a.yaml"
assert_fails_with "team folder must be a DNS label" \
  "invalid team folder is rejected" \
  helm template t "$EMPTY_CHART" --set target=mimir --set tenant=platform
rm -rf "$EMPTY_CHART/rules/Bad_Team"

LONG_SEGMENT=$(printf 'a%.0s' $(seq 1 60))
LONG_DIR="$EMPTY_CHART/rules/platform/mimir/$LONG_SEGMENT/$LONG_SEGMENT/$LONG_SEGMENT/$LONG_SEGMENT"
mkdir -p "$LONG_DIR"
printf '%s\n' 'groups: []' > "$LONG_DIR/a.yaml"
assert_fails_with "generated ConfigMap name or key exceeds 253 bytes" \
  "overlong generated data key is rejected" \
  helm template t "$EMPTY_CHART" --set target=mimir --set tenant=platform
rm -rf "$EMPTY_CHART/rules/platform/mimir"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./tests/chart_test.sh`
Expected: the seven new guard assertions FAIL. In particular the only-fixtures case renders empty and *succeeds*, which is precisely the defect: the glob counts test fixtures before the loop skips them.

- [ ] **Step 3: Write minimal implementation**

Replace `templates/configmaps.yaml` in full:

```gotemplate
{{- $target := required "values.target is required" .Values.target -}}
{{- if not (has $target (list "mimir" "loki" "prometheus")) -}}
  {{- fail (printf "values.target must be mimir, loki or prometheus; got %q" $target) -}}
{{- end -}}
{{- $tenant := required "values.tenant is required" .Values.tenant -}}
{{- if or (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $tenant)) (gt (len $tenant) 63) -}}
  {{- fail (printf "values.tenant must be a DNS label of at most 63 bytes; got %q" $tenant) -}}
{{- end -}}
{{- $matched := .Files.Glob (printf "rules/*/%s/**.yaml" $target) -}}
{{- $deployable := list -}}
{{- range $path, $_ := $matched -}}
  {{- if not (hasSuffix "-tests.yaml" (base $path)) -}}
    {{- $deployable = append $deployable $path -}}
  {{- end -}}
{{- end -}}
{{- if and (eq (len $deployable) 0) (not .Values.allowEmpty) -}}
  {{- fail (printf "target %q matched no deployable rule files; refusing to render an empty manifest because ArgoCD prune would delete every existing ConfigMap. Set allowEmpty=true only when deliberately bootstrapping a target." $target) -}}
{{- end -}}
{{- range $path := $deployable }}
    {{- $key  := $path | trimPrefix "rules/" | replace "/" "-" }}
    {{- $team := index (splitList "/" $path) 1 }}
    {{- $name := printf "%s-%s" $tenant ($key | trimSuffix ".yaml") }}
    {{- if or (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $team)) (gt (len $team) 63) }}
      {{- fail (printf "team folder must be a DNS label of at most 63 bytes: %s" $path) }}
    {{- end }}
    {{- if or (gt (len $name) 253) (gt (len $key) 253) }}
      {{- fail (printf "generated ConfigMap name or key exceeds 253 bytes: %s" $path) }}
    {{- end }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $name }}
  labels:
    rules: {{ $target | quote }}
    team: {{ $team | quote }}
    tenant: {{ $tenant | quote }}
  annotations:
    observability-rules/source-path: {{ $path | quote }}
  {{- if ne $target "prometheus" }}
    k8s-sidecar-target-directory: {{ $tenant | quote }}
  {{- end }}
data:
  {{ $key }}: |-
    {{- $.Files.Get $path | nindent 4 }}
{{- end }}
```

**Why the `$deployable` list and not `len $matched`:** the glob counts `*-tests.yaml`
fixtures, which the loop then skips. A target holding only fixtures would pass the
emptiness guard and still render nothing, which is the exact empty-render-then-prune
disaster the guard exists to prevent. Verified: a `rules/platform/loki/` containing
only `only-tests.yaml` gives `len $matched` of 1 and renders zero documents.

Note also that `{{- range $path := $deployable }}` binds `$path` to the **element**,
not the index, because `$deployable` is a list rather than the map `Files.Glob` returns.

- [ ] **Step 4: Run test to verify it passes**

Run: `./tests/chart_test.sh`
Expected: `16 passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add templates/configmaps.yaml tests/chart_test.sh
git commit -m "fix: fail closed on invalid target, tenant, team and empty renders"
```

---

## Task 3: Test-file exclusion, subfolders, and the prometheus target

**Files:**
- Create: `rules/platform/loki/deadman-alerts.yaml`
- Create: `rules/platform/prometheus/deadman-alerts.yaml`
- Create: `rules/platform/mimir/deadman-alerts-tests.yaml`
- Test: `tests/chart_test.sh`

**Interfaces:**
- Consumes: the chart from Task 2
- Produces: canaries for all three targets, which later plans' acceptance tests rely on; and proof that `-tests.yaml` files never reach a ConfigMap.

- [ ] **Step 1: Write the failing test**

Append to `tests/chart_test.sh` before `summary`:

```bash
echo "chart: exclusion, subfolders and prometheus target"

OUT_MIMIR=$(helm template t . --set target=mimir --set tenant=platform)
assert_not_contains "$OUT_MIMIR" "deadman-alerts-tests" \
  "-tests.yaml fixtures are excluded from ConfigMaps"

OUT_LOKI=$(helm template t . --set target=loki --set tenant=platform)
assert_contains "$OUT_LOKI" "name: platform-platform-loki-deadman-alerts" \
  "loki target renders its own canary"
assert_contains "$OUT_LOKI" 'k8s-sidecar-target-directory: "platform"' \
  "loki target keeps the tenant annotation"

OUT_PROM=$(helm template t . --set target=prometheus --set tenant=platform)
assert_contains "$OUT_PROM" "name: platform-platform-prometheus-deadman-alerts" \
  "prometheus target renders its own canary"
assert_not_contains "$OUT_PROM" "k8s-sidecar-target-directory" \
  "prometheus target omits the tenant annotation entirely"

mkdir -p rules/platform/mimir/nested
cat > rules/platform/mimir/nested/example-alerts.yaml <<'YAML'
groups:
  - name: nested-example
    rules:
      - alert: NestedExampleAlert
        expr: vector(1)
        labels: {severity: info, owner: platform}
        annotations:
          summary: Proves subfolders flatten into the generated key.
          runbook_url: https://runbooks.internal/observability-rules/example
YAML
OUT_NESTED=$(helm template t . --set target=mimir --set tenant=platform)
assert_contains "$OUT_NESTED" "platform-mimir-nested-example-alerts.yaml:" \
  "subfolder path flattens into the data key"
rm -rf rules/platform/mimir/nested
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./tests/chart_test.sh`
Expected: FAIL. The loki and prometheus renders abort with "matched no rule files" because those canaries do not exist yet.

- [ ] **Step 3: Write minimal implementation**

`rules/platform/loki/deadman-alerts.yaml`. Note the `or vector(1)` fallback: a bare `count_over_time` returns nothing when no logs match, which would make the canary silently stop firing for the wrong reason. LogQL's `vector()` and this exact idiom are exercised in Loki's own parser tests.

```yaml
groups:
  - name: delivery-canary
    interval: 1m
    rules:
      - alert: ObservabilityRulesLokiDeadman
        expr: |
          (sum(count_over_time({job=~".+"}[5m])) or vector(1)) >= 0
        labels:
          severity: info
          owner: platform
        annotations:
          summary: >-
            Always-firing canary proving the loki rules delivery path is alive.
            Its absence, not its presence, is the alert.
          runbook_url: https://runbooks.internal/observability-rules/deadman
```

`rules/platform/prometheus/deadman-alerts.yaml`:

```yaml
groups:
  - name: delivery-canary
    interval: 1m
    rules:
      - alert: ObservabilityRulesPrometheusDeadman
        expr: vector(1)
        labels:
          severity: info
          owner: platform
        annotations:
          summary: >-
            Always-firing canary proving the meta-Prometheus rules delivery path
            is alive. Its absence, not its presence, is the alert.
          runbook_url: https://runbooks.internal/observability-rules/deadman
```

`rules/platform/mimir/deadman-alerts-tests.yaml`:

```yaml
rule_files:
  - deadman-alerts.yaml

evaluation_interval: 1m

tests:
  - interval: 1m
    alert_rule_test:
      - eval_time: 5m
        alertname: ObservabilityRulesMimirDeadman
        exp_alerts:
          - exp_labels:
              severity: info
              owner: platform
            exp_annotations:
              summary: >-
                Always-firing canary proving the mimir rules delivery path is alive.
                Its absence, not its presence, is the alert.
              runbook_url: https://runbooks.internal/observability-rules/deadman
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./tests/chart_test.sh`
Expected: `22 passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add rules/ tests/chart_test.sh
git commit -m "feat: canaries for all three targets, with test-fixture exclusion proven"
```

---

## Task 4: rulecheck layout and naming checks

**Files:**
- Create: `scripts/rulecheck.py`
- Test: `tests/test_rulecheck.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `check_layout(root: Path) -> list[str]` returning human-readable findings, empty when clean. Tasks 5, 6 and 7 add sibling functions with the same signature. Also produces module constants `TARGETS`, `ENVIRONMENTS`, `SEVERITIES`, `PLATFORM_TEAM` used by later tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rulecheck.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rulecheck'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/rulecheck.py`:

```python
#!/usr/bin/env python3
"""Structure and contract checks for the observability-rules repository.

Query-language validity is NOT checked here: promtool and lokitool do that in
CI stage 3, and they are version-aligned with the deployed backends. This helper
covers what those tools cannot see, namely repository layout, the label and
annotation contract, the canonical environment matcher, CODEOWNERS agreement,
and dashboard identity.

Every check_* function takes the repository root and returns a list of
human-readable findings. An empty list means the check passed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TARGETS = ("mimir", "loki", "prometheus")
ENVIRONMENTS = ("dev", "staging", "prod")
SEVERITIES = ("info", "warning", "error", "critical")
PLATFORM_TEAM = "platform"

# Targets whose rules promtool can unit-test. Loki has no LogQL unit-test
# command, so a fixture there would never run and must not be committed.
TEST_FIXTURE_TARGETS = ("mimir", "prometheus")

RULE_FILENAME_RE = re.compile(r"^[a-z0-9-]+\.yaml$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
MAX_NAME_BYTES = 253
MAX_SEGMENT_BYTES = 63


def rule_entries(root: Path) -> list[Path]:
    """Every file under rules/, including ones with the wrong extension.

    check_layout rejects non-.yaml entries explicitly. Filtering them out here
    instead would let a `.yml` file sit in the repo being silently ignored by
    every check and never deployed, which is indistinguishable from working.
    """
    rules_dir = root / "rules"
    if not rules_dir.is_dir():
        return []
    return sorted(p for p in rules_dir.rglob("*") if p.is_file() or p.is_symlink())


def rule_files(root: Path) -> list[Path]:
    return [p for p in rule_entries(root) if p.suffix == ".yaml"]


def flattened_key(root: Path, path: Path) -> str:
    """The ConfigMap data key the chart will generate for this file."""
    return str(path.relative_to(root / "rules")).replace("/", "-")


def check_layout(root: Path) -> list[str]:
    findings: list[str] = []
    for path in rule_entries(root):
        rel = path.relative_to(root)
        parts = path.relative_to(root / "rules").parts

        if path.is_symlink():
            findings.append(f"{rel}: symlink; rule files must be regular files")
            continue

        if path.suffix != ".yaml":
            findings.append(f"{rel}: every file under rules/ must end in .yaml")
            continue

        if len(parts) < 3:
            findings.append(
                f"{rel}: expected rules/<team>/<target>/<file>.yaml, got {len(parts)} path segments"
            )
            continue

        team, target = parts[0], parts[1]
        filename = parts[-1]

        if target not in TARGETS:
            findings.append(
                f"{rel}: unknown target '{target}'; must be one of {', '.join(TARGETS)}"
            )
            continue

        for segment in parts[:-1]:
            if not DNS_LABEL_RE.match(segment):
                findings.append(
                    f"{rel}: directory segment '{segment}' must match {DNS_LABEL_RE.pattern}"
                )
            if len(segment.encode()) > MAX_SEGMENT_BYTES:
                findings.append(
                    f"{rel}: directory segment '{segment}' exceeds {MAX_SEGMENT_BYTES} bytes"
                )

        if not RULE_FILENAME_RE.match(filename):
            findings.append(
                f"{rel}: filename must match {RULE_FILENAME_RE.pattern}"
            )

        if target == "prometheus" and team != PLATFORM_TEAM:
            findings.append(
                f"{rel}: the prometheus target is reserved for the '{PLATFORM_TEAM}' team"
            )

        if filename.endswith("-tests.yaml") and target not in TEST_FIXTURE_TARGETS:
            findings.append(
                f"{rel}: test fixtures are only runnable under {', '.join(TEST_FIXTURE_TARGETS)}; "
                f"lokitool has no unit-test command"
            )

        key = flattened_key(root, path)
        if len(key.encode()) > MAX_NAME_BYTES:
            findings.append(
                f"{rel}: generated data key is {len(key.encode())} bytes, over the {MAX_NAME_BYTES} limit"
            )

    return findings


CHECKS = {
    "layout": check_layout,
}


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    failed = False
    for name, fn in CHECKS.items():
        findings = fn(root)
        if findings:
            failed = True
            print(f"[{name}] {len(findings)} finding(s):", file=sys.stderr)
            for f in findings:
                print(f"  {f}", file=sys.stderr)
        else:
            print(f"[{name}] ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_rulecheck.py
git commit -m "feat: rulecheck layout and naming validation"
```

---

## Task 5: rulecheck contract checks

**Files:**
- Modify: `scripts/rulecheck.py`
- Test: `tests/test_rulecheck.py`

**Interfaces:**
- Consumes: `TARGETS`, `SEVERITIES`, `PLATFORM_TEAM`, `rule_files()` from Task 4
- Produces: `check_contract(root: Path) -> list[str]`, registered in `CHECKS` under key `contract`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rulecheck.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: FAIL with `AttributeError: module 'rulecheck' has no attribute 'check_contract'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/rulecheck.py`, after `check_layout` and before `CHECKS`:

```python
import yaml

SUMMARY_ALIASES = ("summary", "message", "description")
URL_ANNOTATIONS = ("runbook_url", "dashboard_url")


def load_groups(path: Path) -> tuple[list[dict], str | None]:
    """Return (groups, error). Malformed YAML yields ([], message)."""
    try:
        doc = yaml.safe_load(path.read_text()) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [], f"unreadable or unparseable YAML: {exc}"
    if not isinstance(doc, dict):
        return [], "expected a mapping at the document root"
    groups = doc.get("groups") or []
    if not isinstance(groups, list):
        return [], "'groups' must be a list"
    return groups, None


def iter_alerts(root: Path):
    """Yield (path, alert_dict) for every alerting rule, skipping fixtures."""
    for path in rule_files(root):
        if path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        groups, err = load_groups(path)
        if err:
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for rule in group.get("rules") or []:
                if isinstance(rule, dict) and "alert" in rule:
                    yield path, rule


def check_contract(root: Path) -> list[str]:
    findings: list[str] = []
    seen_names: dict[str, Path] = {}

    for path in rule_files(root):
        if path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        _, err = load_groups(path)
        if err:
            findings.append(f"{path.relative_to(root)}: {err}")

    for path, alert in iter_alerts(root):
        rel = path.relative_to(root)
        name = alert.get("alert")
        parts = path.relative_to(root / "rules").parts
        team = parts[0] if parts else "?"

        # A rule whose labels/annotations are a string or list is malformed, but
        # it must produce a finding rather than an AttributeError traceback.
        labels = alert.get("labels")
        labels = labels if isinstance(labels, dict) else {}
        annotations = alert.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}

        severity = labels.get("severity")
        if severity not in SEVERITIES:
            findings.append(
                f"{rel}: alert {name}: severity '{severity}' must be one of {', '.join(SEVERITIES)}"
            )

        owner = labels.get("owner")
        if owner != team:
            findings.append(
                f"{rel}: alert {name}: owner label is '{owner}' but the team folder is '{team}'"
            )

        if not any(annotations.get(a) for a in SUMMARY_ALIASES):
            findings.append(
                f"{rel}: alert {name}: needs one of {', '.join(SUMMARY_ALIASES)}"
            )

        if not any(annotations.get(a) for a in URL_ANNOTATIONS):
            findings.append(
                f"{rel}: alert {name}: needs one of {', '.join(URL_ANNOTATIONS)}"
            )

        # No `!= path` guard: two alerts sharing a name inside ONE file are just
        # as indistinguishable to Alertmanager as two in different files.
        if name in seen_names:
            findings.append(
                f"{rel}: alert name '{name}' is not unique; also defined in "
                f"{seen_names[name].relative_to(root)}. Alerts carry no namespace label, "
                f"so duplicates are indistinguishable to Alertmanager."
            )
        else:
            seen_names[name] = path

    return findings
```

Then extend the registry:

```python
CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: `20 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_rulecheck.py
git commit -m "feat: rulecheck label and annotation contract with alert-name uniqueness"
```

---

## Task 6: rulecheck canonical environment matcher

This is the check that makes a future tenant-per-environment split mechanical rather than archaeological. Read the "Design note" at the top of this plan before starting.

**Files:**
- Modify: `scripts/rulecheck.py`
- Test: `tests/test_rulecheck.py`

**Interfaces:**
- Consumes: `ENVIRONMENTS`, `iter_alerts()`, `rule_files()`, `load_groups()` from Tasks 4 and 5
- Produces: `check_env_matchers(root: Path) -> list[str]`, registered under key `envmatcher`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rulecheck.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: FAIL with `AttributeError: module 'rulecheck' has no attribute 'check_env_matchers'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/rulecheck.py` before `CHECKS`:

```python
# Any appearance of the label, in any matcher form, so non-canonical usage is
# caught rather than skipped. Three details are load-bearing:
#
#   (?<![a-zA-Z0-9_])   word boundary, so my_deployment_environment is not matched
#   "..." | '...' | `...`  PromQL accepts single quotes and backticks as string
#                       delimiters, verified with promtool. Matching only double
#                       quotes would let deployment_environment='prod' bypass the
#                       contract silently, the worst possible failure for a check
#                       whose entire purpose is making the environment set derivable.
#   (?:=~|!~|=|!=)      every operator, so non-canonical ones are reported, not skipped
ENV_ANY_RE = re.compile(
    r"""(?<![a-zA-Z0-9_])deployment_environment\s*(?:=~|!~|=|!=)\s*"""
    r"""(?:"[^"]*"|'[^']*'|`[^`]*`)"""
)
# The one permitted form. No \s*, double quotes only: whitespace and alternative
# delimiters fail this by construction and are reported as non-canonical.
ENV_CANONICAL_RE = re.compile(r'deployment_environment=~"([a-z|]+)"')


def iter_expressions(root: Path):
    """Yield (path, rule_name, expr) for every rule that has an expression.

    Only the `expr` field is examined, so a matcher inside an annotation,
    a summary string or a YAML comment cannot influence the result.
    """
    for path in rule_files(root):
        if path.name.endswith("-tests.yaml") or path.is_symlink():
            continue
        groups, err = load_groups(path)
        if err:
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for rule in group.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                expr = rule.get("expr")
                if isinstance(expr, str):
                    yield path, rule.get("alert") or rule.get("record"), expr


def check_env_matchers(root: Path) -> list[str]:
    findings: list[str] = []
    for path, name, expr in iter_expressions(root):
        rel = path.relative_to(root)
        # finditer, not findall: the pattern has no capture group, so we want the
        # whole matched text of each occurrence in order to compare them literally.
        raw = [m.group(0) for m in ENV_ANY_RE.finditer(expr)]
        if not raw:
            continue

        if len(set(raw)) > 1:
            findings.append(
                f"{rel}: {name}: deployment_environment matchers must be byte-identical "
                f"within one expression; found {sorted(set(raw))}"
            )

        for occurrence in sorted(set(raw)):
            canonical = ENV_CANONICAL_RE.fullmatch(occurrence)
            if not canonical:
                findings.append(
                    f"{rel}: {name}: '{occurrence}' is not the canonical form. "
                    f'Use deployment_environment=~"staging|prod": =~ only, double quotes, '
                    f"no whitespace, no negation."
                )
                continue

            values = canonical.group(1).split("|")
            unknown = [v for v in values if v not in ENVIRONMENTS]
            if unknown:
                findings.append(
                    f"{rel}: {name}: unknown environment(s) {unknown}; "
                    f"known values are {', '.join(ENVIRONMENTS)}"
                )
                continue

            if len(set(values)) != len(values):
                findings.append(f"{rel}: {name}: duplicate environment values in '{occurrence}'")
                continue

            expected = [e for e in ENVIRONMENTS if e in values]
            if values != expected:
                findings.append(
                    f"{rel}: {name}: environments must be in list order "
                    f"({', '.join(ENVIRONMENTS)}); expected \"{'|'.join(expected)}\""
                )

    return findings
```

Extend the registry:

```python
CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
    "envmatcher": check_env_matchers,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: `34 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_rulecheck.py
git commit -m "feat: enforce the canonical deployment_environment matcher form"
```

---

## Task 7: rulecheck CODEOWNERS reconciliation

**Files:**
- Modify: `scripts/rulecheck.py`
- Create: `.github/CODEOWNERS`
- Test: `tests/test_rulecheck.py`

**Interfaces:**
- Consumes: `rule_files()` from Task 4
- Produces: `check_codeowners(root: Path) -> list[str]`, registered under key `codeowners`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rulecheck.py`:

```python
CODEOWNERS_HEADER = """\
* @org/platform
/Chart.yaml @org/platform
/templates/ @org/platform
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: FAIL with `AttributeError: module 'rulecheck' has no attribute 'check_codeowners'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/rulecheck.py` before `CHECKS`:

```python
# Paths that govern the checks themselves. If a team could approve changes to
# these, the contract would be self-modifiable.
PLATFORM_OWNED_PATHS = (
    "/Chart.yaml",
    "/values.yaml",
    "/values.schema.json",
    "/templates/",
    "/validation.yaml",
    "/scripts/",
    "/.github/",
)


def team_folders(root: Path) -> set[str]:
    teams: set[str] = set()
    for parent in ("rules", "dashboards"):
        base = root / parent
        if base.is_dir():
            teams |= {d.name for d in base.iterdir() if d.is_dir()}
    return teams


def codeowners_entries(root: Path) -> tuple[set[str], set[str]]:
    """Return (team names claimed under rules/ or dashboards/, all path patterns)."""
    path = root / ".github" / "CODEOWNERS"
    teams: set[str] = set()
    patterns: set[str] = set()
    if not path.is_file():
        return teams, patterns
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern = line.split()[0]
        patterns.add(pattern)
        for parent in ("/rules/", "/dashboards/"):
            if pattern.startswith(parent):
                remainder = pattern[len(parent):].strip("/")
                if remainder:
                    teams.add(remainder.split("/")[0])
    return teams, patterns


def check_codeowners(root: Path) -> list[str]:
    findings: list[str] = []
    if not (root / ".github" / "CODEOWNERS").is_file():
        return [".github/CODEOWNERS is missing"]

    owned_teams, patterns = codeowners_entries(root)
    actual_teams = team_folders(root)

    for team in sorted(actual_teams - owned_teams):
        findings.append(
            f"team '{team}' has folders but no CODEOWNERS entry; "
            f"add '/rules/{team}/ @org/{team}'"
        )

    for team in sorted(owned_teams - actual_teams):
        findings.append(
            f"CODEOWNERS claims team '{team}' but no rules/ or dashboards/ folder exists"
        )

    for required in PLATFORM_OWNED_PATHS:
        if required not in patterns:
            findings.append(
                f"CODEOWNERS must assign the platform team to '{required}', "
                f"otherwise a team can approve changes to the checks that govern it"
            )

    return findings
```

Extend the registry:

```python
CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
    "envmatcher": check_env_matchers,
    "codeowners": check_codeowners,
}
```

Create `.github/CODEOWNERS`:

```
# Default owner for anything not matched below.
* @org/platform

# Paths that govern the checks themselves. A team must not be able to approve
# changes to the contract that governs its own changes, so these stay with the
# platform team regardless of who is editing rules.
/Chart.yaml @org/platform
/values.yaml @org/platform
/values.schema.json @org/platform
/templates/ @org/platform
/validation.yaml @org/platform
/scripts/ @org/platform
/.github/ @org/platform

# Per-team ownership. One entry covers a team's rules and its dashboards.
/rules/platform/ @org/platform
/dashboards/platform/ @org/platform
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rulecheck.py -q && python3 scripts/rulecheck.py .`
Expected: `38 passed`, then `[layout] ok` / `[contract] ok` / `[envmatcher] ok` / `[codeowners] ok`

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_rulecheck.py .github/CODEOWNERS
git commit -m "feat: reconcile CODEOWNERS against team folders and protect check-governing paths"
```

---

## Task 8: Dashboards tree and uid identity checks

A changed `uid` orphans the live dashboard and breaks every link and annotation pointing at it, while looking in the diff like an ordinary edit. That is the failure this task exists to prevent.

**Files:**
- Modify: `scripts/rulecheck.py`
- Create: `dashboards/platform/delivery-canary.json`
- Test: `tests/test_rulecheck.py`

**Interfaces:**
- Consumes: `team_folders()` from Task 7
- Produces: `check_dashboards(root: Path, base_ref: str | None = None) -> list[str]`, registered under key `dashboards`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_rulecheck.py`:

```python
import json
import subprocess


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rulecheck.py -q`
Expected: FAIL with `AttributeError: module 'rulecheck' has no attribute 'check_dashboards'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/rulecheck.py`:

```python
import json
import subprocess

DASHBOARD_FILENAME_RE = re.compile(r"^[a-z0-9-]+\.json$")


def dashboard_files(root: Path) -> list[Path]:
    base = root / "dashboards"
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.json") if p.is_file())


def _uids_at_ref(root: Path, ref: str) -> dict[str, str]:
    """Map relative path -> uid as of the given git ref. Missing files are skipped."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref, "dashboards/"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    except subprocess.CalledProcessError:
        return {}

    uids: dict[str, str] = {}
    for rel in listing:
        if not rel.endswith(".json"):
            continue
        try:
            blob = subprocess.run(
                ["git", "-C", str(root), "show", f"{ref}:{rel}"],
                capture_output=True, text=True, check=True,
            ).stdout
            uid = json.loads(blob).get("uid")
        except (subprocess.CalledProcessError, json.JSONDecodeError, AttributeError):
            continue
        if uid:
            uids[rel] = uid
    return uids


def check_dashboards(root: Path, base_ref: str | None = None) -> list[str]:
    findings: list[str] = []
    seen_uids: dict[str, Path] = {}
    current: dict[str, str] = {}

    for path in dashboard_files(root):
        rel = path.relative_to(root)

        if not DASHBOARD_FILENAME_RE.match(path.name):
            findings.append(f"{rel}: filename must match {DASHBOARD_FILENAME_RE.pattern}")

        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            findings.append(f"{rel}: invalid JSON: {exc}")
            continue

        uid = doc.get("uid")
        if not uid:
            findings.append(f"{rel}: dashboard must declare a 'uid'")
            continue

        current[str(rel)] = uid

        if uid in seen_uids and seen_uids[uid] != path:
            findings.append(
                f"{rel}: uid '{uid}' is not unique; also used by "
                f"{seen_uids[uid].relative_to(root)}"
            )
        else:
            seen_uids.setdefault(uid, path)

    if base_ref:
        for rel, old_uid in _uids_at_ref(root, base_ref).items():
            new_uid = current.get(rel)
            if new_uid and new_uid != old_uid:
                findings.append(
                    f"{rel}: uid changed from '{old_uid}' to '{new_uid}'. This orphans the "
                    f"live dashboard and breaks every link and annotation pointing at it. "
                    f"If deliberate, say so explicitly in the pull request."
                )

    return findings
```

Register it, wrapping to match the single-argument signature the runner uses:

```python
CHECKS = {
    "layout": check_layout,
    "contract": check_contract,
    "envmatcher": check_env_matchers,
    "codeowners": check_codeowners,
    "dashboards": check_dashboards,
}
```

Update `main` so a base ref can be passed through:

```python
def main(argv: list[str]) -> int:
    import os

    root = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    base_ref = os.environ.get("BASE_REF") or None

    failed = False
    for name, fn in CHECKS.items():
        findings = fn(root, base_ref) if name == "dashboards" else fn(root)
        if findings:
            failed = True
            print(f"[{name}] {len(findings)} finding(s):", file=sys.stderr)
            for f in findings:
                print(f"  {f}", file=sys.stderr)
        else:
            print(f"[{name}] ok")
    return 1 if failed else 0
```

Create `dashboards/platform/delivery-canary.json`:

```json
{
  "uid": "obs-rules-delivery-canary",
  "title": "Observability Rules: Delivery Canary",
  "tags": ["observability-rules", "platform"],
  "timezone": "browser",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "text",
      "title": "Purpose",
      "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
      "options": {
        "mode": "markdown",
        "content": "If this dashboard is present in Grafana, the Git Sync delivery path from the observability-rules repository is working. Its absence is the signal."
      }
    }
  ]
}
```

Add the corresponding CODEOWNERS line is already covered by `/dashboards/platform/`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rulecheck.py -q && python3 scripts/rulecheck.py .`
Expected: `44 passed`, then all five checks report `ok`

- [ ] **Step 5: Commit**

```bash
git add scripts/rulecheck.py tests/test_rulecheck.py dashboards/
git commit -m "feat: dashboard identity checks with uid-change detection against the base ref"
```

---

## Task 9: check.sh orchestrator with external tools and render assertions

**Files:**
- Create: `scripts/check.sh`, `validation.yaml`
- Modify: `Makefile`
- Test: run it

**Interfaces:**
- Consumes: `scripts/rulecheck.py` from Tasks 4 to 8, the chart from Tasks 1 to 3
- Produces: `make check`, the single command CI runs

- [ ] **Step 1: Write the failing test**

Create `validation.yaml`:

```yaml
validationRules:
  - name: check-alert-summary
    scope: Alert
    validations:
      - type: hasAnyOfAnnotations
        params:
          annotations: ["summary", "message", "description"]

  - name: check-severity-label
    scope: Alert
    validations:
      - type: hasLabels
        params:
          labels: ["severity"]
      - type: labelHasAllowedValue
        params:
          label: "severity"
          allowedValues: ["info", "warning", "error", "critical"]

  - name: check-owner-label
    scope: Alert
    validations:
      - type: hasLabels
        params:
          labels: ["owner"]

  - name: check-has-one-url
    scope: Alert
    validations:
      - type: hasAnyOfAnnotations
        params:
          annotations: ["runbook_url", "dashboard_url"]
```

Note there is deliberately no `allowedValues` list for `owner`: ***REMOVED*** maintained one by hand and it drifted out of sync with reality. `rulecheck.py` derives the truth from the filesystem instead.

Then run: `./scripts/check.sh`
Expected: FAIL, `no such file or directory`.

- [ ] **Step 2: Run to verify it fails**

Run: `./scripts/check.sh`
Expected: `bash: ./scripts/check.sh: No such file or directory`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/check.sh`:

```bash
#!/usr/bin/env bash
# The single validation entrypoint. Runs identically on a laptop and in CI so a
# contributor can reproduce any failure without pushing.
#
# Stages are ordered cheapest-first: the mistakes people actually make are
# caught in seconds rather than after a full render.
set -uo pipefail
cd "$(dirname "$0")/.."

STATUS=0
stage() { printf '\n=== %s ===\n' "$1"; }
run()   { "$@" || STATUS=1; }

have() { command -v "$1" >/dev/null 2>&1; }

require() {
  if ! have "$1"; then
    printf 'missing required tool: %s\n' "$1" >&2
    STATUS=1
    return 1
  fi
}

stage "1-2. structure, contract, environment matchers, CODEOWNERS, dashboards"
require python3 && run python3 scripts/rulecheck.py .

# macOS ships bash 3.2, which has no `mapfile`. This keeps the script working
# with the system bash so `make check` behaves identically everywhere.
# Reads NUL-delimited paths from stdin into the global array FILES.
# Verified: both `find -print0` and `sort -z` work on BSD and GNU userland.
collect() {
  FILES=()
  while IFS= read -r -d '' f; do FILES+=("$f"); done
}

stage "3. contract (promruval)"
if require promruval; then
  # promruval needs explicit paths; fixtures are not rule files.
  # Split by dialect. promruval parses PromQL by default; --support-loki is
  # required for LogQL rules and --support-mimir for Mimir-flavoured ones.
  # Verified against the promruval README.
  collect < <(find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml' -print0 | sort -z)
  [ "${#FILES[@]}" -gt 0 ] && \
    run promruval validate --config-file=./validation.yaml --support-mimir "${FILES[@]}"

  collect < <(find rules -path 'rules/*/loki/*' -name '*.yaml' -print0 | sort -z)
  [ "${#FILES[@]}" -gt 0 ] && \
    run promruval validate --config-file=./validation.yaml --support-loki "${FILES[@]}"
fi

stage "4. syntax (promtool, lokitool)"
if require promtool; then
  collect < <(find rules \( -path 'rules/*/mimir/*' -o -path 'rules/*/prometheus/*' \) \
    -name '*.yaml' ! -name '*-tests.yaml' -print0 | sort -z)
  [ "${#FILES[@]}" -gt 0 ] && run promtool check rules "${FILES[@]}"
fi
# lokitool is REQUIRED, not optional. Making it optional meant CI silently
# skipped every LogQL syntax check, since it was never installed there.
# Set ALLOW_MISSING_LOKITOOL=1 for a local run without it, never in CI.
if [ "${ALLOW_MISSING_LOKITOOL:-0}" = "1" ] && ! have lokitool; then
  printf 'WARNING: lokitool missing, LogQL syntax NOT checked (local override)\n' >&2
elif require lokitool; then
  collect < <(find rules -path 'rules/*/loki/*' -name '*.yaml' -print0 | sort -z)
  [ "${#FILES[@]}" -gt 0 ] && run lokitool rules check "${FILES[@]}"
fi

stage "5. unit tests (promtool test rules)"
if have promtool; then
  collect < <(find rules -name '*-tests.yaml' -print0 | sort -z)
  if [ "${#FILES[@]}" -eq 0 ]; then
    printf 'no test fixtures found\n'
  else
    for f in "${FILES[@]}"; do
      # promtool resolves rule_files relative to the fixture, so run in its directory.
      ( cd "$(dirname "$f")" && promtool test rules "$(basename "$f")" ) || STATUS=1
    done
  fi
fi

stage "6. render (helm template) and Kubernetes constraints"
if require helm; then
  run ./tests/chart_test.sh
  run python3 scripts/render_assert.py
fi

if [ "$STATUS" -eq 0 ]; then
  printf '\nall checks passed\n'
else
  printf '\nCHECKS FAILED\n' >&2
fi
exit "$STATUS"
```

Create `scripts/render_assert.py`:

```python
#!/usr/bin/env python3
"""Assertions against rendered chart output, not against source files.

These catch the failure modes that only exist after templating: a file silently
excluded from the render, two paths colliding on one generated name, a ConfigMap
over the 1MiB limit, and template-induced YAML corruption.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

TARGETS = ("mimir", "loki", "prometheus")
CONFIGMAP_LIMIT = 1024 * 1024
HEADROOM = 16 * 1024

ROOT = Path(__file__).resolve().parents[1]


def render(target: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "t", str(ROOT),
         "--set", f"target={target}", "--set", "tenant=platform"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        # A target with no rules legitimately fails closed; that is not an error here.
        if "matched no rule files" in out.stderr:
            return []
        print(f"helm template failed for target={target}:\n{out.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def main() -> int:
    findings: list[str] = []
    rendered_sources: set[str] = set()
    names: dict[str, str] = {}
    keys: dict[str, str] = {}

    for target in TARGETS:
        for doc in render(target):
            if doc.get("kind") != "ConfigMap":
                findings.append(f"{target}: rendered a {doc.get('kind')}, expected only ConfigMaps")
                continue

            name = doc["metadata"]["name"]
            if name in names:
                findings.append(f"duplicate ConfigMap name '{name}' (also in {names[name]})")
            names[name] = target

            source = doc["metadata"].get("annotations", {}).get("observability-rules/source-path")
            if not source:
                findings.append(f"{name}: missing observability-rules/source-path annotation")
            else:
                rendered_sources.add(source)

            for key, payload in doc.get("data", {}).items():
                if key in keys:
                    findings.append(
                        f"duplicate data key '{key}' in {name} (also in {keys[key]}). "
                        f"These would overwrite each other in the ruler directory."
                    )
                keys[key] = name

                size = len(payload.encode())
                if size > CONFIGMAP_LIMIT - HEADROOM:
                    findings.append(
                        f"{name}: data key '{key}' is {size} bytes, within {HEADROOM} "
                        f"of the {CONFIGMAP_LIMIT} ConfigMap limit"
                    )

                try:
                    yaml.safe_load(payload)
                except yaml.YAMLError as exc:
                    findings.append(
                        f"{name}: extracted payload for '{key}' is not valid YAML, "
                        f"which means templating corrupted it: {exc}"
                    )

    expected = {
        str(p.relative_to(ROOT))
        for p in (ROOT / "rules").rglob("*.yaml")
        if p.is_file() and not p.name.endswith("-tests.yaml")
    }
    for missing in sorted(expected - rendered_sources):
        findings.append(
            f"{missing}: present in the repository but absent from every rendered ConfigMap. "
            f"A silently unrendered file is indistinguishable from one that works."
        )

    for f in findings:
        print(f"  {f}", file=sys.stderr)
    if findings:
        print(f"[render] {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("[render] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Make both executable: `chmod +x scripts/check.sh scripts/render_assert.py`

- [ ] **Step 4: Run to verify it passes**

Run: `make check`
Expected: every stage reports ok and the run ends with `all checks passed`. Stages whose tools are not installed print a skip notice; install `promruval`, `promtool` and `lokitool` to exercise them fully.

- [ ] **Step 5: Commit**

```bash
git add scripts/check.sh scripts/render_assert.py validation.yaml Makefile
git commit -m "feat: check.sh orchestrator with render assertions over templated output"
```

---

## Task 10: GitHub Actions, README and branch protection

**Files:**
- Create: `.github/workflows/ci.yaml`, `README.md`, `docs/branch-protection.md`
- Create: `tools/checksums.txt`
- Create: `examples/alerts.yaml`, `examples/dashboard.json`

**Interfaces:**
- Consumes: `make check` from Task 9
- Produces: the required status check that branch protection references

- [ ] **Step 1: Write the failing test**

There is no unit test for a workflow file. The verification is that the workflow parses and its steps match what `make check` needs. Write the check first:

```bash
python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yaml'))"
```

Expected right now: `FileNotFoundError`.

- [ ] **Step 2: Run to verify it fails**

Run the command above.
Expected: `FileNotFoundError: [Errno 2] No such file or directory: '.github/workflows/ci.yaml'`

- [ ] **Step 3: Write minimal implementation**

Create `.github/workflows/ci.yaml`. Every third-party action is pinned by commit SHA, and the job holds no secrets and needs no internal network access, which is what makes it safe to run against pull-request-controlled code.

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

env:
  HELM_VERSION: "v3.16.3"
  PROMTOOL_VERSION: "3.1.0"
  PROMRUVAL_VERSION: "3.2.0"
  LOKITOOL_VERSION: "3.3.2"

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0             # uid-change detection needs the base commit
          persist-credentials: false # this job executes PR-controlled code, so it
                                     # must not leave a usable token in .git/config

      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b # v5.3.0
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - uses: azure/setup-helm@fe7b79cd5ee1e45176fcad797de68ecaf3ca4814 # v4.2.0
        with:
          version: ${{ env.HELM_VERSION }}

      # Every download is checksum-verified. Pinning a version proves which URL
      # was requested, not what arrived. Generate tools/checksums.txt once by
      # downloading each artifact and recording `sha256sum <file>`; CI then fails
      # if any of them ever changes underneath the pin.
      - name: Install pinned tools
        run: |
          set -euo pipefail
          curl -sSfL --retry 3 -o prometheus.tar.gz \
            "https://github.com/prometheus/prometheus/releases/download/v${PROMTOOL_VERSION}/prometheus-${PROMTOOL_VERSION}.linux-amd64.tar.gz"
          curl -sSfL --retry 3 -o promruval.tar.gz \
            "https://github.com/fusakla/promruval/releases/download/v${PROMRUVAL_VERSION}/promruval_${PROMRUVAL_VERSION}_linux_amd64.tar.gz"
          curl -sSfL --retry 3 -o lokitool.zip \
            "https://github.com/grafana/loki/releases/download/v${LOKITOOL_VERSION}/lokitool-linux-amd64.zip"
          sha256sum --check --strict tools/checksums.txt
          tar -xzf prometheus.tar.gz
          sudo install "prometheus-${PROMTOOL_VERSION}.linux-amd64/promtool" /usr/local/bin/promtool
          tar -xzf promruval.tar.gz
          sudo install promruval /usr/local/bin/promruval
          unzip -q lokitool.zip
          sudo install lokitool-linux-amd64 /usr/local/bin/lokitool

      # One command, and the same one a contributor runs. `make check` depends on
      # `test`, so pytest runs here too rather than as a separate step that could
      # drift away from the local experience.
      - name: make check
        env:
          BASE_REF: ${{ github.event.pull_request.base.sha }}
        run: make check
```

Create `tools/checksums.txt`. Generate it once, on the machine you trust, rather than copying values from anywhere:

```bash
mkdir -p tools
PROMTOOL_VERSION=3.1.0
PROMRUVAL_VERSION=3.2.0
LOKITOOL_VERSION=3.3.2
curl -sSfL -o prometheus.tar.gz \
  "https://github.com/prometheus/prometheus/releases/download/v${PROMTOOL_VERSION}/prometheus-${PROMTOOL_VERSION}.linux-amd64.tar.gz"
curl -sSfL -o promruval.tar.gz \
  "https://github.com/fusakla/promruval/releases/download/v${PROMRUVAL_VERSION}/promruval_${PROMRUVAL_VERSION}_linux_amd64.tar.gz"
curl -sSfL -o lokitool.zip \
  "https://github.com/grafana/loki/releases/download/v${LOKITOOL_VERSION}/lokitool-linux-amd64.zip"
sha256sum prometheus.tar.gz promruval.tar.gz lokitool.zip > tools/checksums.txt
rm -f prometheus.tar.gz promruval.tar.gz lokitool.zip
cat tools/checksums.txt
```

The result is three lines of `<sha256>  <filename>`. When a tool version changes,
regenerate the file in the same commit; CI fails loudly if the artifact behind a
pinned version ever changes, which a version pin alone cannot detect.

Create `docs/branch-protection.md`:

```markdown
# Branch protection

CODEOWNERS on its own is review *routing*, not enforcement. Without the settings
below, a contributor can merge without review, including changes to the checks
that govern their own contributions.

Required settings on `main`:

- Require a pull request before merging.
- Require approval from Code Owners.
- Dismiss stale approvals when new commits are pushed.
- Require the `check` status check to pass.
- Require branches to be up to date before merging.
- Block force pushes and branch deletion.
- Restrict who can bypass these settings to the platform team.

The `check` status check is produced by `.github/workflows/ci.yaml`. If that
workflow is renamed, update the required check or protection silently stops
enforcing anything.

Grafana Git Sync opens branches for dashboard edits made in the UI. Those
branches go through the same pull request and review path as any other change,
which is the reason `workflows: ["branch"]` was chosen over `["write"]`.
```

Create `README.md`:

```markdown
# observability-rules

Alerting rules, recording rules and dashboards for the global Mimir, Loki and
meta-Prometheus stack. Teams own their own folder; the platform team owns the
chart, the checks and CI.

Design: [`docs/superpowers/specs/2026-08-10-observability-rules-design.md`](docs/superpowers/specs/2026-08-10-observability-rules-design.md)

## Adding an alert

1. Put the file at `rules/<your-team>/<target>/<service>[-<type>]-alerts.yaml`,
   where `<target>` is the ruler that should evaluate it:
   - `mimir` for metrics alerts
   - `loki` for log alerts
   - `prometheus` for alerts about the observability stack itself (platform team only)
2. Every alert needs:
   - label `severity`: one of `info`, `warning`, `error`, `critical`
   - label `owner`: exactly your team folder name
   - annotation `summary` (or `message`/`description`)
   - annotation `runbook_url` or `dashboard_url`
   - a name unique across the whole repository
3. If the alert is environment-specific, use exactly this form:

   ```promql
   deployment_environment=~"staging|prod"
   ```

   No matcher at all means all environments. Values come from `dev`, `staging`,
   `prod`, in that order, no duplicates, no negation, no plain `=`.
4. Run `make check` before pushing. It runs exactly what CI runs.

## Adding a dashboard

Put it at `dashboards/<your-team>/<name>.json` with a stable `uid`. **Never
change the `uid` of an existing dashboard**: it orphans the live one and breaks
every link and annotation pointing at it. CI will catch this.

Dashboards reach Grafana through Git Sync, not ArgoCD. Editing in the Grafana UI
opens a branch, which you turn into a pull request as usual.

## Unit tests

`promtool test rules` runs any `<service>-alerts-tests.yaml` fixture next to the
rules it tests. **This works for `mimir` and `prometheus` targets only.**
`lokitool` has no unit-test command, so LogQL alerts cannot be behaviourally
tested. Do not assume log alerts are covered by tests.

## Local setup

```bash
pip install -r requirements.txt
# plus: helm, promtool, promruval, lokitool
make check
```
```

Create `examples/alerts.yaml`:

```yaml
# A complete, contract-compliant example. Copy this as a starting point.
groups:
  - name: checkout-http
    rules:
      - alert: CheckoutHighErrorRate
        expr: |
          sum(rate(http_server_request_duration_seconds_count{
            service_name="checkout",
            http_response_status_code=~"5..",
            deployment_environment=~"staging|prod"
          }[5m]))
          /
          sum(rate(http_server_request_duration_seconds_count{
            service_name="checkout",
            deployment_environment=~"staging|prod"
          }[5m])) > 0.05
        for: 10m
        labels:
          severity: critical
          owner: payments
        annotations:
          summary: >-
            Checkout is returning more than 5% 5xx responses in
            {{ $labels.deployment_environment }}.
          runbook_url: https://runbooks.internal/payments/checkout-error-rate
          dashboard_url: https://grafana.internal/d/payments-overview
```

Create `examples/dashboard.json`:

```json
{
  "uid": "payments-example",
  "title": "Payments: Example",
  "tags": ["payments"],
  "timezone": "browser",
  "schemaVersion": 39,
  "panels": [
    {
      "type": "timeseries",
      "title": "Request rate",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
      "targets": [
        {
          "expr": "sum(rate(http_server_request_duration_seconds_count{service_name=\"checkout\"}[5m]))",
          "refId": "A"
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Run to verify it passes**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('workflow parses')"
make check
```

Expected: `workflow parses`, then `all checks passed`.

Note `examples/` sits outside `rules/` and `dashboards/`, so it is deliberately not validated. It is documentation, not deployed content.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yaml README.md docs/branch-protection.md examples/
git commit -m "feat: CI workflow, contributor guide and branch protection requirements"
```

---

## Self-Review

**Spec coverage.** Every Section 11 stage maps to a task: stage 1 to Tasks 4, 6, 7; stage 2 to Task 9's promruval invocation with explicit paths; stage 3 to Task 9; stage 4 to Task 9's per-fixture loop; stage 5 to Task 9's `render_assert.py`; stage 6 to Task 8; stage 7 to the mixins plan, which is out of scope here and named as such. Section 5's chart lands in Tasks 1 to 3, including all five load-bearing properties. Section 4's contract lands in Tasks 5 and 6. Section 12's canaries land in Tasks 1 and 3. Section 9's dashboards tree lands in Task 8, with the Git Sync Repository resource itself deferred to the Grafana plan since it is Grafana-side configuration.

**Deliberate gaps, all deferred to named follow-on plans:** backend ruler wiring, ArgoCD Applications, the Git Sync Repository resource and its sync-failure alert, mixins and CI stage 7, and Phase 2 live validation.

**Type consistency.** `check_layout`, `check_contract`, `check_env_matchers`, `check_codeowners` all take `(root: Path)` and return `list[str]`. `check_dashboards` takes `(root, base_ref=None)` and `main` special-cases it, which is called out in Task 8 Step 3. Helpers `rule_files`, `load_groups`, `iter_alerts`, `iter_expressions`, `team_folders`, `codeowners_entries`, `dashboard_files`, `flattened_key` are each defined once and used with consistent signatures. The generated-name convention `<tenant>-<team>-<target>-<flattened>` is asserted identically in Task 1's shell test and Task 9's `render_assert.py`.

**One known deviation from the spec**, stated rather than hidden: Section 11 stage 1 asks for AST-based matcher enforcement, and this plan parses YAML and applies a strict regex to `expr` fields instead. The reasoning and the residual weakness are in the "Design note" above.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-10-repository-foundation.md`.
