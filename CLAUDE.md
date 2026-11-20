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

M0 (skeleton) is done. Next is M1 (facts path).

```sh
docker compose up -d          # Postgres 17 + pgvector on localhost:5433 (5432 is often taken by a native install)
uv sync                       # install dependencies (uv.lock is committed; CI uses --locked)
uv run vrag migrate           # apply migrations; `--status` to list them
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest                 # integration tests skip if Postgres is unreachable
```

- Config comes from the environment or `.env` (copy `.env.example`). `DATABASE_URL` is the only setting so far.
- Migrations are forward-only numbered SQL files in `src/verified_rag/store/migrations/`. Never edit an applied one; add a new file (ADR-8, `docs/adr/0008-plain-sql-migrations.md`).
- New ADRs go in `docs/adr/`, continuing from ADR-7 in the design doc.
