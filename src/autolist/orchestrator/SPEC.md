---
module: orchestrator
status: not-started      # written by Claude Code at the start of phase 1, approved by DEV before code
phase: 1 (skeleton in P0-09)
---

# orchestrator

## Purpose
The only package allowed to import feature modules. It wires them together: it receives channel events, drives the job state machine (core), calls feature modules in order, and hands results to channels and publish. It contains no business logic: no prompt text, no product rules, no field decisions. Cloud Run entrypoints (Pub/Sub push, Cloud Tasks handlers, Scheduler jobs) live here.

## Planned files
orchestrator.py, entrypoints.py, tasks_queue.py

## Inputs
TBD in this SPEC before code.

## Outputs
TBD.

## Side effects
TBD. Default: none (pure).

## Errors
TBD.

## Allowed dependencies
`autolist.core`, `autolist.llm` + all feature modules.

## Not responsible for
TBD.

## Acceptance criteria
Taken from the phase file in the store repo, then refined here.
