# lgtm-gitops

Alerting rules, recording rules and dashboards for the global Mimir, Loki and
meta-Prometheus stack. Teams own their own folder; the platform team owns the
chart, the checks and CI.

Design: [`docs/superpowers/specs/2026-08-10-observability-rules-design.md`](docs/superpowers/specs/2026-08-10-observability-rules-design.md)

## First: configure ownership

**This repository ships unconfigured and, as shipped, enforces nothing.** Every
handle in `.github/CODEOWNERS` is under `@org`, a placeholder organisation that
does not exist on GitHub. GitHub silently ignores an owner it cannot resolve, so
a rule naming a non-existent team means **nobody** is required to review, while
`scripts/rulecheck.py` treats the same string as authoritative. `make check`
prints an `UNCONFIGURED` warning on every run until this is done.

1. Set `org` in [`ownership.yaml`](ownership.yaml) to your GitHub organisation,
   including the leading `@`.
2. Replace every `@org/...` handle in `.github/CODEOWNERS` to match, and create
   the teams on GitHub: `<org>/platform`, plus one team per folder under
   `rules/` and `dashboards/`.
3. Set `configured: true` in `ownership.yaml`.

`make check` **fails** if you set `configured: true` while `org` is still the
placeholder, or while any `@org/...` handle is left in CODEOWNERS. Claiming an
ownership you do not have is the one state these checks refuse.

Ownership rules that follow, all enforced by `make check`:

- a team owns its own `rules/<team>/` **and** its own `dashboards/<team>/`;
  both entries are required, and the two are one ownership boundary, so both
  folders must contain at least one committed file, because git does not track
  an empty directory
- the owning team must be the **sole** owner of them, because on GitHub any
  co-owner can approve alone
- the paths that govern the checks themselves (`scripts/`, `tests/`, `tools/`,
  `templates/`, `Makefile`, `ownership.yaml`, …) stay with the platform team

Onboarding a new team has a dead end worth knowing about in advance. Add both
CODEOWNERS entries but create only `rules/<team>/`, and `make check` fails with
`CODEOWNERS claims team '<team>' under /dashboards/ but no dashboards/<team>/
folder exists`. Delete the dashboards entry to silence that, and the next check
fails instead: every path under `dashboards/<team>/` now resolves to the default
owner, and the finding says it `must resolve to @org/<team>`. Creating an empty
`dashboards/<team>/` is not a way out either. It passes locally, because the
check looks at the working tree, and then fails in CI, which only ever sees what
git tracked. Add the team's first rule **and** its first dashboard in the same
commit as the two CODEOWNERS entries.

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

   Names need not be globally unique: routing, silences and queries always
   combine the name with other labels (`owner` at minimum), never the name
   alone.
3. If the alert is environment-specific, use exactly one of these forms:

   ```promql
   deployment_environment="prod"
   deployment_environment=~"staging|prod"
   ```

   `=` for exactly one environment, `=~` for two or more. No matcher at all
   means all environments. Values come from `dev`, `staging`, `prod`, in that
   order, no duplicates, no negation.
4. Run `make check` before pushing. CI runs the same command, but not the
   same tool *versions* unless you pin them yourself, see
   [Local setup](#local-setup).

Ideas and future work are tracked as GitHub issues, not as TODO comments or
uncommitted notes.

## Adding a dashboard

Put it at `dashboards/<your-team>/<name>.json`, or nested at any depth below
your team folder: `dashboards/<your-team>/<area>/.../<name>.json`. Nested
folders are supported; discovery is recursive, uid checks span the whole
tree, and CODEOWNERS ownership follows the team prefix down. Every directory
segment must be a lowercase DNS label of at most 63 bytes, the same rule as
under `rules/`, so the tree maps onto Grafana folder names. A dashboard
directly under `dashboards/` with no team folder is rejected.

Give it a stable `uid`. **Never change the `uid` of an existing dashboard**:
it orphans the live one and breaks every link and annotation pointing at it.
CI will catch this.

Dashboards reach Grafana through Git Sync, not ArgoCD. Editing in the Grafana UI
opens a branch, which you turn into a pull request as usual.

## Unit tests

`promtool test rules` runs any `<service>-alerts-tests.yaml` fixture next to the
rules it tests. **This works for `mimir` and `prometheus` targets only.**
`lokitool` has no unit-test command, so LogQL alerts cannot be behaviourally
tested. Do not assume log alerts are covered by tests.

## Local setup

```bash
make setup                 # venv, python deps, four pinned tools into .venv/bin
source .venv/bin/activate  # or put .venv/bin first on PATH
make check
```

`make setup` parses the tool pins out of `.github/workflows/ci.yaml` (the
source of truth, so nothing can drift), downloads this platform's binaries,
verifies them against the pinned checksums in `tools/checksums-local.txt`,
and installs everything into `.venv/bin`, touching no system path. Supported
platforms: macOS and Linux, amd64 and arm64 each; anywhere else it refuses,
naming the exact tools and versions to install by hand. It is idempotent and
safe to re-run after a pin bump.

With no local toolchain at all, or to reproduce CI exactly:

```bash
make check-docker
```

builds an image holding the same pinned toolchain (installed by the same
`scripts/setup.sh`, verified against the same checksums) and runs the same
`make check` CI runs, against a bind mount of this repository.

The manual path still works if you prefer it. This machine's Python may be
externally managed (PEP 668), so a bare `pip install` fails; create and
activate a virtualenv first, then install into that:

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

CI runs the same commands as `make check`. The pins live in
`.github/workflows/ci.yaml` and are, at the time of writing:

| tool      | CI pin    | installed by                          |
| --------- | --------- | ------------------------------------- |
| helm      | `v3.16.3` | `azure/setup-helm`, pinned by SHA     |
| promtool  | `3.1.0`   | release tarball, checksum-verified    |
| promruval | `3.2.0`   | release tarball, checksum-verified    |
| lokitool  | `3.3.2`   | release zip, checksum-verified        |
| Python    | `3.12`    | `actions/setup-python`, pinned by SHA |

`scripts/rulecheck.py` requires **Python 3.11 or newer** (`glob.glob`'s
`root_dir` needs 3.10, `include_hidden` needs 3.11).

`.github/workflows/ci.yaml` is the source of truth; read the `env:` block there
rather than this table if the two disagree.

A version difference can change a result. Helm's `**` glob, which the rule
subfolder feature depends on, and promtool's PromQL parser, which decides
which expressions are even valid, are both version-sensitive. If a check
passes locally and fails in CI, compare versions first: `helm version --short`,
`promtool --version`, `promruval version`, `lokitool version`.

## History

On 2026-09-02 this repository was published from a private predecessor after a
full history rewrite. Every commit hash changed on that date; the pre-rewrite
history is not public and is not restorable from this repository.

The two publishability gates that guarded that publication (a public
home-path pattern scan and a private denylist scan) were removed on
2026-11-05, once the one-time publication was done. Their design, plans and
the cutover runbook stay under `docs/superpowers/` as the record.
