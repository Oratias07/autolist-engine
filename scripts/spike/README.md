# scripts/spike (phase 0 only)

Thin CLIs for discovery. Each script:
- calls real modules (`publish.istores_client`, `llm.client`, ...) and never makes raw HTTP calls
- writes its findings to a file (fixtures or a markdown report), never only to stdout
- for any store write, prints the exact request body and asks for an interactive `yes` before sending

These scripts are deleted, or promoted into a module, at the end of phase 0.
