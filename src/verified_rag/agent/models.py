"""Build the answer model from config. Model IDs and parameters never live in code."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from verified_rag.config import Settings


class ModelConfigError(RuntimeError):
    pass


def build_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "base_url": settings.ollama_base_url,
            "num_ctx": settings.llm_num_ctx,
            "num_predict": settings.llm_max_tokens,
        }
        if settings.llm_temperature is not None:
            kwargs["temperature"] = settings.llm_temperature
        if settings.llm_reasoning is not None:
            kwargs["reasoning"] = settings.llm_reasoning
        return ChatOllama(**kwargs)

    # Anthropic: install with `uv add langchain-anthropic` (pinned) when a paid run is approved.
    try:
        from langchain_anthropic import ChatAnthropic  # pyright: ignore[reportMissingImports, reportUnknownVariableType]
    except ImportError as exc:
        raise ModelConfigError(
            "llm_provider=anthropic needs the langchain-anthropic package and ANTHROPIC_API_KEY"
        ) from exc
    kwargs = {"model": settings.llm_model, "max_tokens": settings.llm_max_tokens}
    if settings.llm_temperature is not None:
        kwargs["temperature"] = settings.llm_temperature
    return ChatAnthropic(**kwargs)  # pyright: ignore[reportUnknownVariableType]
