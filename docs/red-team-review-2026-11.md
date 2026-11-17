# Red-Team Review and Fix Record — November 2026

This is an engineering record of one review cycle: an independent red-team
review of the Definitio runtime (2026-11-16), the resulting fix
(RT-FIX-20261116), and an independent acceptance of that fix (2026-11-17).
It is published so that claims about the review can be checked against the
record. **It is not a certification document** — the runtime's certification
status remains **NOT_CERTIFIED**.

Scope of all conclusions below: this repository's tree at source digest
`10492781ee3c51d523ff9d292b043933fbb6094f7314ba3f240429283522b288`
(the digest algorithm covers `definitio/*.py` plus the two backend worker
scripts; see the fix manifest in the project evidence archive).

## 1. The review (2026-11-16)

An independent red team examined the runtime adversarially (interface
`definitio-agent-v1`, version 0.2.0-dev) and returned a verdict of **NO_GO**:
**11 issue groups (RT-01 … RT-11) with 13 reproducible counterexamples.**
Every one of the 13 counterexamples was reproduced on the canonical tree
before any fix work began (13/13).

The findings, grouped by failure class:

### A. The verifier checked consistency, not spatial semantics

| ID | Finding |
| --- | --- |
| RT-01 | A buffer result with wrong geometry passed verification |
| RT-02 | A clip result with wrong geometry passed verification |
| RT-03 | Row/attribute binding failures: duplicated clip properties; joins attaching wrong attributes |
| RT-09 | The buffer bound for MultiPoint inputs was too weak to catch wrong results |
| RT-10 | Hillshade tolerance masked wrong output in dark-slope pixels |
| RT-11 | The empty-geometry contract for reprojection was inconsistent between computation and verification |

### B. Authorization scope

| ID | Finding |
| --- | --- |
| RT-04 | Side effects — dataset writes, file export, external publish — were not checked against the grant's capabilities; a plan's self-declared exports could widen the effective scope within the managed directory |

### C. Verification not bound to persisted state

| ID | Finding |
| --- | --- |
| RT-05 | A replay still reported "confirmed" after the artifact file had been deleted; artifact readback failures were not surfaced |
| RT-08 | Verify paths (including failed-task and read-only verification) accepted results that were not rebuilt from persisted output and evidence |

### D. External side effects

| ID | Finding |
| --- | --- |
| RT-06 | External-publish phases were not isolated in separate persistence; a malformed response (e.g. invalid JSON) could leave the true target state ambiguous |

### E. Publication and recovery

| ID | Finding |
| --- | --- |
| RT-07 | Publication state handling had gaps: registration vs. byte-visibility ordering and failure isolation could leak state |

In addition, the G1 baseline guard resolved its workbench root implicitly and
pointed at a baseline file outside the package. For the record: two items in
the original review were artifacts of the flat review copy's directory layout
rather than product defects; they are not counted above.

The 13 counterexample probes were: `buffer_wrong_geometry`,
`clip_wrong_geometry`, `grant_export`, `replay_missing_file`,
`artifact_readback_failure`, `failed_task_verify`, `readonly_verify`,
`multipoint_buffer`, `clip_duplicate_properties`, `empty_reproject`,
`join_wrong_attributes`, `hillshade_dark_slope`,
`external_publish_invalid_json`.

## 2. The fix (RT-FIX-20261116)

The fix was executed in three batches against the canonical tree only.

**Batch 1 — authorization and state binding (RT-04, RT-05, RT-06, RT-08).**
All four side-effect classes are now checked against grant capabilities
before any write, including on the replay path and at a second gate inside
the runtime; a plan's self-declared exports no longer substitute for
authorization. Receipts became immutable history records, and commit
re-reads the live artifact (bytes, size, SHA-256, content binding, state);
a replay whose current state cannot be confirmed reports
`TARGET_STATE_UNKNOWN`. External side-effect phases are persisted on an
independent connection, and failures from `request_pending` onward are
isolated without claiming rollback. Verification now rebuilds its proposal
from persisted output and evidence, binds result digests, and reports
coverage status.

**Batch 2 — spatial semantics (RT-01, RT-02, RT-03, RT-09, RT-10, RT-11).**
Buffer verification uses provable spatial constraints (containment,
expansion, same-kernel recomputation, explicitly labelled as a non-independent
oracle). Clip carries explicit source-row provenance with completeness,
mask-side constraints, and a row-multiplicity check — the latter added after
mutation testing exposed a real gap, and fixed in production code rather
than by relaxing the predicate. Joins bind the full pair relation per row.
The MultiPoint buffer bound is a part-level analytic upper bound with stated
applicability conditions and no tolerance widening. Hillshade is locked to
the GDAL 3.12.2 exact uint8 encoding (117/117 pixels exact; the former ±1
tolerance was removed). The empty/null-geometry contract is now identical on
the computation and verification sides.

**Batch 3 — publication and the baseline guard (RT-07, G1 guard).**
Publication is a recoverable `staging → published → verified` state machine
with a `rolled_back` state; durable registration precedes byte visibility;
failure isolation preserves bytes while releasing uniqueness slots. The G1
guard moved to a bundled 59-file baseline manifest plus an explicit
workbench-root parameter; missing evidence is reported as `MISSING`, never
silently skipped or converted to PASS.

Changed files: 7 engine modules (`agent`, `authorization`, `commit`,
`operations`, `runtime`, `store`, `verifier`), `scripts/audit_g1.py`, and
`tests/test_golden_g1_guard.py`; added `evidence/g1-baseline-59.json`.
9 files changed, 1040 insertions, 173 deletions.

Only one pre-existing test file was modified — `tests/test_golden_g1_guard.py`,
5 changes — each audited in §3.

## 3. Independent acceptance (2026-11-17)

The fix was accepted by a reviewer independent of the fix executor.
Verdict: **ACCEPTED_WITH_LIMITATIONS**.

Evidence gathered independently by the accepting reviewer:

- **Probes:** the original red-team probe file (SHA-256
  `56ffb951b27f46f302d5245d5e66e05ed2da0a2ccad510d3b8d4a9377592ceab`,
  verified byte-identical to the review original) was re-run against the
  fixed tree from an isolated directory: **0 of 13 defect assertions
  reproduce.**
- **Source digest:** recomputed from scratch with an independent script
  (no project code imported) over the 34 covered files:
  `10492781ee3c51d523ff9d292b043933fbb6094f7314ba3f240429283522b288` —
  identical to the fix manifest and to the executor's reported value
  (three-way agreement, zero per-file differences).
- **Assertion audit:** the 5 changes to `tests/test_golden_g1_guard.py` were
  reviewed one by one and found to be net strengthening, not weakening:
  explicit root resolution replaces implicit path derivation; the bundled
  baseline was verified entry-by-entry against the original 59-file list
  (all hashes equal, ruling out a self-generated baseline); PASS conditions
  in the delegated check are stricter than the original assertions; an
  evidence-status assertion was added; 4 new negative tests cover the
  missing-evidence cases. The reviewer also re-ran the red-team regression
  suite (14/14 passed) and the G1 guard suite (6/6 passed, including
  negative cases).
- **Mutation testing** (executor-run, recorded in the fix evidence pack):
  18 mutants across 6 classes (translation, shrink, empty result, attribute
  mismatch, wrong count, failure phase) were all rejected — false-accept
  0/18 — and 12 legitimate cases were all accepted — false-reject 0/12.
- **Full suite** (executor-run): 403 tests passed, exit code 0.

### Limitations recorded by the accepting reviewer

- The full 403-test suite was **not** re-run end-to-end by the accepting
  reviewer; its log had been verified in a prior round, and the probe,
  regression, and guard subsets above were re-run directly.
- Conclusions apply to the tree at the source digest named at the top of
  this document. They are an acceptance of the fix, **not** a certification.

## 4. Current status

- All 13 red-team counterexamples: fixed and independently re-verified
  (0/13 reproduce).
- Certification: **NOT_CERTIFIED** — unchanged by this cycle.

The machine-generated logs behind this record (probe outputs, batch notes,
mutation report, delivery manifest) are retained in the project evidence
archive; this document is the public summary of that material.
