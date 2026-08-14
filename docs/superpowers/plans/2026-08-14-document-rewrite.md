# Document Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** remove every private reference from the tracked documents while preserving the evidence that makes the design's arguments convincing.

**Architecture:** the prior design and its plan are rewritten to neutral framing. Appendix A keeps every evidence row, rewritten from audit findings into failure modes and design responses, verified by a row-by-row diff rather than by assertion.

**Tech Stack:** Markdown, `scripts/privatescan.py` from the publishability-gates plan.

**Spec:** `docs/superpowers/specs/2026-08-14-publishable-example-design.md` section 5, and section 7 steps 4 and 5.

**Depends on:** the publishability-gates plan. Gate 2 must exist before this plan starts, because Task 1 is the observation that Gate 2 detects the real defect.

## Global Constraints

- No em dashes anywhere.
- **Never write a private term into a commit message.** The commit that fixed a bug in the publishability design reintroduced a term into the history that design exists to clean. Describe what changed without naming what was removed.
- Gate 2 output is confidential. Do not paste its findings into a commit message, a pull request, or any tracked file.
- The rewrite must not weaken an argument. A generalised lesson that no longer supports the rule citing it has been rewritten too far.
- Run the suite as: `cd <repo> && PATH="$PWD/.venv/bin:$PATH" make check`

## File Structure

| File | Responsibility |
| --- | --- |
| `docs/superpowers/specs/2026-08-10-observability-rules-design.md` (modify) | The prior design: neutral framing, Appendix A preserved |
| `docs/superpowers/plans/2026-08-10-repository-foundation.md` (modify) | One line referencing the prior system |
| `README.md` (modify) | Title matches the repository name |
| `docs/superpowers/specs/2026-08-14-publishable-example-design.md` (modify, if needed) | This workstream's own spec is in scope like any other document |

---

### Task 1: Observe Gate 2 failing against the real denylist

This task produces no commit. It is a required observation, and skipping it means the rewrite is verified against nothing. An implementer who reaches Task 2 without having seen this failure has not established that the scanner detects the real defect rather than only its fixtures.

**Files:** none modified.

- [ ] **Step 1: Create the denylist outside the repository**

This step is performed by the operator, not by an agent, and the file must never be read back into a transcript. In a separate terminal:

```bash
mkdir -p ~/.config/publishability
$EDITOR ~/.config/publishability/denylist.yaml
```

With contents of this shape, using opaque ids:

```yaml
version: 1
terms:
  - id: private-term-01
    value: "<the first string>"
  - id: private-term-02
    value: "<the second string>"
```

- [ ] **Step 2: Run Gate 2 and observe it fail**

```bash
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" python3 scripts/privatescan.py .
```

Expected: non-zero exit, with findings naming `docs/superpowers/specs/2026-08-10-observability-rules-design.md` and `docs/superpowers/plans/2026-08-10-repository-foundation.md` by opaque term id.

Record only the **count** of findings and the **set of files**. Do not copy the findings anywhere tracked.

- [ ] **Step 3: Confirm the whole build fails too**

```bash
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" make check
```

Expected: `CHECKS FAILED`, non-zero exit.

This is the red state the remaining tasks turn green.

---

### Task 2: Rewrite the prior design's framing

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-observability-rules-design.md`

- [ ] **Step 1: Establish the baseline row inventory**

Before changing anything, capture the Appendix A rows so the rewrite can be diffed against them:

```bash
awk '/^## Appendix A/,0' docs/superpowers/specs/2026-08-10-observability-rules-design.md \
  > /tmp/appendix-before.md
grep -c '^| ' /tmp/appendix-before.md
```

Write that number down. It is the count Task 3 must still produce. Note that `/tmp` is acceptable here because the extract is of a file about to become public; do not put Gate 2 findings there.

- [ ] **Step 2: Replace the framing outside Appendix A**

Rewrite each reference to the prior system as a neutral description. The substitutions:

- the codename becomes "the prior system", or a noun phrase that reads naturally in context ("the system this design replaces", "the earlier implementation")
- the standalone repository inventory table near the top of the document is deleted entirely, including its file counts and commit counts
- the local archive path is deleted. Gate 1 does **not** catch it, because it is written tilde-relative and the configured patterns match zero discovered paths. This is a manual obligation.
- no sentence may state the category of the removed words

Keep every technical claim. "The prior system hardcoded the tenant into four backend values files" is a preserved argument; only the name changes.

- [ ] **Step 3: Run Gate 2 and confirm this file is clean**

```bash
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" python3 scripts/privatescan.py . 2>&1 | \
  grep -c 'observability-rules-design'
```

Expected: `0`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-08-10-observability-rules-design.md
git commit -m "docs: neutral framing in the prior design

Replaces the prior system's name with a neutral description throughout,
and deletes the standalone repository inventory table whose counts
identify without teaching anything the failure modes do not.

Every technical claim is preserved. Only the naming changes."
```

---

### Task 3: Rewrite Appendix A row by row

The acceptance criteria here are mechanical because "do not generalise too far" is not testable.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-10-observability-rules-design.md` (Appendix A)

- [ ] **Step 1: Rewrite each row**

For each row, state the failure mode and the design response rather than the audit finding. Preserve:

- the concrete mechanism: the specific expression, option, filename pattern or code path
- quantitative evidence **inside** a row when it is part of that mechanism, or when it records a correction to a previously wrong claim. Those counts stay. Only counts whose sole purpose was to inventory the prior system's size are deleted.
- rows recording a correction keep **both** the wrong claim and the correction, because the correction is the evidence

Remove: any organisation, repository or person name.

Worked example of the transformation. Before:

> | Tenant hardcoded and unowned | `/tmp/rules/<tenant>` in 4 backend values files; 0 tenant commits in the rules repo |

After:

> | Tenant hardcoded and unowned | The tenant path was written into four backend values files, so the rules repository contained no commit that could change it. Tenancy became a per-ConfigMap sidecar annotation for this reason. |

The count of four survives because it is the mechanism: it is why the change was expensive. The repository's identity does not survive because it teaches nothing.

- [ ] **Step 2: Verify the row count is unchanged**

```bash
awk '/^## Appendix A/,0' docs/superpowers/specs/2026-08-10-observability-rules-design.md \
  > /tmp/appendix-after.md
grep -c '^| ' /tmp/appendix-after.md
```

Expected: the number recorded in Task 2 Step 1.

- [ ] **Step 3: Diff row by row**

```bash
diff <(grep '^| ' /tmp/appendix-before.md) <(grep '^| ' /tmp/appendix-after.md) | head -80
```

Read every changed row. For each, confirm:

1. it still names a concrete mechanism, not only an abstract lesson
2. the design rule that cites it still resolves, and the row still supports it
3. correction rows retain both the wrong claim and the correction
4. no organisation, repository or person is named

A row failing 1 or 2 has been rewritten too far. Restore its mechanism.

- [ ] **Step 4: Confirm each citation still resolves**

```bash
rg -n 'Appendix A' docs/superpowers/specs/2026-08-10-observability-rules-design.md
```

For each citation, read the cited row and confirm it still supports the claim at the citation site.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-10-observability-rules-design.md
git commit -m "docs: rewrite Appendix A as failure modes and design responses

Every row is preserved and every mechanism with it, including the counts
that are part of a mechanism and the corrections that record a claim this
design got wrong. What goes is the identity of the system audited, which
was the only part that taught nothing."
```

---

### Task 4: Sweep the remaining documents and confirm the build is green

**Files:**
- Modify: `docs/superpowers/plans/2026-08-10-repository-foundation.md`, `README.md`, and this workstream's spec if Gate 2 still flags it

- [ ] **Step 1: Fix the plan's one reference**

```bash
rg -n -i 'the prior system|<codename>' docs/superpowers/plans/2026-08-10-repository-foundation.md
```

Rewrite the single line referencing the prior system's owner allow-list to neutral framing.

- [ ] **Step 2: Fix the README title**

`README.md:1` reads `# observability-rules`; the repository is `lgtm-gitops`. Change the title so the front door matches the repository name. Do **not** rename the chart: `observability-rules` in `Chart.yaml` and the `observability-rules/source-path` annotation are a contract the ArgoCD-side workstreams key on.

- [ ] **Step 3: Run Gate 2 over the whole tree**

```bash
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" python3 scripts/privatescan.py .
```

Expected: `[privatescan] ok`, exit 0.

If any file still matches, fix that file. Do not add an exemption; there is no exemption mechanism, deliberately.

- [ ] **Step 4: Run the full build with both gates**

```bash
PUBLISHABILITY_TERMS_FILE=~/.config/publishability/denylist.yaml \
  PATH="$PWD/.venv/bin:$PATH" make check
```

Expected: exit 0. The only acceptable caveat is the deliberate unconfigured-ownership one.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-08-10-repository-foundation.md README.md
git commit -m "docs: neutral framing in the plan, and a README title matching the repo

The chart keeps its name: Chart.yaml and the source-path annotation are a
contract the delivery workstreams key on, and a chart may legitimately
differ from the repository holding it."
```

---

## Self-Review

**Spec coverage.** Section 5's neutral-framing requirement is Task 2; its Appendix A preservation rules and all five mechanical acceptance criteria are Task 3; its note that this spec is itself in scope is Task 4 Step 3. Section 7 step 4's required red-state observation is Task 1, and step 5's green state is Task 4 Step 4.

**Placeholder scan.** Task 2 Step 2 does not print the replacement text for every reference, because the right neutral phrasing depends on each sentence and a table of mechanical substitutions would produce unreadable prose. The rule is stated, the deletions are enumerated exactly, and Task 3 supplies a worked before-and-after example for the case that carries real risk.

**One deliberate ordering dependency.** Task 1 cannot be performed by an agent alone: the denylist holds plaintext private terms, so the operator creates it in a separate terminal and the file is never read into a transcript. Everything after Task 1 is ordinary agent work.
