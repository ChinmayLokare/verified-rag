# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# LangGraph's generics are loose under strict mode; the correctness modules stay fully strict.
"""Answer one question end to end. The single entry point for the CLI, evals, and later the API."""

import time
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from langchain_core.language_models import BaseChatModel

from verified_rag.agent.graph import AgentDeps, Outcome, build_graph
from verified_rag.agent.models import ModelConfigError, build_chat_model
from verified_rag.agent.prompts import system_prompt
from verified_rag.agent.tools import AgentTools
from verified_rag.config import Settings
from verified_rag.packs.loader import load_pack
from verified_rag.store.db import QueryLimits, connect
from verified_rag.verify.evidence import load_evidence
from verified_rag.verify.verifier import Evidence


def ask(
    question: str,
    settings: Settings,
    pack_id: str = "finance",
    model: BaseChatModel | None = None,
    conn: psycopg.Connection | None = None,
    progress: Callable[[str], None] | None = None,
) -> Outcome:
    """Answer ``question`` with a verified answer or a refusal. Never raises for model failures."""
    started = time.monotonic()
    pack = load_pack(pack_id, settings.packs_dir)
    limits = QueryLimits(settings.query_timeout_ms, settings.query_max_rows)
    try:
        chat = model or build_chat_model(settings)
    except ModelConfigError as exc:
        return Outcome(status="refused", refusal_reason=str(exc))

    own_conn = conn is None
    db = conn or connect(settings.database_url)
    try:

        def evidence(refs: list[str]) -> Mapping[str, Evidence]:
            return load_evidence(db, refs, limits)

        deps = AgentDeps(
            model=chat,
            tools=AgentTools(db, pack.spec.id, limits, request_id=None),
            system_prompt=system_prompt(pack.spec.display_name, pack.prompt),
            load_evidence=evidence,
            max_model_calls=settings.agent_max_model_calls,
            timeout_s=settings.agent_timeout_s,
            max_cost_usd=settings.agent_max_cost_usd,
            max_retries=settings.agent_max_retries,
            input_usd_per_mtok=settings.llm_input_usd_per_mtok,
            output_usd_per_mtok=settings.llm_output_usd_per_mtok,
            progress=progress,
        )
        graph = build_graph(deps)
        config: Any = {
            "recursion_limit": settings.agent_max_model_calls * 4 + 10,
            "run_name": "vrag-ask",
            "metadata": {"question_id": str(uuid4()), "model": settings.llm_model},
        }
        try:
            final = graph.invoke({"question": question}, config=config)
        except (httpx.HTTPError, ConnectionError, OSError) as exc:
            return Outcome(
                status="refused",
                refusal_reason=f"the answer model is unavailable ({exc.__class__.__name__})",
                elapsed_s=time.monotonic() - started,
            )
        outcome = final.get("outcome")
        if not isinstance(outcome, Outcome):
            return Outcome(status="refused", refusal_reason="no verified answer")
        return outcome
    finally:
        if own_conn:
            db.close()
