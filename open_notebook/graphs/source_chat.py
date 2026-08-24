from typing import Annotated, Dict, List, Optional

from ai_prompter import Prompter
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.ai.runtime import chat_langchain as provision_langchain_model
from open_notebook.context import for_source_chat as build_source_context
from open_notebook.context.assembly import format_source_context
from open_notebook.conversations.runtime import (
    get_sqlite_checkpointer,
    run_coroutine_sync,
)
from open_notebook.domain.notebook import Source, SourceInsight
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class SourceChatState(TypedDict):
    messages: Annotated[list, add_messages]
    source_id: str
    source: Optional[Source]
    insights: Optional[List[SourceInsight]]
    context: Optional[str]
    model_override: Optional[str]
    context_indicators: Optional[Dict[str, List[str]]]


def _source_content_is_available(
    source_info: Dict,
    context_data: Dict,
) -> bool:
    """Return whether source text, including a truncated prefix, is available."""
    status = context_data.get("metadata", {}).get("source_text_status")
    if status is not None:
        return status in {"available", "truncated"}
    full_text = source_info.get("full_text")
    return isinstance(full_text, str) and bool(full_text.strip())


def call_model_with_source_context(
    state: SourceChatState, config: RunnableConfig
) -> dict:
    """Build source context and call the model (sync graph node)."""
    try:
        return _call_model_with_source_context_inner(state, config)
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


def _call_model_with_source_context_inner(
    state: SourceChatState, config: RunnableConfig
) -> dict:
    source_id = state.get("source_id")
    if not source_id:
        raise ValueError("source_id is required in state")

    context_data = run_coroutine_sync(
        lambda: build_source_context(
            source_id=source_id,
            max_tokens=50000,
        )
    )

    source = None
    insights = []
    context_indicators: dict[str, list[str | None]] = {
        "sources": [],
        "insights": [],
        "notes": [],
    }

    if context_data.get("sources"):
        source_info = context_data["sources"][0]
        source = Source(**source_info) if isinstance(source_info, dict) else source_info
        if (
            isinstance(source_info, dict)
            and _source_content_is_available(source_info, context_data)
            and source.id
        ):
            context_indicators["sources"].append(source.id)

    if context_data.get("insights"):
        for insight_data in context_data["insights"]:
            insight = (
                SourceInsight(**insight_data)
                if isinstance(insight_data, dict)
                else insight_data
            )
            insights.append(insight)
            context_indicators["insights"].append(insight.id)

    formatted_context = _format_source_context(context_data)

    prompt_data = {
        "source": source.model_dump() if source else None,
        "insights": [insight.model_dump() for insight in insights] if insights else [],
        "context": formatted_context,
        "context_indicators": context_indicators,
    }

    system_prompt = Prompter(prompt_template="source_chat/system").render(
        data=prompt_data
    )
    payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])

    model_id = config.get("configurable", {}).get("model_id") or state.get(
        "model_override"
    )
    model = run_coroutine_sync(
        lambda: provision_langchain_model(
            str(payload),
            model_id,
            "chat",
            max_tokens=8192,
        )
    )

    ai_message = model.invoke(payload)

    content = extract_text_content(ai_message.content)
    cleaned_content = clean_thinking_content(content)
    cleaned_message = ai_message.model_copy(update={"content": cleaned_content})

    return {
        "messages": cleaned_message,
        "source": source,
        "insights": insights,
        "context": formatted_context,
        "context_indicators": context_indicators,
    }


def _format_source_context(context_data: Dict) -> str:
    """Format context through the builder's shared budgeted renderer."""
    return format_source_context(context_data)


memory = get_sqlite_checkpointer()

source_chat_state = StateGraph(SourceChatState)
source_chat_state.add_node("source_chat_agent", call_model_with_source_context)
source_chat_state.add_edge(START, "source_chat_agent")
source_chat_state.add_edge("source_chat_agent", END)
source_chat_graph = source_chat_state.compile(checkpointer=memory)
