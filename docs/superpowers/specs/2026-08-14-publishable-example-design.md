# Making this repository publishable — design

**Goal:** make `lgtm-gitops` safe to publish as a reusable example, and keep it
safe, without losing what makes its design arguments convincing.

**Status:** design of record for workstream 2b. Workstream 2 (the repository
foundation) is complete and pushed to a private remote.

---

## 1. Why this exists

The repository was built as a rebuild of a working system at a previous
employer. That heritage is the reason nearly every rule in it exists, and it is
also the reason the repository cannot be published as it stands.

The exposure was measured, not estimated:

| Where | Former employer name | Former internal codename |
| --- | --- | --- |
| Tracked files at HEAD | 2 lines, both in the spec's Appendix A | 25 lines, all under `docs/superpowers/` |
| Commit diffs | 1 commit | 4 commits |
| Commit messages | 0 | 1 commit, twice |

Everything else a reader might mistake for private detail is already
placeholder material: `@org` in CODEOWNERS, `runbooks.internal` and
`grafana.internal` in rule annotations, `platform` as the only team. The chart,
the scripts, the rules and the tests contain nothing private.

So this is a small, bounded change. Its risk is not size; it is that a
one-time sweep looks identical to a thorough one right up until someone greps
the published repository.

## 2. What is in scope

Four units, in the order they must be built:

| Unit | Deliverable |
| --- | --- |
| Publishability check | `publishability.yaml` plus a `publishability` check in `scripts/rulecheck.py` |
| Document rewrite | Neutral prior-system framing in the spec and plan |
| Second example team | `rules/payments/`, `dashboards/payments/`, CODEOWNERS entries |
| History rewrite | All 39 commits redacted; remote deleted and recreated |

### Not in scope

**Renaming the chart.** The chart is `observability-rules`; the repository is
`lgtm-gitops`. Charts and repositories legitimately differ, and the
`observability-rules/source-path` ConfigMap annotation is a contract the
ArgoCD-side workstreams will key on. Only the README's `# observability-rules`
title changes, so the front door matches the repository name.

**Scanning commit messages in the publishability check.** History is made clean
once, by rewrite (section 6), and thereafter protected by the branch protection
in `docs/branch-protection.md`. A check that scans all of history on every run
would be the wrong shape: slow, and duplicating a guarantee that force-push
protection already provides. The check governs tracked file contents.

**Branch protection itself.** Verified unavailable: both
`POST /repos/{owner}/{repo}/rulesets` and
`PUT /repos/{owner}/{repo}/branches/main/protection` return HTTP 403,
`"Upgrade to GitHub Pro or make this repository public to enable this
feature."` Publishing the repository is what makes protection available on this
account, which makes this workstream a prerequisite for the one gap both prior
reviews flagged. `docs/branch-protection.md` gains a note recording this.

## 3. The publishability check

### The problem with the obvious design

A denylist file that spells out the words it forbids **is** the leak it exists
to prevent. Publish `publishability.yaml` containing `***REMOVED***` and the name is
published, by the very mechanism meant to stop it.

Storing the terms outside the repository fails differently and worse: the check
would silently pass wherever the file is absent, which is precisely the
appears-to-pass-but-never-ran failure this repository was built to eliminate.

### Design

Terms are stored as SHA-256 of the lowercased term, with the term's length,
which the scan needs to size its search window. Findings report a
non-identifying `hint`, never the term.

```yaml
# publishability.yaml
terms:
  - hash: "sha256:<64 lowercase hex>"
    length: 9
    hint: "former employer name"
patterns:
  - regex: '/Users/[^/ ]+/'
    hint: "personal absolute path"
```

### What hashing does and does not buy

It must be stated plainly, because the rest of the design depends on not
believing more than is true: **hashing the terms is obfuscation, not
confidentiality.**

Measured on this machine, a pure-Python single core tests 2.87M SHA-256
candidates per second. A ten-million-entry wordlist of English words and brand
names therefore falls in **3 seconds**, and about a millisecond on a GPU. The
hashes are unsalted by necessity — the check must run in a fresh clone with no
external key — so anyone who wants a redacted term back can have it.

What hashing genuinely buys is worth having anyway:

- the words are not greppable, not indexed by search engines, and not returned
  by GitHub code search
- a reader browsing the repository does not learn them incidentally
- and, the actual point, a contributor cannot reintroduce one without CI
  failing

The primary purpose of this check is preventing reintroduction, and for that
the hash strength is irrelevant. Confidentiality of the term itself is a
secondary, weak property, and the spec claims nothing more. An adopter with a
term that genuinely must stay secret should keep it in a private fork's
denylist rather than trusting this file, and section 9 says so.

Two sections, because the two kinds of forbidden content fail differently:

- A **term** is a secret word. It must be hashed to be committable at all.
- A **pattern** is a convention, not a secret. `/Users/<name>/` is a shape you
  *want* readable, so a contributor who trips it understands why without having
  to ask.

Neither section compromises the other, and the file stays publishable as-is —
which matters, because an adopter should be able to fork this repository and
add their own private terms without publishing them.

### Matching semantics

- **Terms.** For each configured length `n`, the lowercased line is scanned as
  overlapping `n`-grams: every window `line[i:i+n]` is SHA-256'd and compared
  against the hashes configured at that length.

  The obvious cheaper rule — tokenise on `[a-z0-9]+` and hash whole tokens —
  was specified first and rejected on measurement. It misses a term embedded in
  a longer alphanumeric run, which is exactly how one reappears in code:

  | Input | token scan | `n`-gram scan |
  | --- | --- | --- |
  | `***REMOVED***-alerting` | hit | hit |
  | `***REMOVED***.` | hit | hit |
  | `***REMOVED***_RULES` | hit | hit |
  | `***REMOVED***Alerting` | **miss** | hit |
  | `***REMOVED***2` | **miss** | hit |

  The `n`-gram scan costs 0.12s against this repository's 278k tracked
  characters, versus 0.01s for tokens. Both are free; only one is correct.

  Storing `length` is what makes the window sizable. It leaks the term's
  length, which given the section above changes nothing material.
- **Patterns.** `re.search(regex, line)` per line, case-sensitive.
- **Findings** are `path:line: <hint>`. The matched text is never printed, for
  terms or for patterns: a pattern's match can itself be the private value.
- **File set.** `git ls-files`, which is the definition of "what publishing
  would expose". Files containing a NUL byte in their first 8000 bytes are
  treated as binary and skipped.

A skipped file must never be indistinguishable from a scanned one. Because the
`CHECKS` contract is "returned strings fail the build", a skip cannot be a
finding, so the check prints one line per skipped file to stdout:

```
[publishability] skipped (binary, not scanned): path/to/file
```

The repository currently has no binary tracked files — dashboards are JSON — so
this output is expected to be empty, and a line appearing in it is information a
reviewer needs.

### Self-reference

`publishability.yaml` is exempt from the **pattern** scan and only from it. Its
`regex` values would otherwise match themselves. It is still scanned for terms,
where by construction it can contain none. The exemption is narrow enough that
nothing private can hide behind it: a term cannot be stored there in plaintext
without failing the term scan.

Tests exercise the *mechanism* with synthetic patterns of their own
(`INTERNAL-[0-9]+`), never the real configuration's patterns, so test fixtures
cannot collide with the live denylist. One test asserts the real
`publishability.yaml`'s personal-path pattern behaves, against a string built by
concatenation so the literal never appears in a tracked file.

### Adding a term

```
make add-private-term
```

Reads the term via `getpass`, so it reaches neither the shell history nor a
terminal transcript, prompts for a hint on stdin normally, and appends an entry
carrying the hash, the length and the hint. It refuses a term shorter than four
characters, since a three-character term matches half the repository as an
`n`-gram and the hash cannot be inspected afterwards to find out why.

When the new term already matches the tracked tree, it prints the number of
matching files — never their contents — and warns that `make check` will now
fail until they are cleaned up. It still writes the entry: section 7's build
order depends on exactly this state being reachable, since observing the
failure is what proves the check works against the real defect. The warning
exists so that reaching it by accident is loud, not so that reaching it
deliberately is blocked.

This is the one place where the general rule against putting secrets on a
command line has a supported alternative, and the Makefile target exists so
nobody improvises one.

### Registration

A new entry in the existing `CHECKS` dict in `scripts/rulecheck.py`, alongside
`layout`, `contract`, `fixtures`, `envmatcher`, `ownership`, `codeowners` and
`dashboards`. It takes `root` like every check but one, returns a list of
finding strings, and needs no change to `main`.

A missing or malformed `publishability.yaml` is a **hard failure**, not a
warning and not a skip. The unconfigured-ownership precedent does not apply:
ownership ships deliberately unconfigured because only the adopter can know
their organisation, whereas an absent denylist means the check cannot run at
all. "The check could not run" must never be reportable as "the check found
nothing".

## 4. Documents

`***REMOVED***` becomes "the prior system" throughout the spec and the one line in
the plan. The `~/git/old-work` path reference goes.

Appendix A keeps all 16 evidence rows, each rewritten to state a failure mode
and the design response rather than an audit finding. The repository inventory
table, file counts and commit counts are deleted: they are the identifying
detail, and they teach nothing that the failure mode itself does not.

The rewrite must preserve the arguments. The point of Appendix A is that every
rule in the design exists because something specific went wrong, and a
generalised lesson that no longer supports its rule has been rewritten too far.

## 5. Second example team

`rules/payments/mimir/checkout-alerts.yaml` with a `checkout-alerts-tests.yaml`
fixture, a `dashboards/payments/` dashboard, and CODEOWNERS entries for both
paths.

This is example content, but it is not only example content. The existing
checks for the multi-team ownership boundary are exercised today only by unit
tests against synthetic CODEOWNERS files; no second team exists in the tree.
The single most consequential defect found in the whole build — that adding a
second Mimir rule always failed CI, because a test asserted the entire render
contained exactly one ConfigMap — survived every review precisely because
nobody had tried to use the repository for its stated purpose. A second team in
the tree makes that class of defect impossible to ship again.

The new content must satisfy every existing check with no exemptions: the
`rules/<team>/<target>/<service>[-<type>]-alerts.yaml` naming contract, the
canonical `deployment_environment=~"..."` matcher form, a passing promtool unit
test, and a dashboard uid unique across the tree.

## 6. History

`git filter-repo --replace-text` over all 39 commits, redacting both terms in
diffs and in `e05b539`'s commit message. Four commits change; the replacements
file is written to the session scratchpad, never into the repository, and
deleted afterwards. `git-filter-repo` 2.47.0 is installed at
`/opt/homebrew/bin/git-filter-repo`.

The remote is then **deleted and recreated**, not force-pushed.

This is the part worth being explicit about. A force-push leaves the old
commits on GitHub as dangling objects, reachable by SHA through the API until
GitHub garbage-collects them on a schedule nobody outside GitHub controls. The
repository is hours old, private, and has no issues, pull requests, forks or
stars, so deleting and recreating it costs nothing and is the only version of
this step that can honestly be called complete.

`gh repo delete` requires the `delete_repo` scope, which the current token does
not carry (`gist`, `read:org`, `repo`). The scope must be added with
`gh auth refresh -h github.com -s delete_repo`, which is interactive; if it
cannot be obtained, the fallback is a force-push plus an explicit statement
that dangling objects remain, never a silent downgrade.

### Verification

The rewrite is verified, not assumed:

- `git log --all -S<term> --pickaxe-regex -i` returns nothing, for both terms
- `git log --all --grep=<term> -i` returns nothing
- a fresh `git clone` of the recreated remote passes `make check`
- `make check` on that clone exits 0 with no `CHECKS INCOMPLETE` caveat other
  than the ownership one, which is deliberate and documented

## 7. Build order

The order is chosen so the work is test-driven at the repository level, not
only at the unit level:

1. **Publishability check, TDD.** Failing tests first, then the check, then
   registration. At this point `publishability.yaml` is empty and the check
   passes vacuously — which is itself a state the tests must cover.
2. **Add the two terms.** `make check` now **fails**, pointing at the spec.
   This is the red state, and it proves the check works against the real
   defect rather than only against fixtures.
3. **Rewrite the documents.** `make check` goes green.
4. **Second example team.** Green throughout; the multi-team paths now carry
   real content.
5. **History rewrite and remote recreate.** Last, because it invalidates every
   SHA and should happen once, over the finished tree.

Step 2 failing is a required observation, not an accident. An implementer who
reaches step 3 without having seen step 2 fail has not established that the
check does anything.

## 8. Testing

TDD throughout: failing test, observed failing, then implementation.

New pytest coverage for the publishability check:

- a term hash matching text in a tracked file is reported
- the finding contains the hint and **not** the term or the matched text
- a term embedded in a longer identifier is caught: the five inputs tabulated
  in section 3 are the test cases, including the two the rejected token rule
  missed
- case-insensitivity: `***REMOVED***`, `***REMOVED***` and `***REMOVED***` all match
- two terms of different lengths are both found in one file, so the per-length
  window loop is exercised rather than only its first iteration
- a pattern match is reported with its hint
- `publishability.yaml` is exempt from patterns but not from terms
- a binary file is skipped, and the skip is visible
- a missing `publishability.yaml` fails
- a malformed `publishability.yaml` fails: not a mapping, `terms` not a list,
  an entry missing `hash`, `length` or `hint`, a hash that is not `sha256:` +
  64 hex, a `length` that is not an integer of at least 4, a `regex` that does
  not compile
- an empty but well-formed config passes

The existing 109 tests stay green. The final gate is `make check` exiting 0 on
a fresh clone of the recreated remote, plus the history greps in section 6.

## 9. What this does not make safe

Publishing remains a judgement, and this check narrows it rather than settling
it. It catches a configured term or pattern in a tracked file. It does not
catch a private detail nobody thought to add to the denylist, a design decision
that reveals something by its shape rather than its wording, or anything in an
untracked file that a later `git add` would sweep in.

It also does not keep the configured terms secret. Section 3 measures this: an
unsalted SHA-256 of a dictionary word falls in seconds. A term whose secrecy
actually matters does not belong in a published denylist at all; keep it in a
private fork's copy of this file and accept that the public one is a weaker
net.

The honest claim is: the repository contains no configured private term, its
history contains none, and reintroducing one fails the build. That is a
materially different claim from "this repository is safe to publish", and the
README should not make the second one.
