# Observability Rules Repository: Design

Date: 2026-08-10
Status: Approved, ready for implementation planning

## 1. Context

We run one global Mimir and one global Loki, self-hosted OSS. All environments
(dev, staging, prod) write to that single stack, separated today only by a
`deployment_environment` label, into a single tenant. ArgoCD is established and
the backends are deployed. This spec covers **only the alerting and recording
rules layer** that sits on top.

The design is a rebuild of "***REMOVED***", a working system at a previous employer,
whose repositories are archived locally at `~/git/old-work` for reference:

| Repo | Commits | What it was |
| --- | --- | --- |
| `***REMOVED***-alerting` | 413 | The Helm chart plus every team's rules |
| `argocd-op-apps` | 806 | ArgoCD Application/ApplicationSet definitions |
| `argocd-op-helm-charts` | 236 | Wrapper charts and per-env values for the backends |
| `***REMOVED***-alerts` | 2 | An abandoned proposal for a restructured rules repo |

***REMOVED*** worked. This design keeps its shape and fixes the specific things that
went wrong, each of which is cited with evidence in Appendix A.

### Out of scope

- SLOs (***REMOVED*** used pyrra; deliberately deferred)
- Alertmanager configuration and routing
- Deploying or configuring Mimir, Loki, or ArgoCD themselves
- Instrumentation and collector configuration

Where this spec depends on a backend change, it is listed in Section 11 as a
named prerequisite rather than assumed.

## 2. Goals

1. Teams self-serve alerting and recording rules through PRs into their own folder.
2. A rule that passes CI and syncs in ArgoCD is actually loaded by a ruler, or
   something tells us it is not.
3. Moving from one tenant to many is a values change, not a migration.
4. Alerts about the metrics stack keep working when the metrics stack is down.

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
├── validation.yaml                 # promruval contract
├── scripts/check.sh                # single CI entrypoint, runs identically locally
├── examples/
├── .github/
│   ├── CODEOWNERS
│   └── workflows/ci.yaml
└── README.md
```

Two structural differences from ***REMOVED***:

**The `regional`/`global` level is gone.** With a single global stack it has no
meaning. In ***REMOVED*** it was scaffolded everywhere and never used: `metrics/global`
contained only a `.gitkeep` across 413 commits.

**The second level names the evaluation target, not the signal.** ***REMOVED*** used
`metrics/` and `logs/`, which read as signals but always meant destinations
(Mimir ruler, Loki ruler). Once a third destination exists that is also metrics
(Section 8), the signal reading breaks. `mimir/`, `loki/`, `prometheus/` answers
the question actually asked when an alert is not firing: which ruler was supposed
to evaluate this. It also leaves room for Tempo or a second Prometheus without
another rename.

Subfolders below the target level are permitted for grouping, for example
`rules/payments/mimir/checkout/latency-alerts.yaml`.

### Naming

- Alerts: `<service>[-<type>]-alerts.yaml`
- Recording rules: `<service>-rules.yaml`
- Unit tests: `<service>-alerts-tests.yaml`
- Filenames must match `^[a-z0-9-]+\.yaml$`

***REMOVED*** silently rewrote `_` to `-` when deriving object names. A CI error is
better than a silent rename.

### Team list

**Folder names are the source of truth.** There is no team registry file. CI
enforces the consequences:

- Every `rules/<team>/` has a matching CODEOWNERS entry, and every CODEOWNERS
  rules entry has a folder. Neither can drift.
- Every alert's `owner` label equals its containing team folder.
- Adding a team is a folder plus a CODEOWNERS line. No chart or ArgoCD change.

## 4. The team contract

| Field | Requirement |
| --- | --- |
| label `severity` | one of `info`, `warning`, `error`, `critical` |
| label `owner` | equals the containing team folder |
| annotation `summary` | required (`message` and `description` accepted as aliases) |
| annotation `runbook_url` or `dashboard_url` | at least one required |

Alertmanager is out of scope, but `owner` is the label routing will key on later.
Making it unfakeable now is cheaper than reconciling it after eighty alerts exist.

### Environment selectors: one canonical form

While a single tenant holds every environment, an environment-specific rule must
filter in PromQL. That is unavoidable. What is avoidable is variation. CI requires
exactly:

```promql
deployment_environment=~"preprod|prod"
```

Regex match, double quotes, pipe-separated values drawn from a known list, no
negation, no plain `=`. If an expression contains the selector more than once,
every occurrence must be identical.

This is the single highest-leverage decision in the spec for goal 3. It makes the
environment set of every rule machine-readable **from the rule itself**, so a
future tenant split can parse the selector, emit the ConfigMap into exactly those
tenants, and delete the selector, with no team re-declaring anything and no file
moves. ***REMOVED*** accumulated 229 selectors in 8 different shapes including a lone
negation, which would have made the same migration an archaeology exercise.

## 5. The chart

`values.yaml`:

```yaml
target: mimir     # mimir | loki | prometheus
tenant: <name>    # ruler directory for mimir and loki; object-name prefix only for prometheus
```

`templates/configmaps.yaml`:

```gotemplate
{{- $target := .Values.target -}}
{{- range $path, $_ := .Files.Glob (printf "rules/*/%s/**.yaml" $target) }}
  {{- if not (hasSuffix "-tests.yaml" (base $path)) }}
    {{- $key    := $path | trimPrefix "rules/" | replace "/" "-" }}
    {{- $team   := index (splitList "/" $path) 1 }}
    {{- $tenant := $.Values.tenant }}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ printf "%s-%s" $tenant ($key | trimSuffix ".yaml") }}
  labels:
    rules: {{ $target }}
    team: {{ $team }}
    tenant: {{ $tenant }}
  {{- if ne $target "prometheus" }}
  annotations:
    k8s-sidecar-target-directory: {{ $tenant }}
  {{- end }}
data:
  {{ $key }}: |-
    {{- $.Files.Get $path | nindent 4 }}
  {{- end }}
{{- end }}
```

Four properties are load-bearing.

**The data key is the flattened path, not the bare filename.** The sidecar writes
each ConfigMap key as a file into the ruler's directory, and Mimir treats each
file as one rule namespace. A bare `http-alerts.yaml` key would let two teams
collide on one path and **silently overwrite each other in the ruler**, with both
PRs green and both ConfigMaps present. Flattening makes that impossible and gives
a 1:1:1 mapping between the git path, the ConfigMap name, and the namespace shown
in the ruler UI.

**`hasSuffix "-tests.yaml"`, not `contains "tests"`.** ***REMOVED*** used the latter,
so a legitimately named file such as `integration-tests-alerts.yaml` would have
been silently excluded from every ConfigMap and never deployed.

**The tenant is in the object name; the data key is not.** Each tenant has its own
ruler directory, so data keys need not be unique across tenants, only Kubernetes
object names must be. When tenants fan out, ConfigMap names gain a prefix and
multiply while **rule namespace names stay byte-identical**, so the ruler UI,
`namespace` labels on alerts, and any silences referencing them do not churn.

**`**` is required for the glob.** Verified against Helm v4.2.3: `*` does not
cross `/`, `**` does. This is what permits subfolders.

## 6. Tenancy

### Today

One tenant. `values.tenant` is set once and every ConfigMap carries
`k8s-sidecar-target-directory: <tenant>`, a **relative** path that the sidecar
resolves against its `FOLDER`. The sidecar is configured with `FOLDER: /tmp/rules`,
without the tenant.

Behaviour is identical to ***REMOVED***'s. The difference is where the tenant
decision lives: in the chart, per object, rather than in the ruler's pod spec,
per container. ***REMOVED*** hardcoded `/tmp/rules/***REMOVED***` into four backend values
files and its rules repo contains zero commits mentioning tenants across 413
commits, so any split would have required a ruler change discovered at the moment
of need.

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

Cross-environment rules remain possible under tenant-per-env: Mimir's ruler
supports federation (`ruler.tenant_federation` config, `SourceTenants` on
`RuleGroupDesc`), so a rule spanning preprod and prod becomes a federated rule
group rather than an impossibility.

**Open question, deliberately deferred (see Section 12):** how a team declares
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
        tenant: <tenant>
  destination:
    server: <in-cluster>
    namespace: <mimir ruler namespace>
  syncPolicy:
    automated: {prune: true, selfHeal: true}
```

The loki and prometheus Applications are identical but for `target` and the
destination namespace.

**Plain Applications, not ApplicationSets.** ***REMOVED*** used cluster generators
selecting on `platform`/`environment`/`class` cluster-secret labels because it
fanned out to many EKS clusters. There is one global stack here, so a generator
matching one destination is ceremony that hides the destination.

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

Alerts about Mimir must not be evaluated by Mimir. ***REMOVED***'s
`rules/op/metrics/regional/mimir-alerts.yaml` held `MimirIngesterUnhealthy`,
`MimirRequestErrors`, `MimirFrontendQueriesStuck`, `MimirKVStoreFailure` and six
more, all evaluated by Mimir's own ruler against Mimir's own storage. Its
`prometheus-query` component was not a hedge: its entire config was a `remote_read`
pointed at the Mimir gateway, so it went dark exactly when Mimir did. The only
real coverage was the deadman heartbeat stopping, which can say "metrics alerting
is dead" but never which component or why.

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

## 9. CI gates

One entrypoint, `scripts/check.sh`, running identically on a laptop and in GitHub
Actions, so a contributor can reproduce a failure without pushing. Five stages,
cheapest first.

1. **Structure.** Filename regex. CODEOWNERS and folder sets agree in both
   directions. `owner` label equals team folder. Canonical environment selector
   form, values from a known list, consistent within an expression.
   `prometheus/` only under the platform team.
2. **Contract.** `promruval validate --config-file validation.yaml`, carrying
   ***REMOVED***'s four rules minus its hand-maintained owner list, which stage 1 now
   derives from the filesystem.
3. **Syntax.** `promtool check rules` for mimir and prometheus targets,
   `lokitool rules check` for loki.
4. **Unit tests.** `promtool test rules` over `*-tests.yaml`. **Metrics only.**
   `lokitool rules` offers list, print, get, delete, load, diff, sync, prepare,
   format and check, but **no unit-test command**, so LogQL alert rules cannot be
   behaviourally tested. This asymmetry goes in the README so nobody assumes log
   alerts are covered.
5. **Render.** `helm template` per target, then assert against the rendered output:
   no duplicate ConfigMap names or data keys; every ConfigMap under 1MB; **every
   non-test rule file appears in exactly one rendered ConfigMap**; and the rule
   payloads extracted back out of the ConfigMaps still parse.

The last two checks are the ones ***REMOVED*** could not have had. Asserting that
every file appears in the output is what catches a file silently excluded from the
render, which is otherwise indistinguishable from a file that works, and is
exactly its `contains "tests"` bug. Re-parsing the *extracted* payload catches
template-induced corruption, since `nindent 4` will happily produce broken YAML
from a source file that validated fine; ***REMOVED*** validated only sources.

***REMOVED*** shipped three `*-tests.yaml` files and its chart deliberately excluded
them from ConfigMaps, but no `promtool`, `cortextool` or `mimirtool` reference
exists anywhere in its CI. The convention was built and the runner never wired up.
Stage 4 is that gap closed.

GitHub Actions: one workflow on `pull_request` and `push: main` running
`scripts/check.sh` with pinned tool versions, set as a required status check.
CODEOWNERS supplies per-team review. The machine enforces the contract, humans
review the judgement.

**Optional, not day-one:** require a `-tests.yaml` companion for any alert with
`severity: critical`, targeting the alerts that will page someone without
demanding tests for every warning.

## 10. Failure modes and verification

A green PR and a green ArgoCD do not prove an alert works.

| Hop | Failure | Covered by |
| --- | --- | --- |
| Rule file authored | syntax, missing labels, wrong owner | CI 1-4 |
| Rendered to ConfigMap | not globbed, duplicate key overwrite, template corruption | CI 5 |
| ConfigMap applied | drift, manual edit | ArgoCD `selfHeal` |
| Sidecar writes file | crashed container, wrong label, wrong annotation | **deadman only** |
| Ruler loads namespace | poll interval, rule rejected at load | **deadman only** |
| Rule evaluates to data | metric absent, wrong tenant, env selector mismatch | Phase 2 (Section 11) |

Two hops are covered only by the deadman canary, which is why it is not optional
decoration. It ships through the identical pipeline, so it shares a fate with
every other rule, and its heartbeat is observed from outside the cluster. One per
target, since the paths are independent and any can fail alone.

**Until Phase 2 exists, an alert referencing a metric that does not exist passes
every gate and simply never fires.** That is the most likely way a team ships a
useless alert, and no schema validation catches it.

### Acceptance test

Not "CI is green", but end to end:

1. Merge a deliberately-failing canary alert.
2. Confirm it appears in the ruler within the poll interval, and fires.
3. Delete the file; confirm the ConfigMap is pruned, the file removed, the alert resolves.
4. Scale Mimir's ruler to zero; confirm the Mimir-down alert still fires from the
   meta Prometheus, and reaches Alertmanager by a path that does not touch Mimir.

Step 4's final clause matters: routing meta alerts through anything
Mimir-dependent quietly restores the circular dependency being removed.

## 11. Phase 2: live validation against Mimir

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

**Why it is Phase 2:** CI needs a network path to Mimir. GitHub-hosted runners
cannot reach an internal gateway, so this requires a self-hosted runner or a
read-only endpoint reachable from Actions. That is an infrastructure decision and
must not block the other five stages.

**Exemptions** use the alert annotation `disabled_validation_rules: <rule-name>`
(configurable via `customExcludeAnnotation`) rather than promruval's YAML or
PromQL comment forms, because an annotation lands in the PR diff where a reviewer
sees it. Legitimate cases include a service being instrumented in the same PR and
metrics that only appear during an incident.

**Run it non-blocking first**, reporting without failing PRs, until the
false-positive rate on real data is known. Promote to required once quiet.

**One duplication to avoid:** the `X-Scope-OrgID` here and `tenant` in
`values.yaml` must agree, or CI validates against a tenant the rules never reach.
`scripts/check.sh` reads the tenant from `values.yaml` and injects the header, so
there is one source of truth.

## 12. Open questions

**How a team declares which environments a rule runs in.** Deferred until the
tenant-per-environment split is actually wanted. Options considered, with the
constraint that whatever is chosen must let the environment set be derived
mechanically:

- Flat file means all environments, an `<env>/` subfolder means that environment
  only. No new file format, zero ceremony in the common case. A subset such as
  preprod-and-prod-but-not-dev needs the file in two folders.
- Filename suffix carries the set (`checkout-alerts.preprod-prod.yaml`). Handles
  subsets without duplication, at the cost of a parsing convention and noisier
  filenames.
- Always explicit, one env folder per rule. Nothing implicit, but the common
  all-environments rule gets duplicated.
- Uniform fan-out with no scoping. Simplest template; noisy dev alerts for rules
  that only make sense in prod.

The canonical selector form in Section 4 is what makes deferring free.

## 13. Prerequisites (backend, outside this repo)

1. `k8s-sidecar` on the Mimir ruler: `LABEL=rules`, `LABEL_VALUE=mimir`,
   `FOLDER=/tmp/rules` (**without** the tenant, which now comes from the annotation).
2. Same on the Loki ruler with `LABEL_VALUE=loki`.
3. Lower Mimir's `-ruler.poll-interval` from its 10 minute default toward Loki's
   1 minute, so feedback latency after merge is comparable across targets.
4. Deploy the meta Prometheus: direct scrape of Mimir and Loki component pods,
   local TSDB with short retention, `--web.enable-lifecycle`, rules sidecar with
   `REQ_URL`, and an Alertmanager path independent of Mimir.
5. For Phase 2 only: a network path from CI to the Mimir query endpoint.

## Appendix A: evidence

Findings from the archived ***REMOVED*** repositories and from upstream source, each
of which motivated a decision above.

| Finding | Evidence |
| --- | --- |
| The `global` rule tier was never used | `rules/*/metrics/global/` contains only `.gitkeep` across 413 commits |
| Unit tests were never run | 3 `*-tests.yaml` files exist; no `promtool`/`cortextool`/`mimirtool` reference in CI |
| Test-exclusion bug | template used `contains "tests"`, not a suffix match |
| Owner allow-list drifted from reality | 18 values hand-maintained in `validation.yaml`, unconnected to folders |
| Environment selector sprawl | 73 of 79 rule files, 229 selectors, 8 distinct shapes including `!="dev"` |
| Tenant hardcoded and unowned | `/tmp/rules/***REMOVED***` in 4 backend values files; 0 tenant commits in the rules repo |
| Mimir alerts were self-referential | 10 `Mimir*` alerts evaluated by Mimir's own ruler |
| `prometheus-query` was not a hedge | its whole config is one `remote_read` at the Mimir gateway |
| Helm glob semantics | verified on Helm v4.2.3: `*` does not cross `/`, `**` does |
| Sidecar tenant override | `FOLDER_ANNOTATION` defaults to `k8s-sidecar-target-directory`; value may be relative |
| Sidecar delete path | `src/resources.py`, `_process_config_map(..., item_removed=True)` to `remove_file()` |
| Mimir tenant discovery | `pkg/ruler/rulestore/local/local.go:44`, `ListAllUsers` = `os.ReadDir(root)` |
| Mimir poll interval | `pkg/ruler/ruler.go:210`, default 10m |
| Loki poll interval | `pkg/ruler/base/ruler.go:169`, default 1m |
| Mimir ruler federation exists | `ruler.tenant_federation` config, `RuleGroupDesc.SourceTenants` |
| No LogQL unit tests | `lokitool rules` has no test subcommand |

Upstream versions inspected: Mimir `2fba38ee3f` (mimir-distributed 6.2.0-weekly.407),
Loki `1657a04339`, Helm v4.2.3.
