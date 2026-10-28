# Ghost Data Audit Agent

An AI-assisted agent that audits whether a customer's personal data was **really** deleted.

Built by **Vishwas** ([@vishwasvishubm](https://github.com/vishwasvishubm)) for the
**HackerRank Orchestrate (October 2026)** 24-hour hackathon challenge *"Ghost Data Still Exists"*.

---

## The problem

An online shop's deletion workflow tells a customer "your data is deleted", but copies survive in
database replicas, search indexes, support tickets, analytics, exports and backups, and background
sync or restore jobs can bring deleted data back. That leftover data is "ghost data".

For each privacy request, the agent must decide:

- whether the person actually asked for deletion (otherwise `NOT_APPLICABLE`);
- whether the deletion is really `COMPLETE` or `INCOMPLETE` at the audit time;
- which records still expose the person, and which processes could recreate their data;
- the ordered steps to fix it (findings, remediation, verification), each grounded in evidence.

## How it works

```
request ──► intent ──► identity ──► evidence + linking ──► policy ──► planner ──► output row
           (Gemini)   (point-in-time)                     (rules)    (ordering + validation)
```

1. **Intent.** Gemini classifies the request text in any language. Access requests, corrections and
   marketing opt-outs become `NOT_APPLICABLE`.
2. **Identity.** Rebuilds every email, phone and email hash the person had *as of the audit time*
   from the identity snapshot plus its change history. Names are never matched, because the data
   contains look-alike decoys.
3. **Evidence and linking.** About ten evidence formats (SQL primary/replica snapshots, JSON exports,
   six log formats, PDFs, screenshots) are parsed into one normalized `Observation` model. Records
   are linked to the person through exact identifiers and data-catalog aliases. For deleted records
   with no identifier left, deletion-workflow activity is attributed to requests with a **maximum
   bipartite matching**, assigning only when the assignment is forced and never guessing ties.
4. **Policy.** Each record's state is reconstructed *per physical copy* (service and each replica)
   using only evidence that existed by the audit time, then the deletion rules decide: delete,
   redact, anonymize, retain, block restore, or disable a sync flow.
5. **Planner.** Emits ordered findings, remediation and verification steps, and validates every row
   against the output contract.

Gemini is used for only two narrow jobs (transcribing screenshots into a fixed schema, and
classifying intent). Every decision about ownership, state and policy is deterministic Python.
Model answers are cached, so reruns are free and repeatable.

Full write-up: [`docs/technical_architecture_and_development.md`](docs/technical_architecture_and_development.md).

## Results (public sample set)

| Metric | Value |
|---|---|
| Deletion-status accuracy (32 sample cases) | **100%** |
| Step precision / recall / F1 | **100% / 92.7% / 96.2%** |
| Exact step-sequence match | 59.4% |
| Regression and adversarial tests | 20/20 pass (15 scenarios) |
| Output contract checks (518 rows) | 0 problems |
| Model usage for a full run | 15 Gemini calls, about 54k tokens, about $0.08 at list price |

These are local measurements on the public sample answers; they don't predict the challenge's
hidden evaluation. Details: [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md).

## Getting started

Requirements: Python 3.12, and a Gemini API key for the first run.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r code/requirements.txt     # Windows
# .venv/bin/python -m pip install -r code/requirements.txt       # macOS / Linux
```

**Dataset.** The challenge data belongs to HackerRank and is not redistributed here. Copy the
`dataset/` folder from the official challenge repository,
[interviewstreet/hackerrank-orchestrate-october26](https://github.com/interviewstreet/hackerrank-orchestrate-october26),
into the root of this repository.

**API key.** Copy `.env.example` to `.env` and set `GEMINI_API_KEY`. The first run makes about 15
model calls and caches the answers in `code/.cache/`; later runs need no key.

```bash
python code/main.py                       # writes output.csv
python code/evaluation/run_all.py         # full evaluation (samples, custom cases, regression tests, checks)
python code/evaluation/regression_tests.py
python code/scripts/explain_case.py case_26   # how the agent reasons about one case
```

## Project structure

```
code/
  main.py              entry point: requests -> output.csv
  audit/               the agent: intent, identity, linking, policy, planner, schema, llm
    evidence/          one parser per evidence format, normalized into Observation/Coverage
  evaluation/          scorer, custom cases, regression suite, output checks, reports
  scripts/             component checks, case explainer, screenshot prefetch, packaging
docs/
  technical_architecture_and_development.md
```

## Known limitations

- When several requests in one deletion batch are indistinguishable, the agent leaves those records
  out rather than guessing, trading some recall for never attributing someone else's data.
- Some rules (for example the workflow-timing window) were derived from the public samples.
- Request texts and screenshots are untrusted model input; there's no dedicated prompt-injection defence.

## How I built it

I designed and built this agent during the 24-hour hackathon, using **Claude Code** (Anthropic's
AI coding agent) as a pair programmer. I chose the stack (Python, Gemini), set the plan and pace,
directed the work step by step, reviewed the results, and required that every stage be measured
against the sample answers and independent checks before moving on. At runtime the agent uses
**Google Gemini** (`gemini-3.8-flash`, `gemini-3.5-flash-lite`).

## Acknowledgements

The challenge, rules and dataset are by HackerRank (Orchestrate, October 2026). This repository
contains only my solution code and documentation.
