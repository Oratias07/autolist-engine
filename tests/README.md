# tests

Mirrors `src/autolist/`.
- `tests/fixtures/<provider>/`: recorded real responses, secrets stripped. CI greps for `bearer`, `sk-` and `AIza`.
- `@pytest.mark.live`: tests that call real services. Run them by hand with `pytest -m live`. They never run in CI.
- Golden products live in the store repo, under `playbook/golden/`. The regression runner arrives in phase 5.
