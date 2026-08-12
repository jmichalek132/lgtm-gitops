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

summary
