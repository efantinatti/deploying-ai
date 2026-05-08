"""
memory.py — Chat history helper for Cosmos.

Provides a simple trimming utility to prevent the conversation from exceeding
the LLM context window on long sessions. SystemMessage objects are always kept;
only the sliding window of human/assistant exchanges is trimmed.
"""
from langchain_core.messages import SystemMessage

# Maximum number of non-system messages to retain across turns.
# Each user+assistant exchange counts as 2 messages; 20 = 10 full turns.
MAX_HISTORY_MESSAGES = 20


def trim_history(messages: list, max_messages: int = MAX_HISTORY_MESSAGES) -> list:
    """
    Trim a list of LangChain message objects to prevent context overflow.

    System messages are always preserved.  All other messages (HumanMessage,
    AIMessage, ToolMessage, …) are windowed to the most recent `max_messages`
    entries.

    Args:
        messages:     List of LangChain message objects (may include SystemMessage).
        max_messages: Maximum number of non-system messages to keep.

    Returns:
        Trimmed list — system messages first, then the most recent exchanges.
    """
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    other_msgs  = [m for m in messages if not isinstance(m, SystemMessage)]

    if len(other_msgs) > max_messages:
        other_msgs = other_msgs[-max_messages:]

    return system_msgs + other_msgs
