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
- **No secrets in the repo** (keys, tokens, SEC contact details). Use environment config locally and Secrets Manager in AWS.
- **Don't commit dependencies or build artifacts.** Dependencies are declared in `pyproject.toml` and locked by `uv`.
- **No metric goes in the README unless it came from an eval run.**

## Conventions

- Python 3.12, `uv` for dependencies, Ruff for lint and format, Pyright for types, pytest for tests.
- Model IDs, versions, and parameters live in config, not in code. Check the provider's current docs before adding sampling parameters; newer Claude models reject some of them.
- Every change to retrieval, prompts, or models runs the eval suite (`evals/`).
- Infrastructure changes go through Terraform in `infra/`. Nothing is created by hand in the AWS console.
- Record significant decisions as a short ADR in `docs/adr/`.

## Current status

Milestone M0 (repository skeleton) has not started. Commands for running tests, evals, and the local stack go here once they exist.
