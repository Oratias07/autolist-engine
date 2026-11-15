---
module: agent_runtime
status: approved         # fast-track exact commands only (below), decision DEV 15.11.2026. The rest stays TBD for phase 3.
phase: 1 (fast track) / 3
---

## Fast track (phase 1) — exact reply commands (approved, DEV 15.11.2026)
File: `commands.py`. Pure.
- `parse_command(text, words) -> "approve" | "cancel" | "image_1" | "image_2" | None`: the reply (already cut of quote and signature by `channels`) is a command only when its whole text equals a command word from the store repo (`agent/commands.yaml`, loaded by `core.playbook.load_commands`). Normalizing removes direction marks, extra whitespace and one trailing period, nothing else.
- `mentions(text, word)`: used only to refuse ("מאשר" with other text never approves).
- No model is involved. A model can never approve.
- Tests: `tests/agent_runtime/test_commands.py` ("לא מאשר", "מאשר אבל…", "מאשר!", a phone signature line: none is a command).

## Fast track — reply classifier (approved, DEV 15.11.2026: classify_reply v1, model Haiku)
File: `classifier.py`. `classify_reply(message, *, llm, job_context, job_id) -> ReplyClassification` (core).
- One call on the `classify` model, strict JSON, no tools. The reply is untrusted; `job_context` is built by the orchestrator from trusted data only (state, version number, the names of known fields, no owner text).
- Intents: `facts`, `wording_or_image`, `status`, `other`, `unclear`. There is no approve or cancel intent, so a model can never approve.
- Every fact must quote the reply exactly, or it is dropped and listed in `unverified_fields`. `facts` with nothing stated becomes `unclear`.
- Live eval before first use: engine `scripts/spike/classify_eval.py` on store `fixtures/samples/p1-07-classify.yaml` (store `live` workflow, task `p1-07-classify-eval`, paid, no store writes).

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
