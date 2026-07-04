# Contributing

Thanks for your interest. This is a small, focused project — a read-only,
zero-dependency MCP server over a classical-Chinese corpus.

## Development

Requires Python 3.9+ (standard library only — nothing to `pip install` to run
or test).

```bash
# Run the test suite (builds its own fixture DB — no corpus.db needed)
python tests/test_mcp_server.py

# Lint
pipx run ruff check src tests scripts

# Run the server against a corpus (download corpus.db from Releases first)
PYTHONPATH=src python3 -m storyextractor.mcp.server --db /path/to/corpus.db
```

## Principles this project holds to

- **Zero runtime third-party dependencies.** The server is standard library
  only (no MCP SDK). Please don't add runtime dependencies.
- **Read-only.** The server must never write to the corpus (`mode=ro` +
  `PRAGMA query_only`).
- **Every result is cited** with 【book → chapter → paragraph】.
- **Honest labeling.** Results carry their real `review_status`. Do not add
  tools or fields that present machine-adjudicated / draft data as if it were
  individually human-reviewed.
- **Tests only grow.** Add tests; assert invariants rather than freezing
  incidental values.

## Pull requests

1. Keep changes focused; open an issue first for anything non-trivial.
2. Make sure `python tests/test_mcp_server.py` and `ruff check` pass.
3. Add a test for new behavior.
4. Update `CHANGELOG.md` under an "Unreleased" heading.

## Reporting issues

Use the issue templates. For anything data-quality related (a wrong citation, a
bad place mapping, a dubious event), please include the tool call, the returned
`review_status`, and the expected source — it helps triage machine-vs-human
provenance.
