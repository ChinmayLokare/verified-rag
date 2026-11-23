"""Run the M2 smoke eval against the configured answer model.

    python -m uv run python -m evals.harness.run [--lookup 12 --growth 4 --refuse 4] [--seed 7]

Uses whatever model config says (llm_provider, llm_model). With a paid provider this makes paid
calls, so it refuses to run unless --paid is passed (CLAUDE.md: no paid calls unless the run is
explicitly a paid eval). Writes per-question JSONL and a summary JSON to evals/results/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from evals.harness.grade import Grade, grade, summarize
from evals.harness.questions import build
from verified_rag.agent.ask import ask
from verified_rag.config import get_settings
from verified_rag.store.db import QueryLimits, connect

RESULTS = Path(__file__).resolve().parent.parent / "results"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evals.harness.run")
    p.add_argument("--pack", default="finance")
    p.add_argument("--lookup", type=int, default=12)
    p.add_argument("--growth", type=int, default=4)
    p.add_argument("--refuse", type=int, default=4)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--paid", action="store_true", help="allow a paid model provider")
    args = p.parse_args(argv)

    settings = get_settings()
    if settings.llm_provider != "ollama" and not args.paid:
        print("refusing to run a paid provider without --paid", file=sys.stderr)
        return 1

    limits = QueryLimits(settings.query_timeout_ms, settings.query_max_rows)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{settings.llm_model.replace(':', '_').replace('/', '_')}"
    RESULTS.mkdir(exist_ok=True)
    detail_path = RESULTS / f"{name}.jsonl"

    with connect(settings.database_url) as conn:
        questions = build(
            conn, args.pack, limits, n_lookup=args.lookup, n_growth=args.growth,
            n_refuse=args.refuse, seed=args.seed,
        )  # fmt: skip
        print(f"{len(questions)} questions, model {settings.llm_model}", flush=True)
        grades: list[tuple[str, Grade]] = []
        started = time.monotonic()
        with detail_path.open("w", encoding="utf-8") as out:
            for i, q in enumerate(questions, 1):
                outcome = ask(q.text, settings, args.pack, conn=conn)
                g = grade(q.kind, outcome.status, outcome.answer_text, q.expected, q.unit)
                grades.append((q.kind, g))
                print(
                    f"[{i:>2}/{len(questions)}] {g:<16} {outcome.elapsed_s:5.0f}s  {q.text}",
                    flush=True,
                )
                record = {
                    "id": q.id,
                    "kind": q.kind,
                    "question": q.text,
                    "expected": str(q.expected) if q.expected is not None else None,
                    "unit": q.unit,
                    "grade": g,
                    "outcome": outcome.model_dump(mode="json"),
                }
                out.write(json.dumps(record) + "\n")

    s = summarize(grades)
    summary = {
        "model": settings.llm_model,
        "provider": settings.llm_provider,
        "reasoning": settings.llm_reasoning,
        "seed": args.seed,
        "questions": len(questions),
        "answerable": s.answerable,
        "answered": s.answered,
        "correct": s.correct,
        "wrong": s.wrong,
        "coverage": round(s.coverage, 4),
        "wrong_answer_rate": round(s.wrong_answer_rate, 4),
        "accuracy_on_all_answerable": round(s.accuracy_on_all, 4),
        "correct_refusals": f"{s.correct_refusals}/{s.refuse_questions}",
        "minutes": round((time.monotonic() - started) / 60, 1),
    }
    (RESULTS / f"{name}.summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
