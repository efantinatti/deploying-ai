"""
math_service.py — Service 3: Math tool via LangChain StructuredTool.

Wraps the existing `math_tools.get_math_tool()` function from 05_src/.
The tool translates natural-language math problems into numexpr expressions
and evaluates them, returning a precise numeric result.

Usage (called by app.py):
    from services.math_service import build_math_tool
    math_tool = build_math_tool(llm)
"""
import os
import sys

# Add 05_src to sys.path so we can import math_tools from the parent package.
_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from math_tools import get_math_tool  # noqa: E402  (imported after path manipulation)


def build_math_tool(llm):
    """
    Return a LangChain StructuredTool that solves math problems.

    Args:
        llm: A configured LangChain chat model instance.  The math tool uses it
             internally to parse natural-language problems into numexpr expressions.

    Returns:
        A StructuredTool named "math" ready to be bound to the agent.
    """
    return get_math_tool(llm)
