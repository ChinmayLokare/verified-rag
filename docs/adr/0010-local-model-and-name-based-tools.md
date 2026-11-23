# ADR-10: Free local model first, and lookup tools that take names

**Status:** Accepted (M2)

## Context

M2 needs an answer model. ADR-7 asks for the cheapest model that meets the correctness target, and the decision should come from evals, not assumptions. The development machine has a GTX 1650 (4 GB VRAM) and 8 GB RAM.

Two problems showed up on the first runs:

1. **Speed.** `qwen3:4b` did not fit in 4 GB (45% of layers on CPU at an 8,192-token context) and wrote long reasoning even with thinking disabled. One model call took 419 seconds.
2. **Wasted steps.** With `resolve_entity` and `resolve_concept` as separate tools, the 3B model kept guessing IDs, got errors, resolved, and guessed again: about 6 model calls per lookup, and many chances to fumble. Measured context use stayed under 1,900 tokens, so context size was not the cause.

## Decision

- **Default model: `qwen2.5:3b` through Ollama**, 4,096-token context, temperature 0. It fits fully on the GPU (2.2 GB) and answers in about 1 second per call. Model, context, and provider stay in config (`LLM_MODEL`, `LLM_NUM_CTX`, `LLM_PROVIDER`).
- **Lookup tools take names.** `lookup_fact`, `fact_time_series`, and `compare_entities` take `company` and `concept` as words and resolve them inside the tool with `resolve_entity` / `resolve_concept` plus `unambiguous()`. An ambiguous or unknown name comes back as a tool error listing the candidates; the tool never picks silently. The separate resolve tools are no longer offered to the model (the functions remain and are what the lookups use). This differs from the tool list in design.md section 8.3.
- **The prompt includes one worked example** of a correct `submit_answer` call.
- **Unchanged safety layers:** references must come from tool results in this request, every number is verified at its written precision, calculations are recomputed, and an answer that fails verification twice is refused.

## Results

The M2 smoke eval (`evals/harness`): 16 answerable questions generated from the facts table (12 lookups, 4 growth calculations) plus 4 that should be refused. Same seed, same questions in both runs. Summaries are in `evals/results/`.

| | Before (ID-based tools) | After (name-based tools, example) |
|---|---|---|
| Wrong-answer rate (wrong / answered) | 0% (0 of 3) | 0% (0 of 12) |
| Coverage (answered / answerable) | 19% (3 of 16) | 75% (12 of 16) |
| Lookups correct | 3 of 12 | 10 of 12 |
| Growth questions correct | 0 of 4 | 2 of 4 |
| Should-refuse questions refused | 4 of 4 | 4 of 4 |
| Run time | 9.6 min | 3.7 min |

## Consequences

- The free model is usable for single-figure questions and stays safe: its failures become refusals, not wrong answers.
- Twelve answered questions cannot establish a wrong-answer rate under 2%; that needs the M4 question set (FinanceBench plus a golden set, 150+ questions).
- Multi-step questions (two lookups plus a calculation) are the weak spot (2 of 4). If M4 shows this matters, compare `claude-haiku-5-5` on the same set: set `LLM_PROVIDER=anthropic`, install `langchain-anthropic` pinned, and run the eval with `--paid`. The model is a config change; nothing else moves.
- Fewer steps per question also lowers cost for a paid model later.
