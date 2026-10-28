# Ghost Data Still Exists: Technical Architecture and Development Report

Engineering write-up of the agent as submitted to HackerRank Orchestrate (October 2026). Everything marked **verified** was
checked against the source code or reproduced by a command listed in §11. **Assumption** and
**recommendation** are labeled where they appear.

---

## 1. Executive summary

The agent audits 518 privacy requests. For each one it decides whether deletion was authorized,
reconstructs the subject's identifiers as of the audit time, finds every copy of their data in 94
evidence files (SQL snapshots, JSON exports, logs, PDFs, screenshots) and a 4,011-row activity
stream, rebuilds each record's state at the audit cutoff, applies `deletion_rules.csv`, and emits
`COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE` with ordered steps.

The design is **deterministic code with two narrow model calls**. Gemini transcribes 11 screenshots
into a fixed schema and classifies 398 distinct request texts. Identity, linking, attribution,
temporal state, policy, planning and validation are all plain Python.

Verified results (`python code/evaluation/run_all.py`; see the RESULTS block of
`code/evaluation/evaluation_report.md`):

| Measure | Value |
|---|---|
| Public samples (32): status accuracy | 100.0% |
| Step precision / recall / F1 | 100.0% / 92.7% / 96.2% |
| Exact sequence accuracy | 59.4% (19/32) |
| Missing / extra steps | 14 / 0 |
| Custom cases | 6/6; regression suite 20/20 (15 scenarios) |
| Final `output.csv` validation | 518 rows, 1,118 citations, 0 problems |
| Model usage per full run | 15 calls; 31,127 input + 23,060 output = 54,187 tokens; about $0.0814 at aggregator-reported list prices; $0 billed (free tier) |

Public-sample results are a development check and **do not predict** the hidden evaluation.

---

## 2. Problem formulation

**Input** per case (`dataset/deletion_requests.csv`): `case_id, email, requested_at,
reported_status, reported_at, audit_at, request_text`. **Context**: `user_identities.csv` (850
users), `user_audit_log.csv` (306 identity changes), `system_activity.csv` (4,011 rows),
`deletion_rules.csv` (15 rules), `evidence_files.csv` (94 files).

**Output** (`output.csv`): `case_id, deletion_status, steps_json`, where each step is
`{step_no, phase, system_id, resource_id, operation, state, evidence_id}` using only the 15
operation/state pairs in the problem statement.

**Core difficulties:**
1. Identifiers change over time: 100 of 518 requests use a historical email.
2. Deleted or anonymized records carry no identifier.
3. Evidence comes in about ten formats.
4. State must be judged at `audit_at` using only evidence available then.
5. Workflow statuses and `reported_result`s are claims, not proof.
6. Decoys: look-alike names and emails.
7. Non-deletion requests must be `NOT_APPLICABLE`.

---

## 3. Architecture and data flow

```
code/main.py
  load_dataset()                  audit/data.py
  AuditAgent(dataset)             audit/agent.py
    load_evidence(manifest)       audit/evidence/__init__.py   -> Evidence(observations, coverage, errors)
    IdentityResolver(dataset)     audit/identity.py
    ResourceIndex(evidence, activity, captured)   audit/linking.py
    WorkflowAttributor(all requests, ...)         audit/linking.py
    IntentClassifier(all requests)                audit/intent.py   (Gemini, cached)
  for each request (sorted by case_sort_key):
    agent.audit(request)
      1 intent.authorizes_deletion        -> NOT_APPLICABLE, []   if False
      2 context(): resolve identity, index.linked(), attributor.attribute()
      3 policy.plan_resource_actions()    -> list[Decision]
      4 planner.build_steps()             -> status, list[Step]
    schema.serialize_steps() + schema.validate_row()  -> invalid rows are reported (stderr)
  write output.csv; write the call ledger (code/evaluation/usage_ledger_<requests>.json)
```

Setup work (evidence parsing, indexing) happens once per run; per-case work is identity,
linking, attribution and policy.

---

## 4. Evidence schema and parser design

### 4.1 Normalized model (verified, `audit/evidence/model.py`)

- **`Observation`** (dataclass): `file_id, observed_at, system_id, resource_id, exists, user_ref,
  email, phone, email_hash, name, lookup_key, attrs`.
  - `user_ref` holds a stable `user_NN`; `email/phone/email_hash/name` are **direct identifiers**
    (`direct_identifiers` property).
  - `lookup_key` is what the source was queried or correlated by: a link, **not** exposed data.
  - `attrs` carries state signals: `enabled`, `restore_allowed`, `data_category`, `role`
    (replica), `retention_until`, `event`, and others.
- **`Coverage`** (frozen dataclass): `file_id, observed_at, system_id, table, resource_ids`. It is a
  full listing of one system's records in one table at one time.
- `identifiers_from(record)` normalizes source field names: reference keys (a `user_NN` goes to
  `user_ref`, anything else to `lookup_key`), email keys including `uploaded_by`, phone, name and
  hash keys, plus a regex scan of `message_text`/`message_excerpt` for embedded emails and phones.
- `status_observation(status, system_id, category, identifier)` maps the shared checklist vocabulary
  (`NOT_FOUND, FOUND, IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, BLOCKED`) to `exists`
  plus state attributes. On absent or de-identified rows an email is stored as `lookup_key`.

### 4.2 Registry (verified, `audit/evidence/__init__.py`)

`PARSERS` is an ordered list of `(glob over the path below evidence/, parser)`; the first match
wins. File counts below are the 94 manifest files per parser, verified with `parser_for`. `load_evidence(files)` runs each parser **inside try/except**: a failure is appended to
`Evidence.errors`, printed to stderr, and the file is skipped (commit `0e1855c`).

| Pattern | Parser | Files | Notes |
|---|---|---:|---|
| `records/service_resource_export_*.json` | `service_export.parse` | 11 | per-service readbacks; a 404 means absent; a search hit's document carries the identifiers |
| `logs/resource_lookup_trace_*.log` | `lookup_trace.parse` | 11 | joins `*_started` (lookup_key) with `*_completed` by `trace_id` |
| `records/customer_account_handover_*.txt` | `handover.parse` | 1 | pipe-separated checklist with an explicit data category |
| `logs/*.log` | `obslog.parse` | 15 | 6 envelopes normalized by `envelopes.parse_event`; 17 state-bearing event types; infrastructure noise ignored; `support_replica` aliased to `support_platform` |
| `databases/*.sql` | `sql_snapshot.parse` | 24 | executed in in-memory SQLite; per-row observations tagged `table`, `role`; `Coverage` per table/system |
| `records/*.json` | `json_records.parse` | 17 | collection files, status ledgers, snapshot bundles (each sub-snapshot uses its own `captured_at`); typed bundle snapshots emit `Coverage` |
| `documents/*.pdf` | `documents.parse` | 4 | PyMuPDF text layer; recognized by title; register rows derive `legal_hold_N` from `ORD-2026N` (`derived_from`); a statement is emitted under its reference for alias resolution |
| `images/*.png` | `images.parse` | 11 | Gemini transcription (`Screen` schema), then deterministic mapping and alias observations |

Shared helpers: `logfmt.parse_line/convert` (quoted literals convert like unquoted ones),
`records.collection_observation/status_record_observation` (one collection-to-system/category map),
`envelopes.LogEvent`.

### 4.3 Parser invariants (verified)

- An observation's time is the record-level time when the source has one, else the file's capture time.
- Replica rows keep their origin system and resource ID; the copy is recorded in `attrs["role"]`.
- No parser guesses a resource ID from a name. IDs come from the source, from an explicit document
  reference, from the order-number convention (marked `derived_from`), or from catalog aliases
  resolved later in `ResourceIndex`.

### 4.4 Tests

- `scripts/check_evidence.py`: all 94 files open.
- `scripts/check_parsers.py`: every gold evidence citation is supported (75/75).
- `scripts/check_vision.py`: hand reading of the screenshots (32/32 resources, 52/52 fields).
- `regression_tests.py`: `malformed_evidence` (synthetic).

### 4.5 Limitations

- Parsers are written per format family seen in the public data; a new format needs a new parser
  (falls through as "no parser").
- PDF and screenshot recognition is title- and schema-based.
- Order-number derivation rests on 4 observed pairs.

---

## 5. Identity resolution and resource attribution

### 5.1 Identity (verified, `audit/identity.py`)

- `IdentityResolver.__init__` indexes current emails and every `email` old/new value from the audit
  log (`by_email`, lower-cased).
- `resolve(email, as_of)` returns the first sorted candidate user. Every request in the dataset
  matches exactly one user (measured); multi-match handling is a known simplification.
- `_identity(user, as_of)` starts from the snapshot. Changes **after `as_of`** are rolled back
  (remove `new_value`, add `old_value`); changes at or before `as_of` contribute both values and
  their `audit_id`s.
- Emails are lower-cased; phones are reduced to digits; hashes include `sha256` of every email
  (exact and lower-case).
- **Names are never matched.**

Tests: `historical_email_and_hash`, `uppercase_identifier`, `decoy_lookalike`.

### 5.2 Evidence linking (verified, `audit/linking.py: ResourceIndex`)

- **Aliases first**: `LINK_RESOURCE_IDENTITY` details like `linked X (ALIAS)` /
  `confirmed X (ALIAS)` build `aliases[ALIAS] = (system, X)` when ALIAS differs from X (3 in the
  data: `delivery_confirmation_19.pdf` to `object_19`, `NST-2026-0718-1842` to `export_1842`,
  `user_17` to `tombstone_17`). Observations whose `resource_id` is an alias are rewritten.
- **Inverted index**: every normalized identifier value (user ref, email, hash, `tel:` digits)
  maps to `Link(resource, source_id, at, value)`, with `at = observation.observed_at`.
  Catalog events add links at their event time.
- `linked(identity, as_of)` returns resources whose links have `at <= as_of`.
- `workflow_ops[resource]` records which operations the deletion worker attempted (a category hint).
- `available_at(item) = max(item.observed_at, captured[file_id])` (see §6).

### 5.3 Workflow attribution (verified, `WorkflowAttributor`)

For records with no identifier left:

1. **Window**: deletion-worker actions in `[reported_at − 80 min, reported_at + 10 min]`. Measured on
   the samples: actions occur 45-75 min before `reported_at`; the +10 min lag accommodates a few
   early samples.
2. **Candidates per target system** (`_compute_candidates`):
   - A resource evidence links to this subject wins (`mine`).
   - If evidence gathered after `requested_at` already shows the subject's record in this system
     (`_evidenced_systems`), the subject claims no unnamed resource there.
   - Resources linked to *other* subjects are excluded.
   - For `account_service`, the action's `reported_result` must match the case's status via
     `_STATUS_RESULT` (`COMPLETE→SUCCESS, PARTIALLY_COMPLETE→PARTIAL, NOT_STARTED→FAILED`). This held
     for all 32 samples.
3. **Connected component** (`_component`): breadth-first expansion over every request whose window
   contains any candidate's action time.
4. **Maximum bipartite matching** (`_max_matching`, Kuhn augmenting paths; `_decide_by_matching`):
   per system, subjects are on one side and candidate resources on the other. A subject gets a
   resource **only if** it is matched in every maximum matching (removing it lowers the maximum by
   1) **and** exactly one resource can be its partner in some maximum matching. Otherwise the
   result is `ambiguous`, and no step is emitted.

Ownership uses evidence linked at or before the case's `audit_at` (owners memoized per `as_of`).

Tests: `batch_resolved_by_matching` (case_18), `ambiguous_batch_not_guessed` (case_28),
`scripts/check_linking.py` (97/111 gold resources linked; 14 sample cases contain ties).

Limitations: the status/result correspondence is an empirical regularity of this dataset's workflow;
symmetric ties (110 evaluation cases with a tied primary account) remain unresolved by design.

---

## 6. Temporal reasoning and state reconstruction

Four times are distinguished (verified):

| Time | Used for |
|---|---|
| record `observed_at` | ownership links (`Link.at`), ordering events within a copy |
| file `captured_at` (manifest) | availability: evidence is usable for state and citations only when `max(observed_at, captured_at) <= audit_at` |
| `requested_at` | evidence settled before the request doesn't establish state for it (`_decide_resource`: `state.at < requested_at` means skip, unless workflow-attributed) |
| `audit_at` | the cutoff for everything |

`policy.state_at(key, observations, coverage, as_of, available, use_omissions)`:

- **Per-copy events**: `_observation_copy` (from `attrs["role"]`) and `_coverage_copy` (from the table
  name) map evidence to `primary` or a named replica (`read_replica`, `failover_replica`,
  `reporting_replica`, `replica_<region>`). Each available item becomes a `seen`, `gone` or
  `omitted` event for its copy.
- **Omissions** are added only when `use_omissions` is true (DELETE-category records), and only for
  copies with no direct observation: listings are filtered views.
- **Per copy**: if the latest event includes `seen`, the copy is present; else absent (events after
  the last sighting).
- **Record state**: it exists if **any** copy is present, citing the earliest `file_id` among the
  newest present observations. When absent, the latest direct `gone` (e.g. HTTP 404) is cited, else
  the latest omission listing.

Tests: `evidence_after_audit`, `absent_at_audit_reappeared_later`, `replica_survives_primary_delete`,
`full_snapshot_omission_proves_deletion`, `filtered_listing_cannot_remove_retained_record`,
`contradictory_sources` (synthetic).

---

## 7. Policy engine and planning algorithm

### 7.1 Category and rule (verified, `audit/policy.py`)

- `category_of`: a `contact_lookup_completed` event means `SECONDARY_IDENTIFIER`. Otherwise the
  newest explicit `data_category`. Otherwise a support record the worker tried to `DELETE_RECORD`
  is `SUPPORT_ATTACHMENT`. Otherwise `_SYSTEM_CATEGORY[system]`.
- `load_rules`: `deletion_rules.csv` category to action, plus `DATA_FLOW → DISABLE` and
  `BACKUP_ARCHIVE → BLOCK` (retain with restore blocked).
- `_retention_lapsed`: if the latest available `retention_until`/`retained_until` is before
  `audit_at`, `RETAIN` becomes `DELETE` (rules 12 and 13). No public record has lapsed by its audit,
  so this is covered only by the synthetic test.

### 7.2 Decision per resource (`_decide_resource`, `decide`)

Skip when:
- the resource ID isn't of the form `name_N` (an unresolved alias);
- all observations are `derived_from`;
- the category is outside the policy.

Otherwise:
- **No state** → `ABSENT` with no citation if workflow-attributed and DELETE-category; `RETAINED`
  with no citation if evidence-linked and RETAIN; else skip.
- **State settled before the request** → skip (unless workflow-attributed).
- **DELETE** → `REMAINING` if present, else `ABSENT`.
- **REDACT** → `EXPOSED_REDACT` if any direct identifier (a `user_ref` is allowed), else `REDACTED`.
- **ANONYMIZE** → `EXPOSED_ANONYMIZE` if any direct identifier **or** `user_ref`, else `ANONYMIZED`.
- **RETAIN** → `RETAINED`.
- **BLOCK** → `RESTORE_RISK` if any `restore_allowed` is true **or** no restore flag is known, else
  `BLOCKED`.
- **DISABLE** → `ACTIVE_FLOW` if any `enabled`, else `DISABLED` (skip if unknown).

`plan_resource_actions`: decide every evidence-linked resource; then add workflow-attributed
resources **only for systems not already covered** by an evidence decision.

### 7.3 Planning and contract (verified, `planner.py`, `schema.py`)

- `build_steps`:
  - **Violations** → `INCOMPLETE`: finding (cited) and remediation (uncited) per violation, then
    the verification op of **every** decision (uncited).
  - **No violations** → `COMPLETE`: verification ops only for decisions with an `evidence_id`.
- `schema.OPERATIONS` is the authoritative operation → (phase, state) table; dict order is the
  within-phase order.
- `order_steps` sorts by (phase index, operation rank, system_id, resource_id) and drops duplicates.
- `serialize_steps` numbers from 1 and writes compact JSON.
- `validate_row` checks status labels, JSON array shape, consecutive numbering, known operations,
  phase/state agreement, evidence rules per status, and canonical order.
- `check_output.py` adds: exact header, one sorted row per request, the exact step-field set,
  existing evidence IDs, and no citation captured after `audit_at`.

**Known gap (verified by reading `build_steps`)**: a deletion request with no decisions at all would
yield `COMPLETE` with `[]`, which `validate_row` flags. `main.py` reports it but still writes the
row. It does not occur in the final output (0 invalid rows).

---

## 8. LLM integration and deterministic safeguards

`audit/llm.py` (verified):
- `generate_json(purpose, prompt, schema, images, namespace, model)`: Gemini structured output
  (`response_mime_type="application/json"`, `response_schema=<pydantic model>`), `temperature=0.0`,
  `seed=7`, and `_thinking(model)` (`thinking_level="low"` for Gemini 3; `thinking_budget=0` for 2.5).
- **Cache**: `code/.cache/<namespace>/<sha256(model, thinking, prompt, schema JSON, input hashes)[:32]>.json`
  stores model, purpose, usage and the validated answer. Hits are recorded in the ledger as `cached=True`.
- **Ledger**: `LEDGER` records `purpose, provider, model, input_tokens, output_tokens` (thinking
  tokens counted as output) and `cached`.
- **Failure handling**: daily-quota errors (`"PerDay"`) raise immediately; 429/5xx/`UNAVAILABLE`/
  `RESOURCE_EXHAUSTED` back off for 15, 30, 60 and 90 s; anything else is raised.
- **Key handling**: from `.env` (gitignored) via `python-dotenv`; never logged. Cached runs don't
  need the SDK client or a key.

Uses:
- **`images.py`**: `gemini-3.8-flash` fills the `Screen` schema (application, breadcrumb, title,
  subtitle, badge, search query, cards, table rows, detail panel, activity). The prompt says to
  transcribe exactly and not interpret. `screen_observations` applies the linking rules (title
  subjects carry identifiers; other page IDs are link-only; row IDs carry only row identifiers);
  `_alias_observations` emits file-name rows and bare `user_NN` pages.
- **`intent.py`**: `gemini-3.5-flash-lite`, 100 texts per call, one worker. The `Verdict` has
  `language`, `request_type` (6-value literal) and `authorizes_deletion`. Missing verdicts raise.

**Safeguards**: models never decide ownership, state or policy. Outputs are schema-validated
(pydantic). Vision output is interpreted by deterministic rules. Measured accuracy: vision 32/32
resources and 52/52 fields; intent 398/398 texts.

**Not implemented**: prompt-injection detection, a second-model cross-check, confidence scores.

---

## 9. Evaluation methodology and measured results

### 9.1 Metric definitions (`code/evaluation/main.py: evaluate`)

- **Status accuracy**: share of cases whose `deletion_status` equals gold.
- **Step precision / recall**: steps compared as multisets of
  `(phase, system_id, resource_id, operation, state, evidence_id)` (`step_no` excluded);
  TP = multiset intersection; precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = harmonic mean.
- **Field accuracy**: position-aligned comparison of the two step lists, per field, divided by the
  longer list's length.
- **Sequence accuracy**: status equal **and** the ordered key lists identical.
- **Missing / extra / misordered**: unmatched gold, unmatched predicted, and cases where the
  multisets are equal but the order differs.

### 9.2 Results (verified)

| Check | Result |
|---|---|
| Samples | 100% status; P/R/F1 100/92.7/96.2; sequence 59.4%; 14 missing, 0 extra, 0 misordered |
| Field accuracy | phase 92.7, system_id 79.8, resource_id 79.8, operation 81.3, state 81.3, evidence_id 92.2 (%) |
| Custom cases (`custom_cases.py`) | 6/6 |
| Regression suite (`regression_tests.py`) | 20/20 over 15 scenarios (3 synthetic) |
| Components | parsers 75/75; vision 32/32 + 52/52; intent 398/398 texts, 550/550 requests; link recall 97/111 |
| `output.csv` | 518 rows (355 INCOMPLETE / 153 COMPLETE / 10 NOT_APPLICABLE), 1,118 citations, 0 problems |

Why 100% status but not 100% steps: status depends only on whether *any* violation is found, while
steps must list every relevant record. All 14 missing steps are verifications of records the agent
cannot attribute. No missing step changes a status, and no wrong step exists.

### 9.3 Remaining public-sample discrepancies

| Missing | Cause |
|---|---|
| `account_004` (case_04); `audit_record_6645` (case_18); `audit_record_5569` (case_20) | Not present in any evidence file or activity row (verified by grep) |
| analytics in case_03, 21, 25, 26, 27, 28; accounts in case_23, 28, 29, 31, 32 | Symmetric batch ties (identical candidate sets, no identifiers); in case_31/32 the disambiguating owners appear only in the 10-01 handover, after their 09-15 audits |

---

## 10. Error analysis and evolution

Sample metrics after each change during development:

| Stage (commit) | Status | P / R / F1 | Sequence |
|---|---:|---:|---:|
| Skeleton | 31.2% | n/a / 0 / 0 | 0% |
| First policy + planner | 81.2% | 73.6 / 60.6 / 66.5 | 28.1% |
| File availability, per-copy state, workflow fills gaps | 93.8% | 96.8 / 77.2 / 85.9 | 25.0% |
| Copy-detection bug fix; omissions only for DELETE | 96.9% | 98.8 / 85.5 / 91.7 | 46.9% |
| Record-time ownership; direct reads beat listings | 96.9% | 98.9 / 90.2 / 94.3 | 56.2% |
| Catalog-link indexing bug fix; attachment fallback | 100% | 100 / 92.2 / 96.0 | 59.4% |
| Alias resolution; unblocked backup = risk | 100% | 97.8 / 92.2 / 94.9 | 53.1% |
| Pre-request evidence ignored | 100% | 100 / 92.2 / 96.0 | 59.4% |
| Matching attribution, retention expiry, parse isolation | 100% | 100 / 92.7 / 96.2 | 59.4% |

Notable defects and how they were exposed:

1. **Future-dated bundle citations.** The evaluator showed `evidence_02` (exported 09-17) cited for
   09-15 audits. Fixed by `available_at`.
2. **Replicas collapsed into "primary".** A role string was passed to the table-name parser;
   case_05 showed it.
3. **A misplaced method disabled catalog-link indexing.** It surfaced as an `AttributeError`.
4. **The statement PDF was mapped to the wrong export.** Found while tracing invalid rows; fixed by
   catalog aliases.
5. **Missed forced assignment (case_18).** Fixed by maximum matching.
6. Also: a PDF parser used the document after closing it; quoted logfmt literals didn't convert; the
   intent prompt misread one deletion follow-up (fixed, 397 to 398); Gemini free-tier quotas.

---

## 11. Reproducibility and clean-checkout validation

Commands (Windows paths shown; from the repo root):

```powershell
.\.venv\Scripts\python.exe code\main.py                      # writes output.csv
.\.venv\Scripts\python.exe code\evaluation\run_all.py        # all evaluations; refreshes report RESULTS
.\.venv\Scripts\python.exe code\evaluation\regression_tests.py
.\.venv\Scripts\python.exe code\evaluation\check_output.py
.\.venv\Scripts\python.exe code\scripts\explain_case.py case_26
```

Verified:
- Two full runs produce byte-identical output.
- A fresh clone of the upstream challenge repository, with `code/` and `README.md` replaced by the
  final `code.zip`, a new virtualenv and **no `.env` or `GEMINI_API_KEY`**, reproduced `output.csv`
  byte for byte (sha256 `3543cbf5...`), with 15 cached calls and 0 live.

Determinism comes from the answer cache, sorted iteration, and stable serialization. (This public
repository omits the cache because it is derived from the challenge data; the first run with your own
key rebuilds it.)

---

## 12. Security, privacy, cost and reliability

- **Secrets**: the key lives only in `.env` (gitignored; never read into logs). `package_submission.py`
  builds `code.zip` from git-tracked files, rejects nested archives and excluded directories, and
  scans for Google-key-shaped strings.
- **Privacy**: the dataset is fictional. Screenshots and request texts were sent to Gemini's public
  API. **Recommendation** for real data: a no-retention enterprise endpoint or a local model, plus
  PII minimization before calls.
- **Cost**: 15 calls and about $0.08 per full run at list price. Deduplication (550 to 398 texts),
  batching and low thinking cut tokens (one screenshot: 2,427 to 523 output tokens with an identical
  transcription).
- **Reliability**: per-file parse isolation; contract validation per row; fail-fast quota handling;
  keyless cached reruns. Untrusted model input (request text, screenshots) is constrained by schemas
  but not checked for injected instructions.

---

## 13. Known limitations and future improvements

1. Symmetric workflow ties: no step is emitted (110 evaluation cases with a tied primary account).
   *Next*: look for further non-temporal signals; report ties in a side channel.
2. Three sample records exist nowhere in participant data.
3. Rules derived from 32 public samples (e.g. the status/result correspondence, the 80-minute window)
   may not transfer. *Next*: larger synthetic perturbation tests.
4. A request with no linkable evidence would produce a contract-invalid `COMPLETE []` (not present in
   the data). *Next*: decide an explicit policy for it.
5. `resolve` takes the first candidate if an email ever maps to several users (not present).
6. No prompt-injection defence or second-model cross-check for intent and vision.
7. Retention-expiry logic is tested only synthetically.
8. Prices come from third-party aggregators.

---

## 14. Source-file and test references

| Area | Source | Tests / checks |
|---|---|---|
| Entry point | `code/main.py` | full run; `check_output.py` |
| Data loading | `code/audit/data.py` | all runs |
| Evidence model | `code/audit/evidence/model.py`, `records.py` | `check_parsers.py` |
| Parsers | `code/audit/evidence/{service_export,lookup_trace,handover,obslog,envelopes,logfmt,sql_snapshot,json_records,documents,images}.py` | `check_evidence.py`, `check_parsers.py`, `check_vision.py`, `malformed_evidence` |
| Identity | `code/audit/identity.py` | `historical_email_and_hash`, `uppercase_identifier`, `decoy_lookalike` |
| Linking / attribution | `code/audit/linking.py` | `check_linking.py`, `batch_resolved_by_matching`, `ambiguous_batch_not_guessed` |
| Policy / temporal state | `code/audit/policy.py` | tests 4-11, 15 in `regression_tests.py`; custom cases |
| Planner / schema | `code/audit/planner.py`, `code/audit/schema.py` | `validate_row` on every row; `check_output.py` |
| Models | `code/audit/llm.py`, `intent.py`, `evidence/images.py` | `check_intent.py`, `check_vision.py` |
| Evaluation | `code/evaluation/{main,custom_cases,regression_tests,check_output,run_all,usage_report}.py` | `run_all.py` |
| Packaging / debugging | `code/scripts/package_submission.py`, `explain_case.py` | clean-checkout run |
