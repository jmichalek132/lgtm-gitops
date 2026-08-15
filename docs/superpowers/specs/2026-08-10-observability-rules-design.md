# Observability Rules Repository: Design

Date: 2026-08-10
Status: Approved, ready for implementation planning

## 1. Context

We run one global Mimir and one global Loki, self-hosted OSS. All environments
(dev, staging, prod) write to that single stack, separated today only by a
`deployment_environment` label, into a single tenant. ArgoCD is established and
the backends are deployed. This spec covers the **alerting rules, recording rules,
dashboards and mixins layer** that sits on top.

Those travel by two different delivery paths, which is the single most important
structural fact in this document:

```
rules       git -> ArgoCD -> ConfigMap -> sidecar -> Mimir/Loki/Prometheus ruler
dashboards  git -> Grafana Git Sync -> Grafana          (ArgoCD not involved)
```

Mixins are not a third path but a source that fans out into both, rendering
alerts into the first and dashboards into the second.

The design is a rebuild of a prior system that solved this same problem in
production. That system worked. This design keeps its shape and fixes the
specific things that went wrong, each of which is cited with evidence in
Appendix A.

### Out of scope

- SLOs (the prior system used pyrra; deliberately deferred)
- Alertmanager configuration and routing
- Deploying or configuring Mimir, Loki, or ArgoCD themselves
- Instrumentation and collector configuration

Where this spec depends on a backend change, it is listed in Section 15 as a
named prerequisite rather than assumed.

## 2. Goals

1. Teams self-serve alerting rules, recording rules and dashboards through PRs
   into their own folder, with one ownership boundary covering all three.
2. A rule that passes CI and syncs in ArgoCD is very likely loaded by a ruler.
   The residual gap, that per-rule delivery is not verified end to end, is stated
   explicitly in Section 12 rather than assumed away.
3. Moving from one tenant to many is a values change, not a migration.
4. Alerts about the metrics stack keep working when the metrics stack is down.
5. Upstream mixin coverage for Mimir, Loki, Tempo and Kubernetes is vendored and
   version-tracked rather than hand-copied, and satisfies the same contract as
   hand-written content.

### Non-goals

- Multi-cluster fan-out. There is one global stack. If a second appears,
  promoting an `Application` to an `ApplicationSet` is mechanical.
- Per-team ArgoCD Applications. Per-team folders are an ownership boundary, not
  a sync boundary.
- Multiple tenants on day one.

## 3. Repository layout

```
.
├── Chart.yaml
├── values.yaml                     # target, tenant
├── templates/configmaps.yaml
├── rules/
│   └── <team>/
│       ├── mimir/                  # evaluated by the Mimir ruler
│       │   ├── <service>[-<type>]-alerts.yaml
│       │   ├── <service>-rules.yaml
│       │   └── <service>-alerts-tests.yaml
│       ├── loki/                   # evaluated by the Loki ruler
│       └── prometheus/             # evaluated by the meta Prometheus (platform team only)
├── dashboards/                     # Grafana Git Sync watches this path
│   └── <team>/
│       └── <dashboard>.json
├── mixins/                         # jsonnet sources; render into rules/ and dashboards/
│   ├── mimir/
│   │   ├── jsonnetfile.json
│   │   ├── jsonnetfile.lock.json   # pinned independently; Renovate bumps per mixin
│   │   └── mixin.libsonnet         # _config overrides for this mixin
│   ├── loki/
│   ├── tempo/
│   └── kubernetes/
├── Makefile                        # `make mixins` renders all; CI checks for drift
├── validation.yaml                 # promruval contract
├── scripts/check.sh                # single CI entrypoint, runs identically locally
├── examples/
├── .github/
│   ├── CODEOWNERS
│   └── workflows/ci.yaml
└── README.md
```

Two structural differences from the prior system:

**The `regional`/`global` level is gone.** With a single global stack it has no
meaning. The prior system scaffolded it everywhere, populated it briefly, then
abandoned it: its history contains `metrics/global/alerts.yaml`, but the tree it
shipped with held only a `.gitkeep`.

**The second level names the evaluation target, not the signal.** The prior
system used `metrics/` and `logs/`, which read as signals but always meant
destinations (Mimir ruler, Loki ruler). Once a third destination exists that is
also metrics (Section 8), the signal reading breaks. `mimir/`, `loki/`,
`prometheus/` answers the question actually asked when an alert is not firing:
which ruler was supposed to evaluate this. It also leaves room for Tempo or a
second Prometheus without another rename.

Subfolders below the target level are permitted for grouping, for example
`rules/payments/mimir/checkout/latency-alerts.yaml`.

### Naming

- Alerts: `<service>[-<type>]-alerts.yaml`
- Recording rules: `<service>-rules.yaml`
- Unit tests: `<service>-alerts-tests.yaml`
- Filenames must match `^[a-z0-9-]+\.yaml$`.
- Every team and grouping directory segment must match
  `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` and be at most 63 bytes.
- Generated ConfigMap names and data keys must each be at most 253 bytes. The
  template fails rather than truncating.
- Alert names must be unique across the whole repository (see Section 4).

The prior system silently rewrote `_` to `-` when deriving object names. A CI
error is better than a silent rename.

### Team list

**Folder names are the source of truth.** There is no team registry file. CI
enforces the consequences:

- Every `rules/<team>/` has a matching CODEOWNERS entry, and every CODEOWNERS
  rules entry has a folder. Neither can drift.
- A team's `dashboards/<team>/` folder is covered by the same CODEOWNERS entry,
  so rules and dashboards share one ownership boundary.
- Every alert's `owner` label equals its containing team folder.
- Adding a team is a folder plus a CODEOWNERS line. No chart or ArgoCD change,
  and no Git Sync change, since it watches `dashboards/` as a whole.

## 4. The team contract

| Field | Requirement |
| --- | --- |
| label `severity` | one of `info`, `warning`, `error`, `critical` |
| label `owner` | equals the containing team folder |
| annotation `summary` | required (`message` and `description` accepted as aliases) |
| annotation `runbook_url` or `dashboard_url` | at least one required |
| alert name | unique across the entire repository |

The uniqueness requirement exists because **alerts do not automatically carry the
rule namespace or source file as a label.** Mimir invokes the Prometheus rule
manager with empty external labels, and Prometheus adds no file label of its own.
Two identically named alerts in different files with otherwise identical labels
are therefore indistinguishable to Alertmanager and will be deduplicated. Unique
names are the cheapest fix and cost teams nothing they would not do anyway.

Alertmanager is out of scope, but `owner` is the label routing will key on later.
Making it unfakeable now is cheaper than reconciling it after eighty alerts exist.

### Environment selectors: one canonical form

The ordered environment list is `dev`, `staging`, `prod`. An expression with no
`deployment_environment` matcher applies to all three.

While a single tenant holds every environment, an environment-specific rule must
filter in PromQL. That is unavoidable. What is avoidable is variation. CI requires
exactly:

```promql
deployment_environment=~"staging|prod"
```

Regex match, double quotes, no whitespace around the operator, a non-empty subset
of the ordered environment list in list order without duplicates, no negation, no
plain `=`. If an expression contains the matcher more than once, every occurrence
must be byte-identical.

> **Implementation deviation (2026-08-13).** This section originally specified
> that CI parse the PromQL and LogQL selector AST rather than match raw text.
> What was built is a regex over the `expr` field of each rule, scoped so that a
> matcher in a comment, an annotation or a summary string cannot influence the
> result. The reason is dialect coverage: no single available parser accepts both
> PromQL and LogQL, and adding a Python PromQL parser plus a LogQL one to hold a
> formatting contract was judged disproportionate for the repository's first
> phase. The implementation plan records the deviation; this note brings the
> design of record in line with it.
>
> **This has already cost one real bypass.** The regex matched only the bare label
> name, so `up{"deployment_environment"="prod"}` produced zero findings while
> promtool accepted it as valid PromQL (Prometheus 3 permits a quoted label name
> inside braces). The final whole-branch review caught it and it is fixed, but it
> is the exact class of failure an AST would have made impossible: the regex must
> enumerate every spelling the parser accepts, and the parser's list is the one
> that keeps growing. Revisit the AST decision when a second such bypass appears,
> or sooner if a maintained PromQL parser with LogQL support becomes available.

This is the single highest-leverage decision in the spec for goal 3. It makes the
environment set of every rule machine-readable **from the rule itself**, so a
future tenant split can parse the selector, emit the ConfigMap into exactly those
tenants, and delete the selector, with no team re-declaring anything and no file
moves. The prior system accumulated 210 matchers across its deployable rules in 8
different shapes including a lone negation, which would have made the same
migration an archaeology exercise.

## 5. The chart

`values.yaml`:

```yaml
target: mimir     # mimir | loki | prometheus
tenant: <name>    # ruler directory for mimir and loki; object-name prefix only for prometheus
```

`values.schema.json` rejects bad input before rendering:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["target", "tenant"],
  "properties": {
    "target": { "type": "string", "enum": ["mimir", "loki", "prometheus"] },
    "tenant": {
      "type": "string", "minLength": 1, "maxLength": 63,
      "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
    },
    "allowEmpty": { "type": "boolean", "default": false }
  }
}
```

`templates/configmaps.yaml`:

```gotemplate
{{- $target := required "values.target is required" .Values.target -}}
{{- if not (has $target (list "mimir" "loki" "prometheus")) -}}
  {{- fail (printf "values.target must be mimir, loki or prometheus; got %q" $target) -}}
{{- end -}}
{{- $tenant := required "values.tenant is required" .Values.tenant -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $tenant) -}}
  {{- fail (printf "values.tenant must be a DNS label; got %q" $tenant) -}}
{{- end -}}
{{- $matched := .Files.Glob (printf "rules/*/%s/**.yaml" $target) -}}
{{- if and (eq (len $matched) 0) (not .Values.allowEmpty) -}}
  {{- fail (printf "target %q matched no rule files; refusing to render an empty manifest because ArgoCD prune would delete every existing ConfigMap. Set allowEmpty=true only when deliberately bootstrapping a target." $target) -}}
{{- end -}}
{{- range $path, $_ := $matched }}
  {{- if not (hasSuffix "-tests.yaml" (base $path)) }}
    {{- $key  := $path | trimPrefix "rules/" | replace "/" "-" }}
    {{- $team := index (splitList "/" $path) 1 }}
    {{- $name := printf "%s-%s" $tenant ($key | trimSuffix ".yaml") }}
    {{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $team) }}
      {{- fail (printf "team folder must be a DNS label: %s" $path) }}
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
{{- end }}
```

Five properties are load-bearing.

**The data key is the flattened path, not the bare filename.** The sidecar writes
each ConfigMap key as a file into the ruler's directory, and Mimir treats each
file as one rule namespace. A bare `http-alerts.yaml` key would let two teams
collide on one path and **silently overwrite each other in the ruler**, with both
PRs green and both ConfigMaps present. Flattening removes that whole class and
gives a readable 1:1:1 mapping between the git path, the ConfigMap name, and the
namespace shown in the ruler UI.

Flattening is not injective on its own: `checkout/latency-alerts.yaml` and
`checkout-latency-alerts.yaml` in the same team and target both flatten to
`<team>-<target>-checkout-latency-alerts.yaml`. **The duplicate-key assertion in
CI stage 5 is the guard**, not the flattening. It is deterministic and complete,
runs on every PR, and fails with the two offending paths named. This is a
deliberate trade: appending a path hash would make collisions structurally
impossible but would put twelve hex characters into every rule namespace name in
the ruler UI, destroying the readable mapping that is the point of flattening.

**Inputs fail closed, and so does an empty result.** `values.schema.json` and the
`fail` guards reject an absent or unknown `target`, an unsafe `tenant`, an invalid
team folder, and generated names over 253 bytes. This matters more than it looks:
an unknown target makes the glob match nothing, renders an empty manifest, and
**ArgoCD with `prune: true` then deletes every rule ConfigMap owned by that
Application.** A one-character typo in a values file would silently remove all
alerting for a target.

Validating the target value is necessary but not sufficient, because a *valid*
target that happens to match zero files renders empty too, with exactly the same
consequence. A folder convention rename, a bad merge, or a mistaken glob would do
it. So the template also refuses to render an empty manifest at all, unless
`allowEmpty: true` is passed deliberately when bootstrapping a target that has no
rules yet. Verified: without the guard, `target=loki` with no loki rules renders
one byte and reports success.

Label values are quoted so a tenant like `true` or `01` stays a string.

**`hasSuffix "-tests.yaml"`, not `contains "tests"`.** The prior system used the
latter, so a legitimately named file such as `integration-tests-alerts.yaml`
would have been silently excluded from every ConfigMap and never deployed.

**The tenant is in the object name; the data key is not.** Each tenant has its own
ruler directory, so data keys need not be unique across tenants, while Kubernetes
object names must be unique within the destination namespace. When tenants fan
out, ConfigMap names change while **data keys and rule namespaces stay
byte-identical** in the ruler APIs and UI.

Note this does not extend to silences. Alerts do not carry the rule namespace as
a label (see Section 4), so a silence cannot match on a generated namespace name
unless a rule declares such a label itself.

**`**` is required for the glob.** Verified against Helm v4.2.3: `*` does not
cross `/`, `**` does. This is what permits subfolders.

## 6. Tenancy

### Today

One tenant. `values.tenant` is set once and every ConfigMap carries
`k8s-sidecar-target-directory: <tenant>`, a **relative** path that the sidecar
resolves against its `FOLDER`. The sidecar is configured with `FOLDER: /tmp/rules`,
without the tenant.

Behaviour is identical to the prior system's. The difference is where the tenant
decision lives: in the chart, per object, rather than in the ruler's pod spec,
per container. That system wrote a literal tenant name as a fixed segment under
`/tmp/rules/` in four backend values files, and its rules repo contains zero
commits mentioning tenants across 413 commits, so any split would have required
a ruler change discovered at the moment of need.

### The move to many tenants

Two template lines and one values key:

```yaml
tenant: platform
tenantOverrides: {}      # team -> tenant
```
```gotemplate
{{- $overrides := default dict .Values.tenantOverrides -}}
{{- $tenant := get $overrides $team | default $.Values.tenant }}
```

Verified by rendering: with overrides set, `payments` and `fraud` both move to a
`payments` tenant while `platform` stays put, so the mapping is many-to-one.

On the ruler side nothing changes. Mimir's local rule store lists tenants with
`os.ReadDir(root)` and treats **every directory under the rule path as a tenant**
(`pkg/ruler/rulestore/local/local.go:44`). The sidecar creating a new directory is
all it takes. No pod restart, no config change, `FOLDER` unchanged.

It is not instant: `-ruler.poll-interval` defaults to **10 minutes**
(`pkg/ruler/ruler.go:210`), so expect up to that delay between merge and evaluation.

### What the move does not solve

Moving *rules* to a tenant is cheap. Moving *data* is a real migration. A tenant
only holds series if writers send `X-Scope-OrgID` for it, so collectors and their
pipelines must split too; Grafana needs a datasource per tenant; per-tenant limits
and overrides must exist or the new tenant inherits defaults. **An alert whose
rules moved to a tenant that has no data evaluates against nothing and fires
nothing, silently.**

The claim this spec makes is narrower than "tenancy is solved": the *rules half*
of a split is a two-line values change, and the ruler pod spec is permanently out
of its blast radius.

### Tenant per environment

Evaluated and deferred. Because one stack serves every environment, tenant-per-env
is a genuine isolation boundary (per-env limits and retention, a dev cardinality
spike unable to degrade prod queries) and is preferable to tenant-per-team, which
mostly buys tidiness. **Do not stack both axes**: three environments times fifteen
teams is forty-five tenants, each with its own limits, retention, and datasource.
Pick one primary axis and express the other as a label.

Cross-environment rules can remain possible under tenant-per-env, but federated
groups are skipped by default and this is not free. The backend must enable
**both** `ruler.tenant_federation.enabled` and the cross-tenant query federation
flag `tenant_federation.enabled`, and the rule group must declare
`source_tenants: ["staging", "prod"]`. The presence of
`RuleGroupDesc.SourceTenants` in the Mimir source does not by itself mean
federated rules work; treat these as backend changes to make only when
tenant-per-environment is actually implemented.

**Open question, deliberately deferred (see Section 14):** how a team declares
which environments a rule runs in. Deferring is free precisely because of the
canonical selector form in Section 4, which makes the environment set derivable
from the rules themselves at migration time. The ergonomics can be chosen later
with real rules in hand.

## 7. ArgoCD delivery

Three plain `Application` resources, one per target, living in the **existing
ArgoCD apps repo** following whatever convention is already there.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: observability-rules-mimir
  annotations:
    argocd.argoproj.io/manifest-generate-paths: .
spec:
  project: <existing project>
  source:
    repoURL: <this repo>
    targetRevision: main
    path: "."
    helm:
      releaseName: observability-rules-mimir
      values: |
        target: mimir
  destination:
    server: <in-cluster>
    namespace: <mimir ruler namespace>
  syncPolicy:
    automated: {prune: true, selfHeal: true}
```

The loki and prometheus Applications are identical but for `target` and the
destination namespace.

**The Applications override only `target`. They must not set `tenant`.**
`values.yaml`, including any future `tenantOverrides`, is the single source used
by both rendering and the Phase 2 live validation. An Application that overrode
the tenant would make CI validate against a different tenant from the one
receiving the rules, which is precisely the silent-null failure this design keeps
trying to eliminate.

**Plain Applications, not ApplicationSets.** The prior system used cluster
generators selecting on `platform`/`environment`/`class` cluster-secret labels
because it fanned out to many EKS clusters. There is one global stack here, so a
generator matching one destination is ceremony that hides the destination.

**No `CreateNamespace=true`.** The backends already run, so the namespaces exist.
Keeping the option would let a typo in the namespace field silently create an
empty namespace and report Synced while nothing reached a ruler.

**`prune: true` is load-bearing.** It is the first link in the delete chain. The
sidecar removes files on `DELETED` events and, on `MODIFIED`, diffs old against
new keys and removes files for keys that disappeared (verified in
`src/resources.py`: `_process_config_map(..., item_removed=True)` reaching
`remove_file()`). So deleting a rule file genuinely stops the alert firing.
Without pruning, the ConfigMap stays, the file stays, and the alert fires forever.

**No sync waves.** These are independent Applications and waves order resources
only within one. The real ordering dependency, backends before rules, is satisfied
by the backends already existing.

### Sidecar configuration (backend side, prerequisite)

| Target | `LABEL` / `LABEL_VALUE` | `FOLDER` | Reload |
| --- | --- | --- | --- |
| Mimir ruler | `rules` / `mimir` | `/tmp/rules` | polls, 10m default |
| Loki ruler | `rules` / `loki` | `/tmp/rules` | polls, 1m default |
| Meta Prometheus | `rules` / `prometheus` | rules dir | `REQ_URL` POST to `/-/reload` |

## 8. Meta-monitoring Prometheus

Alerts about Mimir must not be evaluated by Mimir. The prior system's
`rules/op/metrics/regional/mimir-alerts.yaml` held 60 rule definitions across 54
unique `Mimir*` alert names, including `MimirIngesterUnhealthy`,
`MimirRequestErrors`, `MimirFrontendQueriesStuck` and `MimirKVStoreFailure`, all
evaluated by Mimir's own ruler against Mimir's own storage. Its `prometheus-query`
component was not a hedge either: its entire config was a `remote_read` pointed at
the Mimir gateway, and it was pinned at `replicas: 0`, so it was both dependent on
Mimir and already switched off. The only real coverage was the deadman heartbeat
stopping, which can say "metrics alerting is dead" but never which component or why.

A dedicated Prometheus scrapes Mimir and Loki component pods directly, stores
locally with short retention, and evaluates `rules/<team>/prometheus/`.

**Prometheus needs an explicit reload.** Unlike both rulers, it re-reads
`rule_files` only on SIGHUP or `POST /-/reload`. Its sidecar sets
`REQ_URL: http://localhost:9090/-/reload` and `REQ_METHOD: POST`, and Prometheus
runs with `--web.enable-lifecycle`. Without this, rules land on disk and are never
loaded, which is indistinguishable from everything working.

**No tenant annotation for this target.** Prometheus has no tenants, so the
template omits `k8s-sidecar-target-directory` when `target` is `prometheus`.

**`prometheus/` is restricted to the platform team**, enforced in CI against a
single configured folder name (the platform team's own `rules/<team>/`, named
once in `scripts/check.sh` rather than assumed). The meta
Prometheus is valuable because it is small and independent; its failure mode is
becoming a second general-purpose ruler that drifts into depending on things.
Easy to relax later, hard to un-sprawl.

**Which alerts move is a per-alert judgement, not a mechanical migration.** Alerts
that matter while Mimir is broken (ingester health, KV store, request errors, a
stuck frontend) belong here. Slow-burn ones (latency trends, cardinality growth)
are better left in Mimir where the history and richer data live. Duplicating
everything doubles the pages.

## 9. Dashboards via Git Sync

`dashboards/<team>/` mirrors `rules/<team>/`, so a single CODEOWNERS entry covers
a team's rules and dashboards together, and Git Sync's directory-to-folder mapping
gives a Grafana folder per team without any extra configuration.

Grafana points a `Repository` resource at this repo with path `dashboards/`,
branch `main`, and:

```yaml
workflows: ["branch"]
```

Verified in Grafana source (`apps/provisioning/pkg/apis/provisioning/v0alpha1/types.go:60-69`):
`WriteWorkflow = "write"` allows direct commits, `BranchWorkflow = "branch"`
"creates a branch for changes", and an empty list means the repository is
read-only. Choosing `["branch"]` means a UI edit lands on a branch and returns as
a pull request, so **the branch protection and CODEOWNERS model in Section 11
applies to dashboards unchanged**. Authors keep the Grafana UI workflow and no
machine committer bypasses review.

The `provisioning` feature toggle that enables Git Sync is
`FeatureStageGeneralAvailability` with `Expression: "true"`, so it is GA and on by
default, but it carries `RequiresRestart: true`.

**ArgoCD is deliberately not involved.** Grafana pulls; there is no ConfigMap, no
sidecar, no prune semantics. Two consequences:

- The per-object delivery gap in Section 12 does not apply to dashboards. Their
  delivery is Grafana's own sync job, which reports success or failure directly.
- That sync job becomes the only signal, and it is not otherwise on anyone's
  radar. Alerting on Git Sync job failure is a prerequisite (Section 15), not an
  optional nicety.

Git Sync provisions **dashboards and folders only**. It has no alert-rule kinds,
so it cannot absorb the ruler pipeline and the two delivery paths stay separate by
necessity rather than by choice.

### Dashboard rules

- Every dashboard has a `uid`, unique across the repository.
- **A `uid` must never change silently.** CI diffs the uid set against the base
  branch. A changed uid orphans the existing dashboard and breaks every link,
  annotation and alert reference pointing at it, while looking in the diff like an
  ordinary edit. Changing one requires an explicit marker in the PR.
- Dashboard JSON must parse, and filenames follow the same
  `^[a-z0-9-]+\.json$` restriction as rule files.

## 10. Mixins

**One subfolder per mixin**, each self-contained with its own `jsonnetfile.json`,
`jsonnetfile.lock.json` and `mixin.libsonnet` holding `_config` overrides:
`mixins/mimir/`, `mixins/loki/`, `mixins/tempo/`, `mixins/kubernetes/`.

Vendoring per mixin rather than sharing one jsonnetfile at the root is what makes
version bumps independent: Renovate can raise a PR for the Loki mixin alone,
without dragging Mimir and Tempo into the same change and the same blast radius.
It also keeps one mixin's transitive jsonnet dependencies from silently
constraining another's.

A `make mixins` step renders each into the **existing** trees rather than a
parallel one:

```
mixins/<name>/  --render-->  rules/platform/<target>/mixin-<name>-alerts.yaml
                             dashboards/platform/mixin-<name>/*.json
```

Because output lands in `rules/` and `dashboards/`, mixin content inherits the
same delivery, the same CI, and the same ownership as hand-written content. There
is no second pipeline and no special case in the chart.

**Target routing follows Section 8's argument.** Observability-stack mixins
(mimir, loki, tempo) render into `rules/platform/prometheus/`, because a mixin
alerting on Mimir must not be evaluated by Mimir. Everything else
(kubernetes-mixin and similar) renders into `rules/platform/mimir/`. This rule
lives in the Makefile, not in someone's memory.

**Contract compliance by injection, not exemption.** The render step injects
`owner: platform` and configures each mixin's runbook URL through its own
`_config` where the mixin supports one, injecting a default where it does not.
Section 4 therefore stays universal with no exempt path, which matters because an
exemption for `mixins/` is precisely where non-compliant hand-written rules would
eventually accumulate.

**Alert-name collisions resolve in favour of the mixin.** Mixin alerts keep their
upstream names, since renaming them breaks the runbooks and dashboards that
reference them. A colliding hand-written alert gets renamed instead. CI reports
which is which.

**Generated files are committed and drift-checked.** They have to be: Git Sync
reads the repository and the Helm chart globs inside the chart directory, so
neither can consume a CI-only artifact. Each generated file carries a header
naming its mixin and version, CI re-renders and fails on any diff, and every
`mixins/<name>/jsonnetfile.lock.json` is committed so Renovate can bump each mixin
independently, the same way the prior system's Renovate bumped mirrored image tags.

**A coupling worth naming:** mixins emit recording rules that their own dashboards
then query. If the rules half fails to deploy while the dashboards half succeeds,
panels render empty with no error anywhere. This is another instance of the
silent-null failure this design keeps chasing, and it appears in the Section 12
table because the two halves travel by different delivery paths and can fail
independently.

## 11. CI gates

One entrypoint, `scripts/check.sh`, running identically on a laptop and in GitHub
Actions, so a contributor can reproduce a failure without pushing. Seven stages,
cheapest first.

1. **Structure.** Filename and directory-segment regexes, and generated
   name/key length. Files rejected outside `rules/<team>/<target>/**.yaml`;
   symlinks rejected. CODEOWNERS and folder sets agree in both directions.
   `owner` label equals team folder. Alert names unique repository-wide.
   Canonical environment matcher enforced by a regex over each rule's `expr`
   field only, never over whole-file text, so a matcher in a comment, an
   annotation or a summary cannot influence the result. **This is a deviation
   from the original AST requirement**; see the note in section 4 for the reason
   and for the bypass it has already cost. `prometheus/` only under the
   configured platform-team folder.
2. **Contract.** `promruval validate --config-file validation.yaml <paths...>`
   with explicit non-test file paths, carrying the prior system's four rules minus
   its hand-maintained owner list, which stage 1 now derives from the filesystem.
   Confirm during implementation whether the installed promruval needs a
   dialect flag to parse LogQL rules; do not assume the PromQL parser accepts them.
3. **Syntax.** `promtool check rules` for mimir and prometheus targets,
   `lokitool rules check` for loki.
4. **Unit tests.** `promtool test rules` over `*-tests.yaml`, failing if any test
   file was not executed, so a fixture cannot go stale unnoticed the way the
   prior system's three did. **Metrics only.**
   `lokitool rules` offers list, print, get, delete, load, diff, sync, prepare,
   format and check, but **no unit-test command**, so LogQL alert rules cannot be
   behaviourally tested. This asymmetry goes in the README so nobody assumes log
   alerts are covered.
5. **Render.** `helm template` per target, then assert against the rendered output:
   no duplicate ConfigMap names or data keys; every ConfigMap under 1MB; **every
   non-test rule file appears in exactly one rendered ConfigMap**; and the rule
   payloads extracted back out of the ConfigMaps still parse.
6. **Dashboards.** Every file under `dashboards/` parses as JSON, has a `uid`, and
   uids are unique repository-wide. **Diff the uid set against the base branch and
   fail on any uid that changed**, since that orphans the live dashboard while
   looking like an ordinary edit. Filenames match `^[a-z0-9-]+\.json$`.
7. **Mixin drift.** Re-run `make mixins` and fail on any diff, so a hand-edit to
   generated output cannot survive. This is the prior system's SLO drift check
   applied to a much larger generated surface. Also assert that every generated
   file carries its mixin-and-version header, and that `jsonnetfile.lock.json` is
   committed.

The last two checks are the ones the prior system could not have had. Asserting
that every file appears in the output is what catches a file silently excluded
from the render, which is otherwise indistinguishable from a file that works, and
is exactly its `contains "tests"` bug. Re-parsing the *extracted* payload catches
template-induced corruption, since `nindent 4` will happily produce broken YAML
from a source file that validated fine; that system validated only sources.

The prior system shipped three `*-tests.yaml` files and its chart deliberately
excluded them from ConfigMaps, but no CI job ever invoked a test runner. Two
commits went as far as downloading `mimirtool` and never called it. The
convention was built and the runner never wired up. Stage 4 is that gap closed.

GitHub Actions: one workflow on `pull_request` and `push: main` running
`scripts/check.sh`, with every third-party action pinned by commit SHA and every
downloaded tool pinned by version and checksum. This job needs **no secrets and
no internal network access**, which is what keeps it safe to run against
PR-controlled code.

**Branch protection is the enforcement, CODEOWNERS is only routing.** Require the
CI status and at least one CODEOWNER approval, dismiss stale approvals, and block
force pushes. CODEOWNERS must assign the platform team to `Chart.yaml`,
`values.yaml`, `values.schema.json`, `templates/`, `validation.yaml`, `scripts/`,
`tests/`, `tools/`, `Makefile`, `requirements.txt` and `.github/`, in addition to
per-team rule folders. Without that, a team can approve its own change to the very
checks that govern it.

The test of membership is not whether a path looks like infrastructure but
whether editing it could change what passes. CI's only step is `make check`, so
the Makefile is the pipeline; `requirements.txt` chooses the Python that runs the
checks; `tools/checksums.txt` is the only thing between a pinned download and an
arbitrary binary; and `tests/` defines what passing means for everything in
`scripts/`. The last four were added on 2026-08-13 after the final review showed
they were absent.

**Ownership is checked per path, not per pattern string.** GitHub applies the
last CODEOWNERS pattern that matches a path, so a more specific pattern later in
the file silently overrides an earlier directory entry: `/scripts/ @org/platform`
followed by `/scripts/rulecheck.py @org/payments` hands that file to payments.
The check therefore compiles every pattern to a path regex and resolves each
governed path the way GitHub does. Patterns outside the supported anchored subset
are reported as unevaluatable rather than assumed safe.

**Optional, not day-one:** require a `-tests.yaml` companion for any alert with
`severity: critical`, targeting the alerts that will page someone without
demanding tests for every warning.

## 12. Failure modes and verification

A green PR and a green ArgoCD do not prove an alert works.

| Hop | Failure | Covered by |
| --- | --- | --- |
| Rule file authored | syntax, missing labels, wrong owner | CI 1-4 |
| Rendered to ConfigMap | not globbed, duplicate name or key, template corruption | CI 5 |
| ConfigMap applied | drift, manual edit | ArgoCD `selfHeal` |
| Sidecar writes file | crashed container, wrong label, wrong annotation | sidecar health alert; **per-object: not covered** |
| Ruler loads namespace | poll delay, rule rejected at load, stale content | ruler reload metric; **per-namespace: not covered** |
| Whole target path dead | sidecar, ruler, or notification path down | external deadman, one per target |
| Rule evaluates to data | metric absent, wrong tenant, selector mismatch | Phase 2, Mimir target only |
| Dashboard reaches Grafana | malformed JSON, Git Sync job failure | Git Sync job status; **alert on it (Section 15)** |
| Dashboard survives an edit | `uid` changed, orphaning the live dashboard | CI 6, uid diff against base branch |
| Mixin output matches source | generated file hand-edited, stale render | CI 7, re-render and diff |
| Mixin rules and dashboards agree | recording rules undeployed while dashboards deployed | **not covered** |

The last row is a genuinely new failure mode introduced by having two delivery
paths. A mixin's dashboards query recording rules the same mixin emits, but the
two halves travel separately: rules through ArgoCD and the sidecar, dashboards
through Git Sync. If the rules half fails while the dashboards half succeeds, the
panels render empty with no error in Grafana, in ArgoCD, or in CI. Detecting it
properly needs the reconciler declined below; until then it is a known hazard
worth mentioning in the runbook for "mixin dashboard is blank".

### Known gap, accepted deliberately

**A deadman proves only that its own ConfigMap and its target's notification path
work.** It does not prove that some other ConfigMap was written, that an edit
replaced the previous content of an existing namespace, or that one namespace
failed to load while the deadman's namespace stayed healthy. A single object
silently missing or stale can coexist indefinitely with a green deadman, a green
ArgoCD, and a green CI run.

Closing that gap properly needs a reconciler that compares the rendered ConfigMap
set against what the ruler APIs actually report. That is deliberately **not** in
scope: it is a component to build and operate, and the risk it addresses is a
partial delivery failure rather than a total one. This is a conscious trade, not
an oversight, and it should be revisited if partial delivery failures are ever
observed in practice.

Two cheap partial mitigations are in scope, because they need no new component:

- Alert on sidecar container unavailability per target.
- Alert on the rulers' own config-reload metrics, including a stale
  last-successful-reload timestamp.

**Also until Phase 2 exists, an alert referencing a metric that does not exist
passes every gate and simply never fires**, and Phase 2 covers only the Mimir
target. Equivalent live validation for Loki and the meta Prometheus is a separate
follow-up that this spec does not design.

### Acceptance test

Not "CI is green", but end to end:

1. Merge an always-firing test alert routed to a test receiver.
2. Confirm it appears in the ruler within the poll interval, and fires.
3. Delete the file; confirm the ConfigMap is pruned and the file removed, then
   confirm the alert resolves, allowing for Alertmanager's resolve timeout rather
   than expecting an immediate resolved notification.
4. Scale Mimir's ruler to zero, **first disabling ArgoCD self-heal for that
   Application or the replica count will be restored under you**, and confirm the
   Mimir-down alert still fires from the meta Prometheus and reaches Alertmanager
   by a path that does not touch Mimir.

Step 4's final clause matters: routing meta alerts through anything
Mimir-dependent quietly restores the circular dependency being removed.

## 13. Phase 2: live validation against Mimir

promruval supports three validations requiring a live connection:

| Validation | Catches | Params |
| --- | --- | --- |
| `expressionSelectorsMatchesAnything` | metric name typos, selectors matching no data | `maximumMatchingSeries` |
| `expressionUsesExistingLabels` | label name typos | none |
| `expressionCanBeEvaluated` | query errors, cardinality bombs before the ruler evaluates them | `timeSeriesLimit`, `evaluationDurationLimit` |

Mimir is addressed by header:

```yaml
prometheus:
  url: http://<mimir-gateway>/prometheus
  httpHeaders:
    X-Scope-OrgID: <tenant>
  timeout: 30s
  cacheFile: .promruval_cache.json
  maxCacheAge: 1h
  queryLookback: 20m
```

`expressionCanBeEvaluated` with `timeSeriesLimit` is arguably the bigger win over
typo-catching: it stops a rule that would return a hundred thousand series before
the ruler evaluates it every interval forever.

This phase covers **`rules/*/mimir/` only.** Loki and meta-Prometheus rules would
need their own endpoints and LogQL-aware checks, which this spec does not design.

Note these parameters are detection thresholds, not protective limits:
`expressionCanBeEvaluated` runs the whole expression and only then counts series
and measures elapsed time. The Mimir endpoint must therefore enforce its own
per-tenant query timeout, max samples, and concurrency limits **before** CI is
allowed to call it. A single tenant means one expensive rule has stack-wide blast
radius.

**Why it is Phase 2:** CI needs a network path to Mimir. GitHub-hosted runners
cannot reach an internal gateway, so this requires a self-hosted runner or a
read-only endpoint reachable from Actions. That is an infrastructure decision and
must not block the other five stages.

### CI trust boundary

This is the part to get right, because the obvious implementation is a serious
vulnerability. **Do not run `scripts/check.sh`, or any other PR-controlled code,
on a runner that can reach Mimir.** Doing so gives anyone who can open a pull
request arbitrary code execution inside the network.

The static workflow stays as it is: PR-controlled code, no secrets, no internal
access. The live-validation workflow is separate and must take its workflow
definition from the protected default branch, must not check out or execute
PR-controlled scripts, actions or binaries, and should fetch only the candidate
`rules/*/mimir/**/*.yaml` blobs through the GitHub API before handing them to a
pinned promruval binary. Do not use `pull_request_target` combined with checking
out the PR head. Any self-hosted runner must be ephemeral, dedicated, hold no
cloud-instance credentials, and use a read-only tenant-scoped Mimir credential.

The promruval cache lives in workflow scratch space, is never committed or
uploaded as an artifact, and is keyed by Mimir URL, tenant, tool version and
config hash.

### Exemptions

Alerts use the annotation `disabled_validation_rules: <rule-name>` (configurable
via `customExcludeAnnotation`), because an annotation lands in the PR diff where a
reviewer sees it. Legitimate cases include a service being instrumented in the
same PR and metrics that only appear during an incident.

**Recording rules have no annotations field**, so they cannot use that mechanism.
They use an immediately preceding rule-level comment instead:

```yaml
# ignore_validations: <validation-rule-name>
# validation_exemption_reason: <why>
- record: ...
```

CI rejects file-level and group-level exemptions, unknown validation names, and
any exemption lacking a reason comment.

**Run it non-blocking first**, reporting without failing PRs, until the
false-positive rate on real data is known. Promote to required once quiet.

**One duplication to avoid:** the `X-Scope-OrgID` here and `tenant` in
`values.yaml` must agree, or CI validates against a tenant the rules never reach.
`scripts/check.sh` reads the tenant from `values.yaml` and injects the header, so
there is one source of truth.

## 14. Open questions

**How a team declares which environments a rule runs in.** Deferred until the
tenant-per-environment split is actually wanted. Options considered, with the
constraint that whatever is chosen must let the environment set be derived
mechanically:

- Flat file means all environments, an `<env>/` subfolder means that environment
  only. No new file format, zero ceremony in the common case. A subset such as
  staging-and-prod-but-not-dev needs the file in two folders.
- Filename suffix carries the set (`checkout-alerts.staging-prod.yaml`). Handles
  subsets without duplication, at the cost of a parsing convention and noisier
  filenames.
- Always explicit, one env folder per rule. Nothing implicit, but the common
  all-environments rule gets duplicated.
- Uniform fan-out with no scoping. Simplest template; noisy dev alerts for rules
  that only make sense in prod.

The canonical selector form in Section 4 is what makes deferring free.

## 15. Prerequisites (backend, outside this repo)

A sidecar writing to `/tmp/rules` achieves nothing unless the ruler reads that
exact directory through a volume they both share. That wiring is the prerequisite,
not the sidecar alone.

### Mimir ruler

1. Configure local rule storage explicitly:
   ```yaml
   ruler_storage:
     backend: local
     local:
       directory: /tmp/rules
   ```
2. Mount the same writable `emptyDir` at `/tmp/rules` in **both** the ruler and
   sidecar containers, and confirm the pod security context lets both create,
   replace and delete tenant directories and files.
3. `k8s-sidecar` pinned by digest: `METHOD=WATCH`, `LABEL=rules`,
   `LABEL_VALUE=mimir`, `FOLDER=/tmp/rules` (**without** the tenant, which now
   comes from the annotation), `NAMESPACE` set to the pod namespace, unique-filename
   rewriting disabled. Namespace-scoped read-only ConfigMap RBAC.
4. Lower `-ruler.poll-interval` from the 10 minute default toward Loki's 1 minute,
   so feedback latency after merge is comparable across targets.
5. Alert on sidecar unavailability and `cortex_ruler_config_last_reload_successful == 0`.

### Loki ruler

1. Configure local rule storage explicitly:
   ```yaml
   ruler:
     storage:
       type: local
       local:
         directory: /tmp/rules
   ```
2. Same shared-volume, permissions, digest-pinning and namespace-scoped RBAC
   requirements as Mimir, with `LABEL_VALUE=loki`.
3. Alert on sidecar unavailability and Loki's ruler config-reload metrics.

### Meta Prometheus

1. Direct pod or service-endpoint scrapes of Mimir and Loki components, local TSDB
   with short retention, and an Alertmanager path that does not traverse Mimir.
2. Configure the rule path explicitly, matching what the sidecar writes:
   ```yaml
   rule_files:
     - /tmp/rules/*.yaml
   ```
3. Shared writable `emptyDir` at `/tmp/rules`, and `--web.enable-lifecycle`.
4. Sidecar with `LABEL_VALUE=prometheus`, `REQ_URL=http://localhost:9090/-/reload`,
   `REQ_METHOD=POST`.
5. Alert on sidecar unavailability, `prometheus_config_last_reload_successful == 0`,
   and a stale successful-reload timestamp.

### Grafana (dashboards)

1. Run Grafana with the `provisioning` feature toggle. It is GA and enabled by
   default, but `RequiresRestart: true`, so confirm it is actually active.
2. Create a `Repository` resource pointing at this repo, path `dashboards/`,
   branch `main`, `workflows: ["branch"]`, with a GitHub credential scoped to this
   repository alone.
3. **Alert on Git Sync job failure.** This is the only delivery signal dashboards
   have, and nothing else in this design watches it.
4. Decide how a Grafana-opened branch becomes a PR and who reviews it, so UI edits
   do not accumulate as abandoned branches.

### Cross-target

1. Enforce backend per-tenant query time, sample, and concurrency limits before
   granting repository write access broadly.
2. Pin Helm, promruval, promtool, lokitool and k8s-sidecar to tested versions or
   digests and record their checksums. The prior system ran sidecar 1.23.1 and
   1.24.3 while its artifact repo had mirrored 1.25.3; the delete behaviour cited
   in Appendix A was read from current upstream and should be confirmed against
   whichever version is actually pinned.
3. For Phase 2 only: the constrained CI path described in Section 13.

### Suggested ordering

Do not make the meta Prometheus, two backend ruler changes, three ArgoCD
Applications, the repository itself, and Phase 2 networking one atomic rollout.
Four separately reviewable workstreams:

1. Backend local rule storage, shared volumes, sidecars, reload alerts.
2. Repository chart, `scripts/check.sh`, static CI, CODEOWNERS, branch protection, canaries.
3. ArgoCD Applications and the end-to-end acceptance test.
4. Dashboards: Git Sync repository, `dashboards/<team>/`, uid checks, sync-failure alert.
5. Mixins: one subfolder at a time, starting with the one whose alerts you most
   want (probably mimir), rendering into the trees the earlier stages already
   deliver. Adding a mixin is then a contained change rather than a new pipeline.
6. Meta Prometheus, then Phase 2 live validation once the rest is stable.

Stages 4 and 5 are genuinely independent of 1 through 3, since dashboards never
touch ArgoCD. They can run in parallel with a different pair of hands.

## Appendix A: evidence

Findings from the archived ***REMOVED*** repositories and from upstream source, each
of which motivated a decision above.

Five rows were wrong in the first draft and were corrected after external review
re-derived them from the archives. The corrections are noted inline, because a
spec that cites evidence should show where its evidence was sloppy.

| Finding | Evidence |
| --- | --- |
| The `global` rule tier was abandoned, not never used | the current tree holds only `.gitkeep`, but history contains `rules/files/op/metrics/global/alerts.yaml` and a test-team equivalent. **Corrected:** the first draft claimed it was never used across 413 commits |
| Unit tests were never run | 3 `*-tests.yaml` files exist and no CI job ever invoked a test runner. **Corrected:** two commits did add a `mimirtool` download, so "no reference in CI" was true only of the final state |
| Test-exclusion bug | template used `contains "tests"`, not a suffix match |
| Owner allow-list drifted from reality | 18 values hand-maintained in `validation.yaml`, unconnected to folders |
| Environment selector sprawl | 73 of 79 rule files, **210** matchers in deployable rules, 8 distinct shapes including `!="dev"`. **Corrected:** the first draft said 229, which counted 19 more occurrences inside test fixtures |
| Tenant hardcoded and unowned | `/tmp/rules/***REMOVED***` in 4 backend values files; 0 tenant commits in the rules repo |
| Mimir alerts were self-referential | **60** `Mimir*` rule definitions, 54 unique alert names, evaluated by Mimir's own ruler. **Corrected:** the first draft said 10, a number taken from `head`-truncated grep output and mistaken for a count |
| `prometheus-query` was not a hedge | its whole config is one `remote_read` at the Mimir gateway. **Corrected:** it was also pinned at `replicas: 0`, so it was already switched off and could not have hedged anything |
| Helm glob semantics | verified on Helm v4.2.3: `*` does not cross `/`, `**` does |
| Sidecar tenant override | `FOLDER_ANNOTATION` defaults to `k8s-sidecar-target-directory`; value may be relative |
| Sidecar delete path | `src/resources.py`, `_process_config_map(..., item_removed=True)` to `remove_file()` |
| Mimir tenant discovery | `pkg/ruler/rulestore/local/local.go:44`, `ListAllUsers` = `os.ReadDir(root)` |
| Mimir poll interval | `pkg/ruler/ruler.go:210`, default 10m |
| Loki poll interval | `pkg/ruler/base/ruler.go:169`, default 1m |
| Mimir ruler federation exists | `ruler.tenant_federation` config, `RuleGroupDesc.SourceTenants` |
| No LogQL unit tests | `lokitool rules` has no test subcommand |
| Git Sync is GA, not experimental | `provisioning` toggle, `FeatureStageGeneralAvailability`, `Expression: "true"`, `RequiresRestart: true` |
| Git Sync workflow modes | `types.go:60-69`: `WriteWorkflow = "write"` (direct), `BranchWorkflow = "branch"` (creates a branch); empty list means read-only |
| Git Sync scope | provisions Dashboard kinds and folders only; no alert-rule kinds, so it cannot replace the ruler pipeline |

Upstream versions inspected: Mimir `2fba38ee3f` (mimir-distributed 6.2.0-weekly.407),
Loki `1657a04339`, Helm v4.2.3.
