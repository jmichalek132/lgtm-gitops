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
