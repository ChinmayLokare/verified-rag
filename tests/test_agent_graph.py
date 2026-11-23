"""The agent graph with a scripted fake model: no network, no paid calls, deterministic."""

# pyright: reportIncompatibleMethodOverride=false, reportUnknownMemberType=false
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from verified_rag.agent.ask import ask
from verified_rag.config import Settings
from verified_rag.ingest.pipeline import ingest
from verified_rag.packs.interface import EntityConfig, Pack, PackContext
from verified_rag.store.db import connect

pytestmark = pytest.mark.integration

Step = Callable[[list[BaseMessage]], AIMessage]


class ScriptedModel(BaseChatModel):
    """Replays a fixed list of steps; each step sees the conversation so far."""

    steps: list[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages: list[BaseMessage], stop: Any = None, **kwargs: Any) -> ChatResult:
        step: Step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=step(messages))])


def call(name: str, /, **args: Any) -> Step:
    return lambda _m: AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c_{name}"}])


def text(content: str) -> Step:
    return lambda _m: AIMessage(content)


def last_ref(messages: list[BaseMessage]) -> str:
    """The first f_/calc_ ref in the latest tool result, as a real model would read it."""
    import re

    for m in reversed(messages):
        if isinstance(m, ToolMessage):
            found = re.search(r'"ref":"((?:f|calc)_\d+)"', str(m.content))
            if found:
                return found.group(1)
    raise AssertionError("no ref in tool results")


def submit(answer: str, claim: str, ref: str | None = None) -> Step:
    def step(messages: list[BaseMessage]) -> AIMessage:
        evidence = [ref or last_ref(messages)]
        args = {"answer_text": answer, "claims": [{"text": claim, "evidence": evidence}]}
        return AIMessage("", tool_calls=[{"name": "submit_answer", "args": args, "id": "c_submit"}])

    return step


@pytest.fixture
def db(migrated_db_url: str, fixture_pack: tuple[Pack, PackContext]) -> tuple[Settings, Any]:
    pack, ctx = fixture_pack
    conn = connect(migrated_db_url)
    assert ingest(conn, pack, ctx, [EntityConfig(ref="EXMP", aliases=("Example",))]).ok
    entity = conn.execute("SELECT entity_id FROM entities").fetchone()
    revenue = conn.execute("SELECT concept_id FROM concepts WHERE name = 'revenue'").fetchone()
    assert entity is not None
    assert revenue is not None
    settings = Settings(database_url=migrated_db_url, agent_max_model_calls=8)
    return settings, (conn, int(entity[0]), int(revenue[0]))


def _lookup_steps(entity: int, concept: int) -> list[Step]:
    del entity, concept  # lookups take names now (ADR-10)
    return [call("lookup_fact", company="Example Corp", concept="revenue", fiscal_year=2024)]


def test_verified_answer(db: tuple[Settings, Any]) -> None:
    settings, (conn, entity, concept) = db
    model = ScriptedModel(
        steps=[
            *_lookup_steps(entity, concept),
            submit(
                "Example Corp's revenue in fiscal 2024 was 1,200 USD.",
                "Revenue was 1,200 USD in fiscal 2024",
            ),
        ]
    )
    events: list[str] = []
    out = ask(
        "What was Example Corp's revenue in fiscal 2024?",
        settings,
        model=model,
        conn=conn,
        progress=events.append,
    )
    assert out.status == "answered", out
    assert out.claims[0].sources == ["Example Corp revenue FY2024 FY"]
    assert any("verify: passed" in e for e in events)


def test_wrong_number_gets_one_retry_then_refusal(db: tuple[Settings, Any]) -> None:
    settings, (conn, entity, concept) = db
    wrong = submit(
        "Example Corp's revenue in fiscal 2024 was 1,300 USD.",
        "Revenue was 1,300 USD in fiscal 2024",
    )
    model = ScriptedModel(steps=[*_lookup_steps(entity, concept), wrong, wrong])
    out = ask("What was Example Corp's revenue in fiscal 2024?", settings, model=model, conn=conn)
    assert out.status == "refused"
    assert any("1,300" in p for p in out.verification_problems)
    assert any("revenue FY2024" in e for e in out.evidence_found)  # shows what it did find
    assert model.calls == 1 + 1 + settings.agent_max_retries  # lookup, submit, retries


def test_retry_can_fix_the_answer(db: tuple[Settings, Any]) -> None:
    settings, (conn, entity, concept) = db
    model = ScriptedModel(
        steps=[
            *_lookup_steps(entity, concept),
            submit("Example Corp's revenue was 1,300 USD.", "Revenue was 1,300 USD"),
            submit(
                "Example Corp's revenue in fiscal 2024 was 1,200 USD.",
                "Revenue was 1,200 USD in fiscal 2024",
                ref=None,
            ),
        ]
    )
    out = ask("What was Example Corp's revenue in fiscal 2024?", settings, model=model, conn=conn)
    assert out.status == "answered", out


def test_invented_reference_is_refused(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    fact = conn.execute("SELECT fact_id FROM facts LIMIT 1").fetchone()
    assert fact is not None
    # The fact exists, but no tool returned it in this request.
    invented = submit(
        "Example Corp's revenue was 1,200 USD.", "Revenue was 1,200 USD", ref=f"f_{fact[0]}"
    )
    model = ScriptedModel(steps=[invented])
    out = ask("What was Example Corp's revenue in fiscal 2024?", settings, model=model, conn=conn)
    assert out.status == "refused"
    assert any("not returned by any tool" in p for p in out.verification_problems)


def test_guard_refuses_advice_without_calling_the_model(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    model = ScriptedModel(steps=[text("should never be called")])
    out = ask("Should I buy Example Corp stock?", settings, model=model, conn=conn)
    assert out.status == "refused"
    assert "advice" in (out.refusal_reason or "")
    assert model.calls == 0


def test_model_can_decline(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    model = ScriptedModel(steps=[text("CANNOT ANSWER: no data for 1985")])
    out = ask("What was Example Corp's revenue in 1985?", settings, model=model, conn=conn)
    assert out.status == "refused"
    assert out.refusal_reason == "no data for 1985"


def test_step_budget_stops_a_looping_model(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    loop = call("lookup_fact", company="Example Corp", concept="revenue", fiscal_year=2024)
    model = ScriptedModel(steps=[loop])  # never submits
    out = ask("What was Example Corp's revenue?", settings, model=model, conn=conn)
    assert out.status == "refused"
    assert "model calls" in (out.refusal_reason or "")
    assert model.calls == settings.agent_max_model_calls


def test_bad_tool_arguments_come_back_as_errors(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    model = ScriptedModel(
        steps=[
            call("lookup_fact", company="Example Corp", concept="revenue", fiscal_year=1800),
            text("CANNOT ANSWER: bad year"),
        ]
    )
    events: list[str] = []
    out = ask("x revenue?", settings, model=model, conn=conn, progress=events.append)
    assert out.status == "refused"
    assert any("-> error" in e for e in events)


def test_unknown_names_come_back_as_errors(db: tuple[Settings, Any]) -> None:
    settings, (conn, _, _) = db
    model = ScriptedModel(
        steps=[
            call("lookup_fact", company="Nonexistent Widgets", concept="revenue", fiscal_year=2024),
            call(
                "lookup_fact",
                company="Example Corp",
                concept="number of employees",
                fiscal_year=2024,
            ),
            text("CANNOT ANSWER: gave up"),
        ]
    )
    events: list[str] = []
    ask("x?", settings, model=model, conn=conn, progress=events.append)
    assert any("no company matches" in e for e in events)
    assert any("no figure matches" in e for e in events)
