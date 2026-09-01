"""LangChain wiring for the agents app.

LangSmith observability needs no code here beyond what's below — it's
enabled by setting LANGCHAIN_TRACING_V2=true (plus LANGCHAIN_API_KEY and
LANGCHAIN_PROJECT) in the environment. Every chain/LLM call is then traced
automatically. `collect_runs()` is used only so we can persist the
LangSmith run id alongside the local AgentRun record.
"""

from dataclasses import dataclass

from django.conf import settings
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tracers.context import collect_runs
from langchain_openai import ChatOpenAI


@dataclass
class ChainResult:
    """Return value of run_prompt(): the chain's output plus its LangSmith run id (for cross-referencing a trace from an AgentRun row)."""

    text: str
    langsmith_run_id: str | None


def build_chain():
    """Construct the prompt | llm | parser chain used by run_prompt().

    Split out from run_prompt() so new capabilities can build their own
    chain/graph and still reuse the collect_runs()/AgentRun persistence
    pattern below.
    """
    llm = ChatOpenAI(
        model=settings.DEFAULT_LLM_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )
    prompt = ChatPromptTemplate.from_messages([
        ('system', 'You are a helpful assistant embedded in a Django app.'),
        ('human', '{input}'),
    ])
    return prompt | llm | StrOutputParser()


def run_prompt(text: str) -> ChainResult:
    """Run `text` through build_chain(), returning the response and its LangSmith run id.

    collect_runs() only captures the run id for local persistence — tracing
    itself already happened via the LANGCHAIN_* env vars regardless of this
    context manager.
    """
    chain = build_chain()
    with collect_runs() as cb:
        output = chain.invoke(
            {'input': text},
            config={'run_name': 'agents.run_prompt', 'tags': ['django', 'agents-app']},
        )
    run_id = str(cb.traced_runs[0].id) if cb.traced_runs else None
    return ChainResult(text=output, langsmith_run_id=run_id)
