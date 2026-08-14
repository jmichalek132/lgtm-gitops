# Destructive Cutover Runbook

> **This is NOT an implementation plan and MUST NOT be executed by a subagent.**
> It rewrites all history and deletes a remote. It is performed once, attended,
> by the operator, with an explicit go/no-go at each gate. An agent may prepare
> commands and read output; the operator runs every step that destroys or
> publishes.

**Goal:** replace the private remote with a sanitised public repository whose history contains no private term, then install branch protection.

**Spec:** `docs/superpowers/specs/2026-08-14-publishable-example-design.md` section 6.

**Prerequisites, all of which must be true before starting:**

- The publishability-gates plan is complete and both gates pass.
- The second-team fixture plan is complete.
- The document-rewrite plan is complete and Gate 2 reports `ok` on the working tree.
- Content is **frozen**. No further commits until the cutover finishes or is abandoned.
- The operator holds the denylist outside the repository and has never committed it.

## Confidentiality

Every artifact this runbook produces is confidential: the freeze report, the ref inventory, the replacement file. They live outside the repository, are never committed, and are never attached to a public issue, pull request or CI job.

Redaction in the freeze report prevents direct emission of matching characters. It does **not** prevent inference from the existence, count, order or grouping of findings when the tree is otherwise known. Treat the report as sensitive on that basis, not as publishable because paths are redacted.

---

## Gate 0: obtain the delete_repo scope

The current token carries `gist`, `read:org` and `repo`. Deleting a repository needs `delete_repo`, and acquiring it is interactive.

**Operator runs, in their own terminal:**

```bash
gh auth refresh -h github.com -s delete_repo
```

Verify:

```bash
gh auth status
```

Expected: the scope list now includes `delete_repo`.

**If the scope cannot be obtained, STOP.** Do not fall back to a force-push. A force-push leaves the old objects reachable by SHA through the API, which is the exposure this cutover exists to remove. Report the blockage and abandon the cutover until the scope is available.

---

## Gate 1: freeze report

Run in a fresh mirror-like clone under the session scratchpad, never in the development working tree.

- [ ] **Push the freeze commit to the private remote, and confirm the tree is clean**

```bash
git status --porcelain          # expect: empty
git push origin main
git rev-parse HEAD              # record this: it is the freeze commit
```

- [ ] **Record the complete advertised ref inventory**

```bash
gh api repos/jmichalek132/lgtm-gitops --jq .private   # expect: true
git ls-remote --refs origin > "$SCRATCH/ref-inventory.txt"
cat "$SCRATCH/ref-inventory.txt"
```

`$SCRATCH` is a directory outside the repository. The inventory is a private cutover artifact.

- [ ] **Clone and fetch every advertised ref into an isolated namespace**

```bash
git clone --mirror git@github.com:jmichalek132/lgtm-gitops.git "$SCRATCH/cutover.git"
cd "$SCRATCH/cutover.git"
git ls-remote --refs . > "$SCRATCH/ref-inventory-local.txt"
diff "$SCRATCH/ref-inventory.txt" "$SCRATCH/ref-inventory-local.txt"
```

Expected: no difference. **A ref that cannot be fetched, a missing object, or a changed remote inventory is a no-go.**

- [ ] **Run the freeze scan over every surface**

The freeze scan reuses Gate 2's exact normalisation, decoder and deletion-closure algorithm. It covers:

- the frozen tracked and non-ignored untracked working tree
- every commit, annotated tag and tree reachable from every fetched ref
- every historical tree-entry path, and each reachable blob object at least once
- commit and annotated-tag messages, and author, committer and tagger identity fields
- every ref name
- `.gitmodules` as an ordinary blob, and every gitlink path, mode and target object id
- every reachable Git LFS pointer, with all referenced objects fetched, verified present, and scanned

A malformed object, missing LFS object, unreadable object, NUL-containing textual surface or non-UTF-8 textual surface is a **no-go**, never a skipped candidate.

Note: `git log -S` is pickaxe history *selection*, not an exhaustive scan of reachable blobs. It is not sufficient and must not be substituted.

- [ ] **Classify every match by surface**

The freeze report classifies each match as: blob content, commit or tag message, repository path, ref name, identity, `.gitmodules`, gitlink, or Git LFS object.

**GO/NO-GO.** Proceed to Gate 2 only if every match is in **blob content** or **commit or annotated-tag messages**. A match in a path, ref name, identity, `.gitmodules`, gitlink or LFS object is an automatic no-go: the baseline rewrite command cannot change those surfaces, and a separate reviewed transformation must be added to this runbook first. No remote deletion or visibility change happens while such a match is unresolved.

---

## Gate 2: rewrite

- [ ] **Verify the tool**

```bash
command -v git-filter-repo
git filter-repo --version
```

Expected: version 2.47.0, discovered on `PATH`. Do not hardcode a Homebrew prefix.

- [ ] **Write the replacement file outside the repository**

```bash
$EDITOR "$SCRATCH/replacements.txt"
```

It must cover the exact byte spellings present in the classified blob and message findings. Format is `literal==>replacement`, one per line.

**This file contains plaintext private terms.** It lives only in `$SCRATCH`, is never committed, and is deleted after verification.

- [ ] **Run the rewrite with all three options**

```bash
cd "$SCRATCH/cutover.git"
git filter-repo \
  --replace-text "$SCRATCH/replacements.txt" \
  --replace-message "$SCRATCH/replacements.txt" \
  --sensitive-data-removal
```

All three are required. `--replace-text` handles blob bytes and does **not** touch commit messages; `--replace-message` handles messages; `--sensitive-data-removal` fetches all fetchable refs and reports LFS and cleanup concerns. An earlier revision of the design claimed one flag did both, which would have shipped a rewrite leaving a term in a message.

- [ ] **Rerun the complete freeze scan**

Because the scanner uses NFKC, case folding and decoded views while `git filter-repo` replaces raw bytes, **the command flags are not proof of removal.** Rerun the full enumeration and matching procedure from Gate 1.

**GO/NO-GO.** Any remaining match is a no-go. Amend the replacement file and rewrite again from a fresh mirror clone.

---

## Gate 3: remote cutover

- [ ] **Confirm nothing of value will be lost**

```bash
gh api repos/jmichalek132/lgtm-gitops --jq '{issues: .open_issues_count, forks: .forks_count, stars: .stargazers_count}'
gh pr list --repo jmichalek132/lgtm-gitops --state all
```

Expected: zero issues, forks, stars and pull requests. If any exist, stop and decide explicitly what happens to them; this runbook assumes none.

- [ ] **Delete and recreate**

Deletion is not erasure: GitHub documents deleted repositories as restorable for 90 days. This reduces exposure rather than guaranteeing against it, and **the account holder must not restore the old repository.**

```bash
gh repo delete jmichalek132/lgtm-gitops --yes
gh repo create jmichalek132/lgtm-gitops --private
cd "$SCRATCH/cutover.git"
git remote add fresh git@github.com:jmichalek132/lgtm-gitops.git
git push --mirror fresh
```

Recreate it **private** first. Publication is the next gate, and it is separate so that a failed verification does not leave a public repository behind.

- [ ] **Verify from a fresh clone**

```bash
cd "$SCRATCH" && rm -rf verify && git clone git@github.com:jmichalek132/lgtm-gitops.git verify
cd verify
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" make check
```

Expected: exit 0, both gates clean, the only caveat being the deliberate unconfigured-ownership one.

Rerun the full freeze scan against this clone as well.

**GO/NO-GO.** Anything short of clean stops the cutover here, with the repository still private.

---

## Gate 4: publish and protect

Branch protection is unavailable while the repository is private: both `POST /repos/{owner}/{repo}/rulesets` and `PUT /repos/{owner}/{repo}/branches/main/protection` return HTTP 403, `"Upgrade to GitHub Pro or make this repository public to enable this feature."` Publication is therefore what makes protection possible, and protection must follow immediately.

- [ ] **Make it public**

```bash
gh repo edit jmichalek132/lgtm-gitops --visibility public --accept-visibility-change-consequences
```

- [ ] **Install the ruleset**

```bash
gh api -X POST repos/jmichalek132/lgtm-gitops/rulesets --input "$SCRATCH/ruleset.json"
```

Where `ruleset.json` requires: pull requests, the `public-checks` status context, dismissal of stale approvals, an up-to-date branch, and blocked force pushes and deletion. CODEOWNER approval is required only once the repository names owners GitHub can resolve; until `ownership.yaml` is configured with a real organisation, requiring it would block every merge on reviewers who do not exist.

Note the context is `public-checks`, not `check`. The CI job was renamed to state what it actually verifies, and a required check pointing at a job name that no longer exists silently enforces nothing.

- [ ] **Read the ruleset back**

```bash
gh api repos/jmichalek132/lgtm-gitops/rulesets --jq '.[].name'
gh api repos/jmichalek132/lgtm-gitops/rulesets/<id> --jq '.rules'
```

Expected: the rules as written. Updating `docs/branch-protection.md` is not acceptance; reading the live ruleset back is.

- [ ] **Run the negative test PR**

Open a pull request containing a harmless synthetic Gate 1 failure, for example a line matching the `macos-home-path` pattern in a scratch file. Confirm **all three**:

1. the `public-checks` job reports a failed required check
2. the merge button is blocked in the UI
3. an API merge attempt is refused without bypass:

```bash
gh api -X PUT repos/jmichalek132/lgtm-gitops/pulls/<n>/merge  # expect: refused
```

Then close the pull request and delete its branch.

**If protection cannot be installed or verified, make the repository private again immediately:**

```bash
gh repo edit jmichalek132/lgtm-gitops --visibility private --accept-visibility-change-consequences
```

Without protection, a pull request can modify the scanner, its configuration, the Makefile and the workflow in the same commit that adds forbidden content, and the required check then runs the contributor's version of itself.

---

## Gate 5: clean up

- [ ] **Destroy the confidential artifacts**

```bash
rm -rf "$SCRATCH/cutover.git" "$SCRATCH/verify" "$SCRATCH/replacements.txt"
```

Keep the freeze report and ref inventory only if they are stored somewhere already trusted with the denylist. Otherwise remove them too.

- [ ] **Record the outcome**

In the repository, note the cutover date and the fact that history was rewritten. Do **not** record what was removed, which files matched, or how many findings there were.

---

## Standing obligations after cutover

- **No outside pull requests.** There is no CI enforcement of private terms, because CI runs contributor-controlled code and must not hold the denylist. Until the trusted workflow described in spec section 9 exists, pull requests from outside contributors must not be merged. This is a hard gate, not a preference.
- **Do not restore the deleted repository** during the 90-day window.
- **`CI=true` is not authentication.** The named skip stops an absent denylist from becoming an accidental green run; it cannot stop a deliberate local bypass.
