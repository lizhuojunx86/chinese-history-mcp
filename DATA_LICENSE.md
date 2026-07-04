# Data license

The **source code** in this repository is licensed under **MIT** (see
[LICENSE](LICENSE)).

The **corpus database** (`corpus.db`, distributed separately as a GitHub
Release attachment — not committed to this repository) is licensed under
**Creative Commons Attribution 4.0 International (CC BY 4.0)**:
https://creativecommons.org/licenses/by/4.0/

## What the corpus is

- **Original text**: public-domain classical Chinese base text (白文), with
  **self-produced, machine-generated punctuation and segmentation** — not
  copied from any modern annotated/collated edition.
- **Vernacular translation**: **machine-generated** across the whole corpus.
- **Structured annotations** (events / entities / places / qualities):
  machine-assisted, with human-review gating on selected layers. Each record
  carries its own `review_status`.

Machine-generated attributes are labeled throughout the data and in every
server response (AIGC-compliant). "Not found" means "not in this corpus," not
"did not happen."

## Attribution

If you use the corpus, please credit: *chinese-history-mcp corpus (CC BY 4.0)*,
with a link to this repository.
