# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-07-04

### Added
- Published to **PyPI**: `pip install chinese-history-mcp` (zero dependencies;
  console script `chinese-history-mcp`).
- MCP registry manifest (`server.json`) + `mcp-name` ownership marker, and a
  GitHub Actions workflow that publishes to the official MCP registry on version
  tags via OIDC (no interactive login).

### Changed
- README install section leads with `pip install` / `uvx`; added PyPI badge.

## [0.1.0] — 2026-07-04

Initial public release.

### Added
- Read-only **MCP server** over `corpus.db` — pure Python standard library,
  hand-written stdio JSON-RPC 2.0 (no MCP SDK). Opens `mode=ro` +
  `PRAGMA query_only`; never writes.
- Four tools, each returning 【book → chapter → paragraph】 citations and an
  honest `review_status`:
  - `search_events` — cross-book fused events with per-source provenance
  - `get_person` — profile + others' appraisals + qualities + events
  - `query_by_place` — ancient stories by modern place name (disambiguates
    same-name places instead of guessing)
  - `query_by_quality` — representative events/people for a quality, with
    original-text evidence
- **Corpus v0.1** — 9 classical texts (pre-Qin to Wei-Jin), distributed as a
  GitHub Release (`corpus.db`, CC BY 4.0). Public-domain 白文 with
  machine-generated punctuation/segmentation and machine translation;
  machine-adjudicated annotations, honestly labeled.
- Test suite (`tests/test_mcp_server.py`) — read-only enforcement, JSON-RPC
  protocol shapes/error codes, honest `review_status`, alias token-exact
  matching + disambiguation, LIKE-wildcard escaping. Builds its own fixture,
  so it runs without `corpus.db`.
- Demo script (`scripts/mcp_demo.py`, also a minimal MCP-client reference) and
  a bare-LLM-vs-server hallucination comparison (`docs/MCP_DEMO.md`).

[0.1.1]: https://pypi.org/project/chinese-history-mcp/0.1.1/
[0.1.0]: https://github.com/lizhuojunx86/chinese-history-mcp/releases/tag/v0.1.0
