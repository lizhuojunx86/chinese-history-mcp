# Security

## Threat surface

This is a **read-only, local** MCP server:

- It opens the corpus database with `mode=ro` + `PRAGMA query_only` and never
  writes.
- It has **no network access** and **no third-party dependencies** (Python
  standard library only), so there is no supply chain to compromise.
- It speaks JSON-RPC over stdio to a local MCP client you configure.

The main realistic concern is **data quality**, not code exploitation:
machine-adjudicated content could contain errors. Every response labels its
`review_status` for exactly this reason — see the honesty notes in the README.

## Reporting a vulnerability

If you find a genuine security issue (e.g. a path that could write to the DB,
or crash the host client), please open a
[GitHub issue](https://github.com/lizhuojunx86/chinese-history-mcp/issues) with
a reproduction. For anything you consider sensitive, mark it clearly and we can
move to a private channel.
