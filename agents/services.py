"""LangChain wiring for the agents app.

LangSmith observability needs no code here beyond what's below — it's
enabled by setting LANGCHAIN_TRACING_V2=true (plus LANGCHAIN_API_KEY and
LANGCHAIN_PROJECT) in the environment. Every chain/LLM call is then traced
automatically. `collect_runs()` is used only so we can persist the
LangSmith run id alongside the local AgentRun record.
"""

from dataclasses import dataclass

from django.conf import settings
from langchain.chat_models import init_chat_model
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tracers.context import collect_runs


@dataclass
class ChainResult:
    """Return value of run_prompt(): the chain's output plus its LangSmith run id (for cross-referencing a trace from an AgentRun row)."""

    text: str
    langsmith_run_id: str | None


def build_chat_model(provider: str | None = None, model: str | None = None):
    """Construct a chat model for the given provider/model (falling back to
    DEFAULT_LLM_PROVIDER/DEFAULT_LLM_MODEL). Shared by every LangChain-based
    capability in this repo so provider/model selection works the same way
    everywhere — see the "Run prompt" skill and linear/services.py.

    Provider API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) are read
    directly from the environment by each provider's own LangChain
    integration; nothing provider-specific needs to be threaded through here.
    """
    return init_chat_model(
        model=model or settings.DEFAULT_LLM_MODEL,
        model_provider=provider or settings.DEFAULT_LLM_PROVIDER,
        temperature=0,
    )


def build_chain(provider: str | None = None, model: str | None = None):
    """Construct the prompt | llm | parser chain used by run_prompt().

    Split out from run_prompt() so new capabilities can build their own
    chain/graph and still reuse the collect_runs()/AgentRun persistence
    pattern below.
    """
    llm = build_chat_model(provider, model)
    prompt = ChatPromptTemplate.from_messages([
        ('system', 'You are a helpful assistant embedded in a Django app.'),
        ('human', '{input}'),
    ])
    return prompt | llm | StrOutputParser()


def run_prompt(text: str, provider: str | None = None, model: str | None = None) -> ChainResult:
    """Run `text` through build_chain(), returning the response and its LangSmith run id.

    collect_runs() only captures the run id for local persistence — tracing
    itself already happened via the LANGCHAIN_* env vars regardless of this
    context manager.
    """
    chain = build_chain(provider, model)
    with collect_runs() as cb:
        output = chain.invoke(
            {'input': text},
            config={'run_name': 'agents.run_prompt', 'tags': ['django', 'agents-app']},
        )
    run_id = str(cb.traced_runs[0].id) if cb.traced_runs else None
    return ChainResult(text=output, langsmith_run_id=run_id)
