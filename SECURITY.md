# Security

## Secrets never belong in the repo

All credentials load from a local `.env` file, which is gitignored. Do not commit
`.env`, `credentials.py`, `rpc_config.txt`, or any file containing a key, token,
or RPC URL with an embedded `api-key`.

A `gitleaks` pre-commit hook enforces this. Activate it once per clone:

```bash
pip install pre-commit
pre-commit install
```

`git commit` then scans staged changes and aborts if it finds a secret.

## If a key is ever exposed

Deleting the file is **not** enough — a committed secret stays in git history and
remains readable to anyone who clones the repo. The only real remedy:

1. **Rotate the key immediately** at the provider (e.g. regenerate the Helius API
   key in the dashboard). This makes the exposed value useless no matter who
   copied it.
2. Put the new key only in your local `.env`.
3. Optionally scrub history with `git filter-repo` or the BFG, then force-push —
   but treat the old key as already compromised regardless.

Rotation is the step that actually protects you. Everything else is cleanup.
