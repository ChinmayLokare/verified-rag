# VerifiedRAG

Agentic RAG over documents that mix prose and structured data. Every number and claim in an answer is traced to its source, and the system refuses when it can't verify. The first domain pack is SEC filings (10-K, 10-Q).

**Read [docs/design.md](docs/design.md) before making design decisions.** It holds the architecture, schema, tools, verifier, threat model, eval plan, and milestone order (section 15).

## Non-negotiable rules

- **The model never does arithmetic or states figures from memory.** Numbers come from the `facts` table; math goes through the `calculate` tool, which takes fact or calc IDs, not values.
- **No model-written SQL.** The agent fills typed parameters into fixed templates in `src/verified_rag/store/`. Bind parameters; never format values into SQL strings.
- **Data access from the agent is read-only,** through views, with a timeout and a row limit.
- **Retrieved document text is untrusted input.** Keep it labeled as content in prompts, never as instructions.
- **Nothing is returned unverified.** If the verifier can't run, refuse.
- **Domain knowledge lives in `packs/<pack>/`.** Don't hard-code finance concepts in the core.
- **No secrets in the repo** (keys, tokens, SEC contact details). Use environment config locally and SSM Parameter Store in AWS.
- **Stay within the cost budget (under $10 a month, ADR-7 in the design doc).** Don't add always-on or per-call paid services (Aurora, Fargate, NAT gateways, managed guardrails, Secrets Manager) without updating that ADR. Tests and evals must not make paid model calls unless the run is explicitly a paid eval.
- **Don't commit dependencies or build artifacts.** Dependencies are declared in `pyproject.toml` and locked by `uv`.
- **No metric goes in the README unless it came from an eval run.**

## Conventions

- Python 3.12, `uv` for dependencies, Ruff for lint and format, Pyright for types, pytest for tests.
- Stack: FastAPI and Pydantic for the API, LangGraph for the agent graph, LangChain for model and tool interfaces, LangSmith for tracing and eval experiments, the MCP Python SDK for the MCP server.
- Keep `src/verified_rag/verify/`, the calculator, and the query templates free of LangChain and LangGraph imports. Graph nodes are thin wrappers around them.
- Pin LangChain and LangGraph versions; they change often.
- Model IDs, versions, and parameters live in config, not in code. Check the provider's current docs before adding sampling parameters; newer Claude models reject some of them.
- Every change to retrieval, prompts, or models runs the eval suite (`evals/`).
- Infrastructure changes go through Terraform in `infra/`. Nothing is created by hand in the AWS console.
- Record significant decisions as a short ADR in `docs/adr/`.
- **Keep [docs/code-map.md](docs/code-map.md) current. This is mandatory.** Any change that adds, renames, or removes a module, public function, class, or Protocol, changes dependencies between packages, adds a user-facing flow (CLI command, endpoint, graph path, pipeline), or changes the schema must update the matching diagram in the same change. A change is not done until the map matches the code. The checklist is at the bottom of that file.

## Current status

M0 (skeleton), M1 (facts path), and M2 (agent and verifier) are done. M2 runs on a free local model (Ollama `qwen2.5:3b`, ADR-10); LangSmith tracing is not wired yet (needs a free key). Next is M3 (text path).

```sh
docker compose up -d          # Postgres 17 + pgvector on localhost:5433 (5432 is often taken by a native install)
uv sync                       # install dependencies (uv.lock is committed; CI uses --locked)
uv run vrag migrate           # apply migrations; `--status` to list them
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest                 # integration tests skip if Postgres is unreachable

uv run vrag ingest [AAPL ...] [-v] [--offline]   # EDGAR -> facts; needs VRAG_FINANCE_SEC_USER_AGENT
uv run vrag lookup AAPL revenue --year 2024 [--period Q1]
uv run vrag series AAPL "net income" --from 2020 --to 2024
uv run vrag compare "operating income" --entities AAPL MSFT --from 2022 --to 2024
uv run vrag calc "(a - b) / b * 100" a=f_12 b=f_9
uv run vrag source f_12
uv run vrag resolve-entity "apple" ; uv run vrag resolve-concept "sales"
uv run vrag ask "What was Apple's revenue in fiscal 2024?"   # agent + verifier; needs Ollama
uv run python -m evals.harness.run                  # M2 smoke eval; --paid for paid providers
```

- On this Windows machine `uv` may not be on PATH; use `python -m uv ...` with no venv active.
- `vrag ask` needs the Ollama server (`ollama serve`, or the Ollama app) and the model pulled: `ollama pull qwen2.5:3b`. Ollama answers on 127.0.0.1, not `localhost`.
- Agent code (`agent/`) wraps LangGraph; `verify/`, `tools/`, and `store/` must stay free of LangChain/LangGraph imports. Tests use a scripted fake model (`tests/test_agent_graph.py`), never a real one.
- If a command can't reach the database, Docker Desktop is probably not running (common after a reboot): start it, then `docker compose up -d`. Connections time out after 5 s.
- Bump `NORMALIZER_VERSION` (core) or the pack's `parser_version` when normalization output changes, so `vrag ingest --offline` rewrites every document.
- The user has ADHD: explain and report in small, goal-first chunks with a runnable check after each.
- Config comes from the environment or `.env` (copy `.env.example`): `DATABASE_URL`, `PACKS_DIR`, `RAW_DIR`, `QUERY_TIMEOUT_MS`, `QUERY_MAX_ROWS`. Packs read their own settings (finance: `VRAG_FINANCE_*`).
- Raw downloads are cached in `data/raw/<pack>/` (gitignored). `--offline` re-runs from the cache.
- CLI exit codes: 0 ok, 1 error, 2 refused (ambiguous entity/concept or no clean value).
- Tests use a synthetic company in `tests/fixtures/edgar/` (made-up numbers). Never put real figures from memory in fixtures or docs.
- Migrations are forward-only numbered SQL files in `src/verified_rag/store/migrations/`. Never edit an applied one; add a new file (ADR-8, `docs/adr/0008-plain-sql-migrations.md`).
- New ADRs go in `docs/adr/`, continuing from ADR-7 in the design doc.
