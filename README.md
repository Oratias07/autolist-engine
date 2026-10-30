# autolist-engine

Email-driven product listing agent. The owner emails a product or a supplier invoice. The agent researches it, writes the copy, generates images, and sends a preview. It publishes to the store only after the owner approves that exact version.

- **Engine (this repo):** generic code, no client data. Private.
- **Store repo (one per client):** playbook, agent tools/prompts/replies, docs, `store.yaml`, task lists.

## Local setup
```bash
git clone <engine> autolist-engine
git clone <store-repo> <store-repo-dir>
cd autolist-engine
export AUTOLIST_STORE_DIR=../<store-repo-dir>
uv sync
make check
claude --add-dir "$AUTOLIST_STORE_DIR"     # Claude Code; reads CLAUDE.md first
```

GCP credentials: `gcloud auth application-default login` with **your own** account. Secrets are read from Secret Manager at runtime. Nothing goes in `.env`.

## Rules
See `CLAUDE.md`. In short:
- Do not assume or guess.
- Write the SPEC before the code.
- Modules import only `core` and `llm`.
- Only `channels` sends messages and only `publish` writes to the store.
- Docs change in the same commit as the code.
