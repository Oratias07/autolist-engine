---
module: llm
status: approved
approved_by: DEV (Or)
approved_at: 2026-10-30
phase: 0
---

# llm

## Purpose
A single interface for text-model calls. The rest of the system never imports a provider SDK directly. Swapping the provider or model is a change in one adapter file plus `store.yaml`.

## Files
| File | Responsibility |
|---|---|
| `client.py` | `LLMClient` protocol plus `AnthropicClient` adapter. |
| `prompt_loader.py` | Loads a prompt file from `$AUTOLIST_STORE_DIR/agent/prompts/<name>.md` and renders `{{variables}}`. |

## Interface
```python
class LLMRequest(BaseModel):
    prompt: str                       # prompt file name, e.g. "extract_request"
    variables: dict[str, str]         # rendered into the prompt; untrusted text goes ONLY here
    output_schema: type[BaseModel] | None   # when set: strict JSON, validated
    model_role: Literal["extract", "write", "classify"]   # mapped to a model id in store.yaml
    job_id: str | None
    max_output_tokens: int

class LLMResult(BaseModel, Generic[T]):
    parsed: T | None; text: str | None
    model: str; input_tokens: int; output_tokens: int; usd: Decimal

class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResult: ...
```

## Behavior
- **Prompt files** have front matter with `name`, `version`, `variables: [...]` and `untrusted: [...]`.
  - Rendering fails if a declared variable is missing, or if an undeclared one is passed.
  - Untrusted variables are wrapped in delimiters (`<untrusted name="…">…</untrusted>`), with any closing tag inside them escaped. The prompt text tells the model that delimited content is data.
- **Structured output.** When `output_schema` is set, the adapter asks for JSON only and validates it.
  - One automatic repair attempt: the validation error is sent back once.
  - A second failure raises `LLMOutputInvalid`, keeping the raw text for the job log.
- **No tool use.** Extraction and classification calls get no tools.
- **Cost.**
  - Before the call: `cost_ledger.authorize()`, with an estimate from the input length and `max_output_tokens`.
  - After the call: `record()`, with real token counts and prices from `store.yaml` (`llm.prices`).
  - Refused authorization raises `CostCapExceeded`, and no request is sent.
- **Retries.** 429, 529 and 5xx get exponential backoff, at most 3 tries, honoring `retry-after`. Other 4xx errors are not retried.
- **Model ids** come from `store.yaml` (`llm.models.extract|write|classify`). There are no hard-coded ids and no fallback model.
- **API key.** Read from Secret Manager by the name in `store.yaml` (`secrets.anthropic_api_key`). Never logged.

## Side effects
One HTTPS call per attempt to the provider. Cost ledger entries.

## Errors
`LLMOutputInvalid`, `LLMUnavailable` (retries exhausted), `CostCapExceeded`, `PromptVariableError`, `ConfigMissing`.

## Allowed dependencies
`autolist.core`, `anthropic` SDK, `jinja2` (sandboxed environment, autoescape off, `StrictUndefined`) or `str.format_map` with a strict dict.

## Not responsible for
- What the prompts say (store repo).
- Image generation (`images`).
- Deciding which model is better. That is the phase-0 bake-off, recorded in decisions.

## Acceptance criteria (P0)
1. A prompt with a missing declared variable raises `PromptVariableError` (unit test).
2. Untrusted text containing `</untrusted>` and "ignore previous instructions" is delimited and escaped. This is a snapshot test of the rendered prompt.
3. With a mocked transport, an invalid-JSON response triggers exactly one repair call and then `LLMOutputInvalid`.
4. Cost: a call refused by the ledger sends zero HTTP requests (transport mock asserts no call).
5. Live smoke (manual, P0-06): one `extract` call on a real Hebrew sample returns schema-valid JSON. The logged cost entry matches the Console usage within 5%.
