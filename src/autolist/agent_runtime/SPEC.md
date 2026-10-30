---
module: agent_runtime
status: not-started      # written by Claude Code at the start of phase 3, approved by DEV before code
phase: 3
---

# agent_runtime

## Purpose
Maps a classified owner message to exactly one tool from the closed set in `$AUTOLIST_STORE_DIR/agent/tools/`. It enforces guardrails (version-bound approval, out-of-scope refusal, max 3 revision rounds) and keeps per-thread memory in Firestore. It never executes store writes itself: it returns a tool call for the orchestrator.

## Planned files
dispatcher.py, guardrails.py, thread_memory.py

## Inputs
TBD in this SPEC before code.

## Outputs
TBD.

## Side effects
TBD. Default: none (pure).

## Errors
TBD.

## Allowed dependencies
`autolist.core`, `autolist.llm`.

## Not responsible for
TBD.

## Acceptance criteria
Taken from the phase file in the store repo, then refined here.
