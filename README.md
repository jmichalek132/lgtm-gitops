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
4. Run `make check` before pushing. CI runs the same *commands*: its only step
   is `make check`, so nothing is checked in CI that you cannot run locally.
   It does not run the same tool *versions* unless you pin them yourself, see
   [Local setup](#local-setup).

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

This machine's Python is externally managed (PEP 668), so a bare `pip install`
fails. Create and activate a virtualenv first, then install into that:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# plus: helm, promtool, promruval, lokitool
make check
```

`make test` and `make check` both assume that venv is active (or `.venv/bin`
is first on `PATH`); they invoke `python3`/`pytest` directly and do not
activate it for you.

### Tool versions

CI runs the same commands as `make check`, but it pins its toolchain and your
machine almost certainly does not. The pins live in `.github/workflows/ci.yaml`
and are, at the time of writing:

| tool      | CI pin    | installed by                          |
| --------- | --------- | ------------------------------------- |
| helm      | `v3.16.3` | `azure/setup-helm`, pinned by SHA     |
| promtool  | `3.1.0`   | release tarball, checksum-verified    |
| promruval | `3.2.0`   | release tarball, checksum-verified    |
| lokitool  | `3.3.2`   | release zip, checksum-verified        |
| Python    | `3.12`    | `actions/setup-python`, pinned by SHA |

`.github/workflows/ci.yaml` is the source of truth; read the `env:` block there
rather than this table if the two disagree.

A version difference can change a result. Helm's `**` glob, which the rule
subfolder feature depends on, and promtool's PromQL parser, which decides
which expressions are even valid, are both version-sensitive. If a check
passes locally and fails in CI, compare versions first: `helm version --short`,
`promtool --version`, `promruval version`, `lokitool version`.
