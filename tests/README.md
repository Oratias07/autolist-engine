# tests

Mirrors `src/autolist/`.
- Fixtures here are **synthetic**, with generic values (`example.com`, `company_id: 1`), shaped like the real responses recorded in the client's store repo. Real responses never enter this public repo. gitleaks scans every change.
- `@pytest.mark.live`: reserved for tests that call real services. They never run in CI. Live checks in phase 0 run as `scripts/spike/*` through the store repo's `live` workflow.
- Golden products live in the store repo, under `playbook/golden/`. The regression runner arrives in phase 5.
