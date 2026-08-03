# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-08-03

### Added
- **Story-layer quality tags** in `query_by_quality`: a new `stories` array
  alongside the existing `events`/`persons` sections, backed by the
  story-level quality extraction landed 2026-08-03 (paragraph-granularity
  evidence, same `evidence_quote`/`review_status`/confidence shape as the
  other two sections). Empty-result and not-found responses now also return
  `stories: []` for a stable shape.

### Data
- `data-v0.3.0` corpus.db release adds the story-quality edges (11k+ edges
  over ~5.6k stories) on top of the v0.2.0 axes; schema unchanged (v11).

## [0.2.0] — 2026-08-02

### Added
- **Person-to-person relations** in `get_person`: a `relations` array over a
  closed 26-type vocabulary (kinship / ruler–minister / mentorship / alliance /
  enmity …), extracted from shared-event source text with verbatim-evidence
  anchoring and a machine review pass; only `approved`/`auto_approved` edges
  are exposed, each with confidence and literal `review_status`. Edges carry
  no temporal bounds (an ally edge and an enemy edge may both be true at
  different times).
- **Derived time labels** in `search_events`: when the hand-filled
  `time_label` is empty, a label is derived from reviewed time anchors
  (scope-priority chapter > book > global; genuine scholarly ambiguity is
  rendered as "A／B", never collapsed). `time_label_source` distinguishes
  `manual` from `derived_from_reviewed_time_anchors`.
- Optional `kind` filter on `search_events` (事件/场景/评价). Unset returns
  everything, including appraisal events migrated into the event graph.

### Changed
- Read paths now use the ADR-009 event-graph tables (`event_person_refs`/
  `event_qualities`) instead of the frozen `entity_mentions`/
  `mention_qualities` archives; person lookups are structural, not
  string-matched.
- Six same-person entity pairs merged upstream after evidence verification
  (孙权/吴主, 刘备/先主, 光武→刘秀, 公孙鞅→商鞅, 太公→吕尚, 子展/公孙舍之);
  ambiguous surface 太公 now multi-candidate (姜太公 vs 刘邦之父) and never
  silently resolved.

### Compatibility
- Running this code against the **data-v0.1.0** corpus (no ADR-009 tables)
  degrades honestly: `relations` is `[]`, derived time labels are omitted.
  For the new axes, use the **data-v0.2.0** corpus release.

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
