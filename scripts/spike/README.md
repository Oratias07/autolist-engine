# scripts/spike (phase 0 only)

Thin CLIs for discovery. Each script:
- calls real modules (`publish.istores_client`, `llm.client`, ...) and never makes raw HTTP calls
- writes its findings to a file (fixtures or a markdown report), never only to stdout
- for any store write: the exact body is approved by DEV in chat before the run, and saved next to the results

| Script | Task | Writes to the store? |
| --- | --- | --- |
| `discovery.py` | P0-05 | no |
| `hidden_product.py` | P0-06 | one hidden test product, always deleted |
| `extract_eval.py` | P0-07 | no (Claude only) |
| `email_check.py` | P0-08 | no (agent mailbox only: watch, read headers, one test email to the owner) |

They run only through the store repo's `live` workflow (WIF, manual, `main`), never from a dev session. Output goes to the store repo.

These scripts are deleted, or promoted into a module, at the end of phase 0.
