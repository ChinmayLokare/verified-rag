# VerifiedRAG

Agentic RAG over documents that mix prose and structured data. Every number and claim in an answer is traced to its source, and the system refuses when it can't verify. The first domain pack is SEC filings (10-K, 10-Q).

See [docs/design.md](docs/design.md) for the architecture and build plan, and [docs/code-map.md](docs/code-map.md) for diagrams of how the code connects.

**Status:** M0 (repository skeleton). No results yet; numbers will appear here only once they come from an eval run.

## Local development

Requires Python 3.12 (uv installs it), [uv](https://docs.astral.sh/uv/), and Docker.

```sh
cp .env.example .env
docker compose up -d     # Postgres 17 + pgvector on localhost:5433
uv sync
uv run vrag migrate
uv run pytest
```
