"""Prompt templates for the agents app's chains, kept separate from the
chain-building logic in services.py so prompt text can be edited/reviewed
without touching wiring code.
"""

from langchain_core.prompts import ChatPromptTemplate

RUN_PROMPT = ChatPromptTemplate.from_messages([
    ('system', 'You are a helpful assistant embedded in a Django app.'),
    ('human', '{input}'),
])
