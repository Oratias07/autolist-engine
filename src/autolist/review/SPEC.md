---
module: review
status: not-started      # written by Claude Code at the start of phase 7, approved by DEV before code
phase: 7
---

# review

## Purpose
Builds the approval preview and batch digest, turns an owner reply into a structured patch (field + instruction), and issues/validates the HMAC approval token bound to job + version (single use, 72h).

## Planned files
preview.py, digest.py, reply_to_patch.py, approval_token.py

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
