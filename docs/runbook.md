# Runbook

Filled in at M6 (design.md section 12): provider outage, parser failure on a new document format, spend spike, and a bad deploy.

## Local development

```sh
docker compose up -d        # Postgres 17 with pgvector on localhost:5433 (VRAG_PG_PORT)
uv sync                     # install dependencies into .venv
uv run vrag migrate         # apply schema migrations
uv run pytest               # unit + integration tests (integration skips if Postgres is down)
```
