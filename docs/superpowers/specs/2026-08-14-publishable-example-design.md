# Making this repository publishable, design

**Goal:** make `lgtm-gitops` safe to publish as a reusable example, and keep it
safe, without losing what makes its design arguments convincing.

**Status:** design of record for workstream 2b. Workstream 2 (the repository
foundation) is complete and pushed to a private remote.

**Notation:** this document never writes a private term. `TERM-A` and `TERM-B`
are opaque labels for the two text strings being removed; they encode neither
identity, category nor length. An earlier revision discussed private material
directly, and editing that document added another occurrence to the history it
was meant to clean. Cleanup documentation is itself part of the exposure
surface.

---

## 1. Why this exists

The repository was built as a rebuild of a working system at a previous
employer. That heritage is the reason nearly every rule in it exists, and it is
also the reason the repository cannot be published as it stands.

Two text strings, labelled `TERM-A` and `TERM-B`, must go. This document does
not assert their semantic categories or final locations. Gate 2 and the freeze
report establish the affected working-tree and historical surfaces before any
publication decision. Existing example values such as `@org`,
`runbooks.internal`, `grafana.internal` and `platform` are reviewed separately
as public placeholder material rather than treated as proof that no other
private detail exists.

**No exposure counts appear in this document.** An earlier revision tabulated
them and every number was stale within the hour: the tree gained occurrences as
the spec was written, and the history gained a message. Counts are produced by
the freeze report (section 6) against the actual repository at the moment of
cutover, never copied into prose.

So this is a small change whose risk is not size. It is that a one-time sweep
looks identical to a thorough one right up until someone greps the published
repository, and that the sweep's own paperwork is a place new occurrences
appear.

## 2. Structure

One workstream, four separately reviewed units. They are **not** one
implementation plan; the last one is not an implementation plan at all.

| Unit | Deliverable | Order |
| --- | --- | --- |
| Publishability gates | Public pattern check in `make check`; private term scanner with an out-of-repo denylist; CI artifact relocation | First |
| Second-team fixture | `rules/payments/`, `dashboards/payments/`, CODEOWNERS, integration assertions | Independent PR, any time before the freeze |
| Document rewrite | Neutral framing across every affected tracked document | After the gates exist |
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
silence. See "What runs where" below and section 9.

## 3. Publishability gates

### Security boundary

The public repository must contain no private term **and no deterministic
verifier derived from one**. An unsalted hash is not a redaction: it is an
offline confirmation oracle over a small candidate set. Measured on this
machine, a pure-Python single core tests 2.87M SHA-256 candidates per second,
so a ten-million-entry wordlist falls in about three seconds. An independent
reviewer measured 3.29M/s and 3.04s. A public salt does not help against
targeted guessing, and a stored length plus a semantic hint makes candidate
generation easier still.

A keyed HMAC would be cryptographically sound, but the key cannot be given to
`make check`, because `make check` runs pull-request-controlled code and could
exfiltrate it.

The conclusion is structural, and it is what an earlier revision got wrong:
**nothing derived from a private term is committed.** Not the term, not a
digest, not a length, not a semantic hint. There are therefore two gates with
different trust properties.

### Gate 1, public pattern check

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

These are conventions, not secrets. A platform-specific absolute home path is a
shape you *want* readable, so a contributor who trips it understands why
without asking.

The schema is strict, and every violation is a hard failure:

- the file is UTF-8 and contains exactly one YAML document
- the document root has exactly `version` and `patterns`
- `version` has type exactly `int`, not `bool`, and equals `1`
- `patterns` is a non-empty list
- every entry has exactly the string fields `id`, `regex`, `message`
- `id` matches `^[a-z][a-z0-9-]{0,63}$`
- `regex` contains between 1 and 512 Unicode code points and does not match the
  empty string
- `message` contains between 1 and 200 Unicode code points and contains no line
  break or control character
- ids and regex strings are unique
- duplicate YAML keys at any mapping level, unknown keys, custom tags and an
  uncompilable regex all fail
- a missing, unreadable, malformed or empty configuration fails

**There is no self-exemption.** `publishability.yaml` is discovered and scanned
like every other repository file. The bracketed-slash forms above do not match
their own regex fields, which is why `[/]Users[/]` is written that way rather
than `/Users/`; the naive form matches its own configuration line, and that is
what made an exemption look necessary in an earlier revision. Any configured
pattern that matches any other content in that file is an ordinary finding.

Regex execution occurs in a killable worker process controlled by the parent,
not through an advisory elapsed-time check after `re` returns, because Python
`re` has no matching timeout. Each pattern-file match receives a 1.0 second
wall-clock deadline. A deadline terminates the worker, produces a finding
naming the pattern id and path, and prevents that pattern from being used for
further files. The test suite includes a known catastrophic-backtracking
expression and adversarial input, and asserts the parent returns a finding
within a fixed outer test deadline.

### Gate 2, private term scanner

The denylist lives **outside the repository**, at a path given by
`PUBLISHABILITY_TERMS_FILE`:

```yaml
version: 1
terms:
  - id: private-term-01
    value: "<plaintext term>"
```

The resolved denylist path must be a readable regular file, must not be a
symlink, and must not be equal to or contained by the resolved repository root.
Violation is a failure before discovery starts.

The denylist is UTF-8 and contains exactly one YAML document. Its root has
exactly `version` and `terms`; `version` is exactly the integer `1`; `terms` is
a non-empty list; every entry has exactly `id` and `value`. `id` must match
`^private-term-[0-9]{2,}$` and be unique. `value` must be a non-empty string.
Duplicate YAML keys, unknown keys, custom tags, malformed YAML, duplicate ids
and values that collide after normalisation all fail.

Ids are opaque by construction. `private-term-01`, never `former-employer`: the
id appears in findings and in any report, and a descriptive id re-creates the
semantic crib that made a digest weak.

Values, matched text and parser source snippets are never printed. Diagnostics
for the denylist contain only its path, a generic error category, a source line
number when safely available, and opaque ids. A parser exception that embeds a
source line is not forwarded verbatim.

The scanner entry point exits zero only after a complete clean scan. An unset
variable, unavailable or in-repository denylist, schema error, discovery error
or finding exits non-zero. There is no result in which the private gate could
not run but reports that it found nothing.

#### Matching

Every regular file's UTF-8 contents **and every discovered repository-relative
path** are candidates. Candidate text and denylist values first undergo Unicode
NFKC followed by `casefold()`. Matching is literal substring matching, never
token matching: tokenising on `[a-z0-9]+` was specified in an earlier revision
and rejected on measurement, because it misses a term embedded in a longer
identifier, which is exactly how one reappears in code.

The scanner then applies the transformations below. The three deletion
transformations form a closure: every combination is applied to both candidate
text and the term. Outputs from the percent, escape and Base64 decoders undergo
normalisation and the same deletion closure, but no decoder is applied
recursively.

| Transformation | Exact rule |
| --- | --- |
| source | scan the normalised text unchanged |
| punctuation, separators, format characters | remove characters whose Unicode general category starts with `P` or `Z`, plus category `Cf` |
| line breaks | remove CRLF as one break, and remove CR, LF, NEL, U+2028, U+2029 individually |
| inserted digits | remove ASCII digits after NFKC, comparing this view only with terms whose normalised form contains no ASCII digit |
| percent decoding | replace each maximal sequence of `%HH` octets once; invalid triplets remain literal and cannot suppress decoding of another sequence |
| JSON/YAML escapes | decode one syntactically valid layer of `\uXXXX`, valid UTF-16 surrogate pairs, `\UXXXXXXXX` and `\xXX`; invalid escapes remain literal and cannot suppress another escape |
| Base64 | decode maximal standard and URL-safe Base64 tokens of at least eight characters, with valid explicit padding or the minimum inferred padding; each candidate considered independently |

Percent and Base64 byte results are decoded as UTF-8 with replacement for
isolated invalid bytes, so invalid bytes do not suppress valid neighbouring
text. Invalid encoding candidates are not scanner errors, because arbitrary
source text can resemble an encoding; they remain covered by the unchanged
source view.

A raw-source content match reports `path:line: <opaque-id>`. A transformed
content match reports `path: <opaque-id>` without inventing a line. **Before any
path is printed it is checked against every term and view; if the path itself
matches, the entire path is replaced by `<redacted-path>` in every finding.**
Non-matching paths are escaped before printing so control characters cannot
alter logs.

Arbitrary encryption, compression and recursive decoding remain outside the
automated guarantee, and section 9 says so.

### File discovery, fail-closed

Discovery is where a scanner silently scans nothing, so every path is accounted
for:

- `git ls-files --cached -z`, **with its exit status checked**. An unchecked
  subprocess returning empty is a scanner that passes by scanning zero files.
- also `git ls-files --others --exclude-standard -z`, so a file a later
  ordinary `git add` would sweep in is not unchecked today
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

**The Gate 1 PR also changes `.github/workflows/ci.yaml` before enabling this
discovery.** The current workflow downloads archives and extracts tool binaries
inside the repository working tree before `make check`; those paths are
untracked, non-ignored files and would correctly fail the scan. Downloads,
checksum verification and extraction move to a directory under `$RUNNER_TEMP`,
outside `$GITHUB_WORKSPACE`. The checksum command runs there while reading
`$GITHUB_WORKSPACE/tools/checksums.txt`, and only the installed executables
leave that temporary directory. Adding these artifacts to `.gitignore` is **not**
an acceptable substitute, because ignored installation artifacts would then be
invisible to the scanner by construction. A CI test asserts immediately before
`make check` that tool installation left no untracked, non-ignored paths in the
repository.

### What runs where

| | Gate 1 (patterns) | Gate 2 (private terms) |
| --- | --- | --- |
| `make check` locally | always | required; an unset or unusable denylist is a non-zero failure |
| `make check` in untrusted CI | always | explicitly skipped with `PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci` |
| Cutover runbook | required clean | required clean; skip mode forbidden |

`scripts/check.sh` invokes Gate 2 by default. If `PUBLISHABILITY_TERMS_FILE` is
unset, unavailable or invalid, the script sets its failure status and ends
non-zero with `CHECKS FAILED`. It does not translate that state into an empty
result or a successful caveat.

This is a correction to an earlier revision, which routed the absent denylist
through the existing caveat mechanism. That mechanism prints `CHECKS
INCOMPLETE` and then exits **0**, verified by running `scripts/check.sh`. A
careful human cannot read that as clean, but Make, GitHub Actions, pre-push
hooks and every other consumer of an exit status do.

The only skip is the exact value `PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci`,
accepted only when `CI=true`. The untrusted workflow sets it explicitly and ends
with `CHECKS INCOMPLETE`. That job may exit zero, because section 9 forbids
treating it as private-term enforcement, but its job name, README documentation
and branch-protection context call it `public-checks`, not a complete
publishability check. Any other skip value fails.

This keeps Gate 2 inside `make check`, so the normal local command cannot forget
it, while making the untrusted-CI exception explicit rather than letting an
absent secret silently turn a required local security check into exit zero.

### Registration and ownership

Gate 1 registers as `publishability` in the existing `CHECKS` dict in
`scripts/rulecheck.py`, taking `root` and returning findings like its
neighbours. Every discovery, configuration and decoding error becomes a
finding, never an empty result and never an uncaught traceback.

`/publishability.yaml` is added to `.github/CODEOWNERS` **and** to
`PLATFORM_OWNED_PATHS` in `scripts/rulecheck.py`. Without the second, a team
could take ownership of the gate that governs it, the exact defect a prior
review found four separate times in this repository, and one an earlier
revision of this design reintroduced by adding a governing file and forgetting
to govern it.

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
- **deleting the payments rule, deleting `dashboards/payments/`, or deleting
  either ownership entry each make at least one test fail**

That last criterion is the point. The most consequential defect in the whole
build, that adding a second Mimir rule always failed CI because a test asserted
the entire render contained exactly one ConfigMap, survived every review
because nobody had used the repository for its stated purpose. A second team
that no test depends on would repeat that mistake in a new place, and a
dashboard no test depends on is ornamental.

It makes the one-ConfigMap assumption visible. It does not make future render
defects impossible, and this document does not claim it does.

## 5. Document rewrite

Affected references become neutral descriptions such as "the prior system"
throughout the prior design and plan. The local archive path cited there, which
Gate 1 would flag independently, goes with them. This spec is itself in scope:
nothing is exempt for being documentation, a plan or scanner test material.

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

The freeze report is a go/no-go input, not only a record. Before rewriting, it
classifies every match by surface: blob content, commit or tag message,
repository path, ref name, identity, `.gitmodules`, gitlink or Git LFS object.

The approved baseline command is `git filter-repo` 2.47.0 with all three
options, using the same outside-repository replacement file for the first two:

```text
git filter-repo \
  --replace-text <replacement-file> \
  --replace-message <replacement-file> \
  --sensitive-data-removal
```

That command is sufficient **only** when the freeze report finds matches in
blob content and commit or annotated-tag messages. `--replace-text` does not
rename paths or refs and does not rewrite author, committer or tagger
identities; `--replace-message` does not change those surfaces either. A freeze
match in a path, ref, identity, `.gitmodules`, gitlink or Git LFS object is an
automatic no-go until a separate, reviewed transformation for that surface is
added to the runbook. No remote deletion or visibility change occurs while such
a match is unresolved.

An earlier revision said `--replace-text` would redact commit messages too, and
would have shipped a rewrite leaving a term in a message. The options are
separate, verified against the installed 2.47.0 help.

The rewrite runs in a fresh mirror-like clone under the session scratchpad, not
in the development working tree. The freeze commit must already be pushed to
the private remote, the working tree must be clean, and the fetched ref
inventory must equal the inventory recorded by the freeze report before
`--sensitive-data-removal` is allowed to perform its mirror-like fetch.

The replacement file is generated and retained only outside the repository. It
must cover the exact byte spellings present in the classified blob and message
findings. Because the scanner additionally uses NFKC, case folding and decoded
views while `git filter-repo` replaces bytes, the command flags are **not**
proof of removal. The full freeze scan is rerun after every rewrite attempt,
and any remaining match is a no-go.

The executable is discovered on `PATH`; the runbook verifies version 2.47.0
through its installation metadata before use. The replacement file and working
clone are deleted after successful independent verification.

### Verification

Against the rewritten repository, not assumed:

- the freeze report re-run over all reachable objects returns nothing
- no reachable blob, path, ref name, message, tag or identity matches
- a fresh clone of the recreated remote passes `make check` with both gates
  clean, the only caveat being the deliberate unconfigured-ownership one

`git log -S` is pickaxe *history selection*, not an exhaustive scan of
reachable blobs, and is not sufficient on its own.

### Remote

Delete and recreate. **A force-push is not an acceptable fallback**: it leaves
the old objects reachable by SHA through the API. Deletion is not erasure
either, since GitHub documents deleted repositories as restorable for 90 days,
so the cutover reduces exposure rather than guaranteeing against it, and the
account holder must not restore the old repository.

`gh repo delete` needs the `delete_repo` scope, which the current token lacks
(`gist`, `read:org`, `repo`). Obtain it with
`gh auth refresh -h github.com -s delete_repo`, which is interactive. If it
cannot be obtained, **stop and report** rather than downgrading to a
force-push.

### Branch protection

In scope, and a gate on the claim in section 9 rather than a documentation
task. Both protection APIs return HTTP 403 while the repository is private,
verified against `POST /repos/{owner}/{repo}/rulesets` and
`PUT /repos/{owner}/{repo}/branches/main/protection`, both answering
`"Upgrade to GitHub Pro or make this repository public to enable this
feature."` So protection is installed immediately after the sanitised
repository becomes public.

Required: pull requests, the CI status context, dismissal of stale approvals,
an up-to-date branch, blocked force pushes and blocked deletion. CODEOWNER
approval is required once the repository names owners GitHub can resolve.

Verified by reading the ruleset back through the API **and** by a negative test
PR that must demonstrate three things: a harmless synthetic Gate 1 failure
appears as a failed required check, the merge button is blocked, and an API
merge attempt is refused without bypass. Updating `docs/branch-protection.md`
is not acceptance.

If protection cannot be installed or verified, the repository goes private
again. Without it, a pull request can modify the scanner, its configuration,
the Makefile and the workflow in the same commit that adds forbidden content,
and the required check then runs the contributor's version of itself.

## 7. Build order

1. **CI artifact relocation.** Tool downloads move under `$RUNNER_TEMP` with
   the no-untracked-paths assertion, before fail-closed discovery exists.
   Without this, Gate 1 cannot pass in CI.
2. **Gate 1, TDD.** Failing tests, then the check, then registration and
   ownership wiring.
3. **Gate 2, TDD.** Failing tests against synthetic terms in a temporary
   out-of-repo file, then the scanner, then the `make check` wiring and the
   single permitted skip value.
4. **Run Gate 2 against the real denylist.** It **fails**, pointing at the
   documents. This is a required observation: an implementer who has not seen
   it fail has not established the scanner detects anything.
5. **Document rewrite.** Gate 2 goes clean.
6. **Second-team fixture**, independent, any time before the freeze.
7. **Cutover runbook**, attended, with the go/no-go checkpoint.

## 8. Testing

TDD throughout: failing test, observed failing, then implementation.

**Gate 1:** every malformed shape in the schema list; a pattern hit reported
with its message and no matched text; no self-exemption, proven by a temporary
pattern matching content inside `publishability.yaml` itself; each configured id
testable independently; a catastrophic-backtracking expression with adversarial
input returns a finding within a fixed outer deadline rather than hanging.

**Gate 2:** each transformation row with a concrete input, including every
combination in the deletion closure; normalisation of case and Unicode; a term
appearing only in a path name is found; a matching path is reported as
`<redacted-path>`; findings carry the opaque id and never the term, matched text
or parser snippet; missing env var, missing file, in-repository denylist,
symlinked denylist, empty list, malformed schema and colliding terms each fail
closed and non-zero.

**Discovery:** `git ls-files` non-zero exit is a finding, not an empty scan;
NUL byte, symlink, gitlink, unreadable file, non-UTF-8 file and path-escape each
produce a finding; an untracked non-ignored file is scanned.

**Skip mode:** `PUBLISHABILITY_PRIVATE_SCAN=skip-untrusted-ci` is honoured only
when `CI=true`; every other value, and the same value without `CI=true`, fails.

All pre-existing tests stay green. **The numeric test count is not an
acceptance criterion**: "109 still pass" says nothing about whether the new ones
test anything.

Final acceptance is the section 6 verification list: freeze report clean over
all reachable objects, fresh-clone `make check` clean with both gates, branch
protection read back, and the negative test PR refused at all three points.

## 9. What this does not make safe

Publishing remains a judgement. This narrows it; it does not settle it.

The gates catch a configured pattern or a configured term, in the views listed
in section 3. They do not catch a private detail nobody added to the denylist, a
design decision that reveals something by its shape rather than its wording, or
a term hidden under encryption, compression or recursive encoding.

**There is no CI enforcement of private terms**, by design: CI runs
pull-request-controlled code and must not hold the secret. The private gate runs
locally, before push, and fails non-zero when it cannot run. Closing this needs a
trusted workflow that takes its own implementation from the protected base
branch, fetches candidate blobs through the API, treats them strictly as data,
never checks out or executes the PR head, and fails when its secret is absent.
That is deferred, and it is a **hard gate, not a preference**: until it exists,
this repository must not merge pull requests from outside contributors.

Even with it, CI cannot prevent *initial* disclosure in a public pull request or
fork, because those git objects exist before CI starts. Nothing in this design
changes that.

And the cutover reduces exposure rather than erasing it: deleted repositories
remain restorable for 90 days.

The honest claim is: the repository contains no configured private term, its
history contains none, reintroducing one fails locally before push, and the
default branch is protected. That is materially narrower than "this repository
is safe to publish", and the README should not make the second claim.
