# Making this repository publishable — design

**Goal:** make `lgtm-gitops` safe to publish as a reusable example, and keep it
safe, without losing what makes its design arguments convincing.

**Status:** design of record for workstream 2b. Workstream 2 (the repository
foundation) is complete and pushed to a private remote.

**Notation:** this document never writes a private term. `TERM-A` and `TERM-B`
stand for the two words being removed. That is not squeamishness — an earlier
revision of this spec discussed them by name, and the commit that fixed a bug
in this very design reintroduced one of them into the git history it was
written to clean. A document about removing a word is a place the word gets
written.

---

## 1. Why this exists

The repository was built as a rebuild of a working system at a previous
employer. That heritage is the reason nearly every rule in it exists, and it is
also the reason the repository cannot be published as it stands.

Two words must go: `TERM-A` (a former employer's name) and `TERM-B` (a former
internal codename). Both appear only under `docs/superpowers/`; the chart, the
scripts, the rules and the tests contain nothing private. Everything else a
reader might mistake for private detail is already placeholder material: `@org`
in CODEOWNERS, `runbooks.internal` and `grafana.internal` in rule annotations,
`platform` as the only team.

**No exposure counts appear in this document.** An earlier revision tabulated
them, and every number was stale within the hour: the tree gained occurrences
as this spec was written, the history gained a commit message, and a "39
commits" figure became 41. Counts are produced by the freeze report (section 6)
against the actual repository at the moment of the cutover, never copied into
prose.

So this is a small change whose risk is not size. It is that a one-time sweep
looks identical to a thorough one right up until someone greps the published
repository, and that the sweep's own paperwork is a place new occurrences
appear.

## 2. Structure

One workstream, four separately reviewed units. They are **not** one
implementation plan; the last one is not an implementation plan at all.

| Unit | Deliverable | Order |
| --- | --- | --- |
| Publishability gates | Public pattern check in `make check`; private term scanner with an out-of-repo denylist | First |
| Second-team fixture | `rules/payments/`, `dashboards/payments/`, CODEOWNERS, integration assertions | Independent PR, any time before the freeze |
| Document rewrite | Neutral prior-system framing across every affected tracked document | After the gates exist |
| Destructive cutover | Freeze report, history rewrite, remote recreation, public cutover, branch protection | Last, behind an explicit go/no-go |

The second-team fixture is ordinary work and does not belong in a
confidentiality change. The cutover is a **runbook with a human checkpoint**,
not a task a subagent executes unattended: it destroys history and deletes a
remote.

### Not in scope

**Renaming the chart.** The chart is `observability-rules`; the repository is
`lgtm-gitops`. Charts and repositories legitimately differ, and the
`observability-rules/source-path` ConfigMap annotation is a contract the
ArgoCD-side workstreams will key on. Only the README's `# observability-rules`
title changes.

**A trusted CI workflow for private terms.** Deferred, with a gate rather than
silence — see section 3's "What runs where" and section 9.

## 3. Publishability gates

### Security boundary

The public repository must contain no private term **and no deterministic
verifier derived from one**. An unsalted hash is not a redaction: it is an
offline confirmation oracle over a small candidate set. Measured on this
machine, a pure-Python single core tests 2.87M SHA-256 candidates per second,
so a ten-million-entry wordlist of English words and brand names falls in about
three seconds. An independent reviewer measured 3.29M/s and 3.04s. A public
salt does not help against targeted guessing, and a stored length plus a hint
reading "former employer name" makes candidate generation easier still.

A keyed HMAC would be cryptographically sound, but the key cannot be given to
`make check`, because `make check` runs pull-request-controlled code and could
exfiltrate it.

The conclusion is structural, and it is what an earlier revision of this design
got wrong: **nothing derived from a private term is committed.** Not the term,
not a digest, not a length, not a semantic hint. There are therefore two gates
with different trust properties.

### Gate 1 — public pattern check

Readable, non-secret patterns, committed and run by `make check` like every
other check.

```yaml
# publishability.yaml
version: 1
patterns:
  - id: macos-home-path
    regex: '[/]Users[/][^/ \t\r\n]+[/]'
    message: "personal absolute path"
  - id: linux-home-path
    regex: '[/]home[/][^/ \t\r\n]+[/]'
    message: "personal absolute path"
```

These are conventions, not secrets. `/Users/<name>/` is a shape you *want*
readable, so a contributor who trips it understands why without asking.

The schema is strict, and every violation is a hard failure:

- the document root has exactly `version` and `patterns`
- `version` is the integer `1`
- `patterns` is a non-empty list
- every entry has exactly the non-empty string fields `id`, `regex`, `message`
- ids and regexes are unique
- duplicate YAML keys, unknown keys, and an uncompilable regex all fail
- a missing, unreadable, malformed or empty configuration fails

**There is no self-exemption.** An earlier revision exempted
`publishability.yaml` from its own pattern scan so the regexes would not match
themselves, which would have let a personal path hide in a comment or a
`message` field. Instead, patterns are written so they do not match their own
configuration line — the bracketed-slash form above is why `[/]Users[/]` is
written that way rather than `/Users/`. If a configured pattern matches
anything else in that file, that is an ordinary finding.

Each pattern's `regex` is bounded in length, and matching runs under a time
budget, because `re` compiling successfully says nothing about catastrophic
backtracking.

### Gate 2 — private term scanner

The denylist lives **outside the repository**, at a path given by
`PUBLISHABILITY_TERMS_FILE`:

```yaml
version: 1
terms:
  - id: private-term-01
    value: "<plaintext term>"
```

Ids are opaque. `private-term-01`, never `former-employer`: the id appears in
findings and in any report, and a descriptive id re-creates the crib that made
the hash weak. Values are never printed, and a finding is
`path:line: <opaque-id>`.

The scanner fails before scanning if the variable is unset, the file is
missing, the schema is malformed, the list is empty, or two terms normalise
identically. There is no mode in which "the private gate could not run" is
reported as "the private gate found nothing".

#### Matching

Terms are normalised with Unicode NFKC then `casefold()`, and matched as
literal substrings — not tokens. Tokenising on `[a-z0-9]+` was specified in an
earlier revision and rejected on measurement: it misses a term embedded in a
longer identifier, which is exactly how one reappears in code. Substring
matching over normalised text catches `TERM-B-alerting`, `TERM-Balerting`,
`TERM-B2`, `TERM-B.` and `TERM-B_RULES` alike.

Substring matching alone still missed five forms found by review, so each file
is scanned through several views, with the term normalised the same way:

| View | Catches |
| --- | --- |
| normalised source text | ordinary occurrences, any case, inside identifiers |
| separators and format characters removed | `f-l-y`, a term broken by punctuation |
| line breaks removed | a term split across a wrapped line |
| inserted digits removed (terms with no digits) | `t3rm`-style padding |
| one pass of URL percent-decoding | `%62` inside a URL |
| decoded string scalars of valid JSON and YAML | `b` escapes |
| one decoded layer of Base64 runs ≥ 8 chars | `Zm…` blobs |

Arbitrary encryption, compression and nested encodings stay outside the
automated guarantee, and section 9 says so. When a match is found only in a
transformed view and cannot be mapped to an exact line, the finding reports the
path and the opaque id without inventing a line number.

### File discovery, fail-closed

Discovery is where a scanner silently scans nothing, so every path is
accounted for:

- `git ls-files --cached -z`, **with its exit status checked**. An unchecked
  subprocess returning empty is a scanner that passes by scanning zero files.
- also `git ls-files --others --exclude-standard -z`, so a file a later
  ordinary `git add` would sweep in is not unchecked today.
- regular files are read in full
- an unreadable, missing or non-UTF-8 file is a **finding**
- a NUL byte anywhere in a file is a **finding**
- symlinks and gitlinks are **findings**
- discovery failure, a duplicate path, or a path escaping the repository root
  is a **finding**

An earlier revision skipped binary files with a note on stdout. A NUL byte was
therefore a one-character bypass. There are no binary skips and no stdout-only
caveats: this repository has no binary tracked files, so rejecting them
outright is cheaper and safer than an allowlist, and adding binary assets later
is a separate design.

### What runs where

| | Gate 1 (patterns) | Gate 2 (private terms) |
| --- | --- | --- |
| `make check` locally | always | when `PUBLISHABILITY_TERMS_FILE` is set |
| `make check` in CI | always | never — CI must not hold the secret |
| Cutover runbook | required clean | required clean |

When gate 2 does not run, `make check` records a **caveat** through the
existing mechanism in `scripts/check.sh` — the same one used for unconfigured
ownership and for a missing `lokitool` — so the run ends in `CHECKS
INCOMPLETE`, naming the private scan as not performed. It never ends in `all
checks passed`.

This reuses a reviewed idiom rather than inventing one, and it is why gate 2 is
not a wholly separate command: a separate command is one nobody remembers to
run, and a caveat is the repository's established way of saying "this ran, that
did not".

### Registration and ownership

Gate 1 registers as `publishability` in the existing `CHECKS` dict in
`scripts/rulecheck.py`, taking `root` and returning findings like its
neighbours. Every discovery, configuration and decoding error becomes a
finding, never an empty result and never an uncaught traceback.

`/publishability.yaml` is added to `.github/CODEOWNERS` **and** to
`PLATFORM_OWNED_PATHS` in `scripts/rulecheck.py`. Without the second, a team
could take ownership of the gate that governs it — the exact defect a prior
review found four separate times in this repository, and one this design
reintroduced by adding a governing file and forgetting to govern it.

## 4. Second-team fixture

`rules/payments/mimir/checkout-alerts.yaml`, its `checkout-alerts-tests.yaml`
fixture, a dashboard under `dashboards/payments/`, and sole-owner CODEOWNERS
entries for both team paths. Documented placeholder URLs and labels only.

An independent PR, before the freeze. Acceptance criteria are explicit because
"add an example team" does not by itself test anything:

- `check_codeowners` resolves `rules/payments/` and `dashboards/payments/`
  solely to the payments owner
- Helm renders the platform and payments Mimir source paths exactly once each
- `render_assert.py` reconciles both source files with their ConfigMaps
- `tests/chart_test.sh` names the payments ConfigMap explicitly and asserts no
  repository-wide total ConfigMap count
- the promtool fixture has at least one firing and one non-firing evaluation
- the dashboard has a unique stable uid and no private datasource uid, hostname
  or tenant
- **deleting the payments rule, or either ownership entry, makes at least one
  test fail**

That last criterion is the point. The most consequential defect in the whole
build — adding a second Mimir rule always failed CI, because a test asserted
the entire render contained exactly one ConfigMap — survived every review
because nobody had used the repository for its stated purpose. A second team
that no test depends on would repeat that mistake in a new place.

It makes the one-ConfigMap assumption visible. It does not make future render
defects impossible, and this document does not claim it does.

## 5. Document rewrite

`TERM-B` becomes "the prior system" throughout the prior design and the one
line in the plan. The local archive path it cites, which gate 1's personal-path
pattern would flag anyway, goes with it. This spec is itself
in scope: nothing is exempt for being documentation, a plan, or scanner test
material.

Appendix A of the prior design keeps **every** evidence row. Its repository
inventory table, file counts and commit counts are deleted: they identify, and
they teach nothing the failure mode does not.

"Do not generalise too far" is not a testable instruction, so acceptance is
mechanical:

- every row still names a concrete mechanism (a specific expression, option,
  filename pattern or code path), not only an abstract lesson
- every row still supports the specific design rule that cites it, and the
  citation still resolves
- rows recording a correction to a previously wrong claim keep both the wrong
  claim and the correction, since the correction is the evidence
- the row count is unchanged, and a reviewer diffs old against new row by row
- no row names an organisation, repository, or person

A generalised lesson that no longer supports its rule has been rewritten too
far, and the row-by-row diff is how that is caught rather than asserted.

## 6. Destructive cutover

A runbook with a human go/no-go, executed once, attended.

### Freeze report

Generated **after all content changes are frozen**, written outside the
repository, recording by opaque term id:

- matches in the tracked and non-ignored untracked working tree
- matches in every reachable blob and path under every fetched ref
- commit and annotated-tag messages
- ref names, and author, committer and tagger identities
- `.gitmodules`, gitlinks and Git LFS objects
- the exact freeze commit and the complete remote-ref inventory

### Rewrite

`git filter-repo` with **both** `--replace-text` and `--replace-message`, and
`--sensitive-data-removal`.

`--replace-text` alone does not rewrite commit messages — verified against the
installed 2.47.0 help, which documents them as separate options. An earlier
revision of this spec said one flag would do both, and would have shipped a
rewrite that left the codename in a commit message. `--sensitive-data-removal`
additionally fetches all fetchable refs and reports LFS and cleanup concerns.

Version is pinned and discovered on `PATH`, not hardcoded to a Homebrew prefix.
The replacements file is written to the session scratchpad, never into the
repository, and deleted afterwards.

### Verification

Against the rewritten repository, not assumed:

- the freeze report re-run over all reachable objects returns nothing
- no reachable blob, path, ref name, message, tag or identity matches
- a fresh clone of the recreated remote passes `make check` with both gates
  clean, the only caveat being the deliberate unconfigured-ownership one

`git log -S` is pickaxe *history selection*, not an exhaustive scan of
reachable blobs, and is not sufficient on its own.

### Remote

Delete and recreate. **A force-push is not an acceptable fallback** — it leaves
the old objects reachable by SHA through the API. Deletion is not erasure
either: GitHub documents deleted repositories as restorable for 90 days, so the
cutover is a reduction of exposure, not a guarantee against it, and the account
holder must not restore the old repository.

`gh repo delete` needs the `delete_repo` scope, which the current token lacks
(`gist`, `read:org`, `repo`). Obtain it with
`gh auth refresh -h github.com -s delete_repo`, which is interactive. If it
cannot be obtained, **stop and report** rather than downgrading to a
force-push.

### Branch protection

In scope, and a gate on the claim in section 9 rather than a documentation
task. Both protection APIs return HTTP 403 while the repository is private —
verified against `POST /repos/{owner}/{repo}/rulesets` and
`PUT /repos/{owner}/{repo}/branches/main/protection`, both answering
`"Upgrade to GitHub Pro or make this repository public to enable this
feature."` So protection is installed immediately after the sanitised
repository becomes public.

Required: pull requests, the CI status context, dismissal of stale approvals,
an up-to-date branch, blocked force pushes and blocked deletion. CODEOWNER
approval is required once the repository names owners GitHub can resolve.
Verified by reading the ruleset back through the API **and** by a negative test
PR. Updating `docs/branch-protection.md` is not acceptance.

If protection cannot be installed or verified, the repository goes private
again. Without it, a pull request can modify the scanner, its configuration,
the Makefile and the workflow in the same commit that adds forbidden content,
and the required check then runs the contributor's version of itself.

## 7. Build order

1. **Gate 1, TDD.** Failing tests, then the check, then registration and
   ownership wiring.
2. **Gate 2, TDD.** Failing tests against synthetic terms in a temporary
   out-of-repo file, then the scanner, then the `make check` caveat wiring.
3. **Run gate 2 against the real denylist.** It **fails**, pointing at the
   documents. This is a required observation: an implementer who has not seen
   it fail has not established the scanner detects anything.
4. **Document rewrite.** Gate 2 goes clean.
5. **Second-team fixture** — independent, any time before the freeze.
6. **Cutover runbook** — attended, with the go/no-go checkpoint.

## 8. Testing

TDD throughout: failing test, observed failing, then implementation.

Gate 1: schema validation for each malformed shape listed in section 3; a
pattern hit reported with its message and no matched text; no self-exemption,
proven by a temporary pattern that matches content inside `publishability.yaml`
itself; each configured id testable independently; a pattern whose regex is
uncompilable, over-long, or backtracking-prone is rejected.

Gate 2: each of the seven views in the matching table, with a concrete input
per row; normalisation of case and Unicode; findings carry the opaque id and
never the term or matched text; a term of a different length found in the same
file, so the loop is exercised past its first iteration; missing env var,
missing file, empty list, malformed schema and colliding terms each fail
closed.

Discovery: `git ls-files` non-zero exit is a finding, not an empty scan; NUL
byte, symlink, gitlink, unreadable file, non-UTF-8 file and path-escape each
produce a finding; an untracked non-ignored file is scanned.

All pre-existing tests stay green. **The numeric test count is not an
acceptance criterion** — "109 still pass" says nothing about whether the new
ones test anything.

Final acceptance is the section 6 verification list: freeze report clean over
all reachable objects, fresh-clone `make check` clean with both gates, branch
protection read back, and the negative test PR rejected.

## 9. What this does not make safe

Publishing remains a judgement. This narrows it; it does not settle it.

The gates catch a configured pattern or a configured term, in the views listed
in section 3. They do not catch a private detail nobody added to the denylist,
a design decision that reveals something by its shape rather than its wording,
or a term hidden under encryption, compression or nested encoding.

**There is no CI enforcement of private terms**, by design: CI runs
pull-request-controlled code and must not hold the secret. The private gate
runs locally, before push. Closing this needs a trusted workflow that takes its
own implementation from the protected base branch, fetches candidate blobs
through the API, treats them strictly as data, never checks out or executes the
PR head, and fails when its secret is absent. That is deferred, and it is a
**hard gate, not a preference**: until it exists, this repository must not
merge pull requests from outside contributors.

Even with it, CI cannot prevent *initial* disclosure in a public pull request
or fork, because those git objects exist before CI starts. Nothing in this
design changes that.

And the cutover reduces exposure rather than erasing it: deleted repositories
remain restorable for 90 days.

The honest claim is: the repository contains no configured private term, its
history contains none, reintroducing one fails locally before push, and the
default branch is protected. That is materially narrower than "this repository
is safe to publish", and the README should not make the second claim.
