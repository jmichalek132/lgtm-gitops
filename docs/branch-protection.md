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
