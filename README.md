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
   - a name unique across the whole repository
3. If the alert is environment-specific, use exactly this form:

   ```promql
   deployment_environment=~"staging|prod"
   ```

   No matcher at all means all environments. Values come from `dev`, `staging`,
   `prod`, in that order, no duplicates, no negation, no plain `=`.
4. Run `make check` before pushing. CI runs the same *command*, but not with
   the same coverage: **CI cannot run the private term scan** (Gate 2), so a
   green `public-checks` is not evidence that your change was privately
   scanned. See [Publishability gates](#publishability-gates). CI also does
   not run the same tool *versions* unless you pin them yourself, see
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

## Publishability gates

This repository is meant to be publishable, so two checks guard against
committing something that should stay private. Both run as part of
`make check`; neither is optional.

**Gate 1** (`scripts/rulecheck.py`'s `publishability` check, configured in
[`publishability.yaml`](publishability.yaml)) scans every tracked and
untracked file for a small set of readable, non-secret patterns, currently a
macOS or Linux home directory path. It has **no self-exemption**:
`publishability.yaml` is scanned like any other file, which is why its own
regexes write the leading slash as `[/]` rather than `/`. A pattern written
the naive way would match the line that defines it, and an earlier revision
of this design used that as an excuse to skip scanning the file; there is no
such excuse now. Gate 1 has no secret input, so it runs everywhere, including
CI.

**Gate 2** (`scripts/privatescan.py`) scans the same files for actual private
terms: former internal codenames, personal names, anything that must never
appear in the published history at all. Its denylist is **never** part of
this repository. It lives on disk outside the checkout, at whatever path
`PUBLISHABILITY_TERMS_FILE` points to, and nothing derived from a term in it
is ever committed here, not the term, not a hash, not a length. A finding
names only the denylist entry's opaque id (`private-term-01`, never a
descriptive name) and where it was found; the term itself, the matched text
and any parser detail that could embed either are never printed. Treat every
line Gate 2 prints, including its raw findings and any diagnostics before a
denylist has loaded, as confidential: pipe it somewhere private, and don't
paste it into a public issue or chat while asking for help.

To run it locally:

```bash
export PUBLISHABILITY_TERMS_FILE=/path/outside/this/repository/denylist.yaml
make check
```

Gate 2 is **fail-closed**: an absent, unreadable or malformed denylist is a
failure, not a skip. That is deliberate. A skip that quietly passed would
turn "I forgot to set `PUBLISHABILITY_TERMS_FILE`" into a green build, which
defeats the point of a gate that exists to catch exactly that kind of
mistake.

There is exactly one permitted way to skip it, and it exists solely because
CI must never hold the denylist: CI runs pull-request-controlled code, and a
secret available to `make check` in CI would be available to whatever a pull
request's own code does. Setting `PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci`
together with `CI=true` makes `scripts/check.sh` skip Gate 2 and end the run
with `CHECKS INCOMPLETE`, not `all checks passed`, naming the private scan as
something this run did not verify. `CI=true` is an ordinary environment
variable, not an authentication check: setting it by hand locally reproduces
the skip, which means the skip cannot be trusted to stop a deliberate bypass,
only an accidental one. Any other value of `PUBLISHABILITY_PRIVATE_SCAN`, or
the named skip without `CI=true`, is a hard failure.

The practical consequence: **a green `public-checks` run on a pull request is
not evidence that the change was privately scanned.** CI cannot run Gate 2 at
all. The only place Gate 2 actually runs is a contributor's own machine, with
their own `PUBLISHABILITY_TERMS_FILE` set, before they push. That is a
contributor obligation this repository asks of you, not something CI
automatically guarantees on your behalf. See section 9 of
[the design doc](docs/superpowers/specs/2026-08-14-publishable-example-design.md#9-what-this-does-not-make-safe)
for exactly what this does and does not make safe.

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
platforms: macOS arm64, Linux amd64 and arm64; anywhere else it refuses,
naming the exact tools and versions to install by hand. It is idempotent and
safe to re-run after a pin bump.

With no local toolchain at all, or to reproduce CI exactly:

```bash
make check-docker
```

builds an image holding the same pinned toolchain (installed by the same
`scripts/setup.sh`, verified against the same checksums) and runs the same
`make check` CI runs, against a bind mount of this repository. The private
denylist never enters the image: when `PUBLISHABILITY_TERMS_FILE` is set it
is bind-mounted read-only for the run; when unset, Gate 2 fails closed
inside the container exactly as on the host.

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
