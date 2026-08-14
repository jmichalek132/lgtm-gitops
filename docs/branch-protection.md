# Branch protection

CODEOWNERS on its own is review *routing*, not enforcement. Without the settings
below, a contributor can merge without review, including changes to the checks
that govern their own contributions.

Required settings on `main`:

- Require a pull request before merging.
- Require approval from Code Owners.
- Dismiss stale approvals when new commits are pushed.
- Require the `public-checks` status check to pass.
- Require branches to be up to date before merging.
- Block force pushes and branch deletion.
- Restrict who can bypass these settings to the platform team.

The `public-checks` status check is produced by `.github/workflows/ci.yaml`. If
that workflow is renamed, update the required check or protection silently
stops enforcing anything.

**`public-checks` is not private-term enforcement.** CI runs pull-request-
controlled code and must not hold the denylist, so this job sets
`PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci` and `scripts/check.sh` reports
`CHECKS INCOMPLETE`, naming the private term scan as not performed, rather
than a clean bill of health. It still enforces everything else this
repository can check without a secret: structure, contract, ownership,
CODEOWNERS, environment matchers, dashboard identity, rule syntax and unit
tests, and Gate 1's public pattern check. Passing `public-checks` on a PR is
not evidence that the change was privately scanned; only a contributor's own
local `make check`, with `PUBLISHABILITY_TERMS_FILE` set and no skip, does
that. See `docs/superpowers/specs/2026-08-14-publishable-example-design.md`
section 9 for what this does and does not make safe.

Grafana Git Sync opens branches for dashboard edits made in the UI. Those
branches go through the same pull request and review path as any other change,
which is the reason `workflows: ["branch"]` was chosen over `["write"]`.
