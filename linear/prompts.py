"""Prompt templates for Lane 1's Linear-triggered chains, kept separate from
the orchestration logic in services.py so prompt text can be edited/reviewed
without touching wiring code.
"""

from langchain_core.prompts import ChatPromptTemplate

REFINE_PROMPT = ChatPromptTemplate.from_messages([
    (
        'system',
        'You turn a raw engineering ticket into a concrete, actionable spec: '
        'what needs to change and why. Call out any ambiguity explicitly '
        'instead of silently guessing at it.',
    ),
    ('human', 'Ticket {identifier}: {title}\n\n{description}'),
])
