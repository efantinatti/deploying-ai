"""
app.py — Cosmos: a conversational AI science companion.

Architecture:
  - Gradio ChatInterface  → receives user message + Gradio-managed history
  - Guardrail filter      → blocks banned topics and prompt-injection attempts
  - LangGraph agent       → MessagesState + call_model node + ToolNode
  - Three tools           → NASA APOD, science facts search, math solver

Run from 05_src/assignment_chat/:
    python app.py

Or from 05_src/:
    python assignment_chat/app.py
"""
import base64
import os
import re
import sys

# Ensure the assignment_chat package root is on sys.path regardless of how
# this script is invoked (python app.py vs python assignment_chat/app.py).
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

# ── Load secrets before any service imports ───────────────────────────────────
from dotenv import load_dotenv

_secrets = os.path.join(_PKG_DIR, '.secrets')
if not os.path.exists(_secrets):
    _secrets = os.path.join(_PKG_DIR, '..', '.secrets')
load_dotenv(_secrets)

# ── Imports ───────────────────────────────────────────────────────────────────
import gradio as gr
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import START, StateGraph, MessagesState
from langgraph.prebuilt.tool_node import ToolNode, tools_condition

from prompts import SYSTEM_PROMPT
from memory import trim_history
from services.api_service import get_astronomy_picture
from services.semantic_service import search_science_facts, ingest_documents
from services.math_service import build_math_tool


# ── LLM factory ───────────────────────────────────────────────────────────────
def _build_llm(temperature: float = 0.7):
    """
    Create a configured LangChain chat model.

    Uses the course API gateway when API_GATEWAY_KEY is present in the
    environment; otherwise falls back to standard OpenAI credentials.
    """
    kwargs: dict = {"temperature": temperature}
    gateway_key = os.getenv("API_GATEWAY_KEY")
    base_url    = os.getenv(
        "OPENAI_BASE_URL",
        "https://k7uffyg03f.execute-api.us-east-1.amazonaws.com/prod/openai/v1",
    )
    if gateway_key:
        kwargs.update({
            "base_url": base_url,
            "api_key":  "any value",
            "default_headers": {"x-api-key": gateway_key},
        })
    return init_chat_model("openai:gpt-4o-mini", **kwargs)


# ── Initialise agent ──────────────────────────────────────────────────────────
print("Initialising Cosmos…")

_llm       = _build_llm()
_math_tool = build_math_tool(_llm)
_tools     = [get_astronomy_picture, search_science_facts, _math_tool]
_model_with_tools = _llm.bind_tools(_tools)


def _call_model(state: MessagesState) -> dict:
    """LLM node: decide whether to answer directly or invoke a tool."""
    response = _model_with_tools.invoke(
        [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    )
    return {"messages": [response]}


def _build_graph():
    builder = StateGraph(MessagesState)
    builder.add_node(_call_model)
    builder.add_node(ToolNode(_tools))
    builder.add_edge(START, "_call_model")
    builder.add_conditional_edges("_call_model", tools_condition)
    builder.add_edge("tools", "_call_model")
    return builder.compile()


_graph = _build_graph()


# ── Guardrails ────────────────────────────────────────────────────────────────
# Topics the model must never discuss (per assignment requirements).
_BANNED_SINGLE_WORDS: frozenset[str] = frozenset({
    "cat", "cats", "kitten", "kittens", "feline", "felines",
    "dog", "dogs", "puppy", "puppies", "canine", "canines",
    "horoscope", "horoscopes", "zodiac", "astrology", "astrological",
    "aries", "taurus", "scorpio", "sagittarius", "capricorn",
    "aquarius", "pisces",
    # The following signs double as common English words; included per spec.
    "virgo", "libra",
})

# Zodiac signs kept as phrase-match to avoid false positives on 'leo' (a name)
# and 'cancer' (a disease).
_BANNED_PHRASES: tuple[str, ...] = (
    "taylor swift",
    "leo zodiac",
    "cancer zodiac",
    "cancer sign",
    "leo sign",
    "gemini sign",
    "gemini zodiac",
    "my zodiac",
    "my star sign",
    "my sun sign",
    "birth chart",
    "rising sign",
)

# Phrases that indicate the user is probing or trying to override the system prompt.
_PROMPT_PROBES: tuple[str, ...] = (
    "system prompt",
    "system message",
    "your instructions",
    "your prompt",
    "initial prompt",
    "reveal your prompt",
    "show your prompt",
    "what are you told",
    "what is your prompt",
    "ignore previous instructions",
    "ignore all previous",
    "forget your instructions",
    "disregard your instructions",
    "pretend you have no restrictions",
    "act as if you have no rules",
    "jailbreak",
    "dan mode",
)

_REFUSAL_TOPIC = (
    "That topic is outside what I can discuss here.  "
    "I'm happy to help with science, astronomy, mathematics, or any other "
    "curiosity about the natural world — what would you like to explore?"
)
_REFUSAL_PROMPT = (
    "I'm not able to share information about my configuration.  "
    "Is there a science or maths question I can help you with?"
)


def _check_guardrails(message: str) -> str | None:
    """
    Return a refusal string if the message triggers a guardrail, else None.

    Checks (in order):
      1. Prompt-injection / system-prompt probing.
      2. Banned topic keywords and phrases.
    """
    msg_lower = message.lower()

    # 1 — Prompt probe detection
    for probe in _PROMPT_PROBES:
        if probe in msg_lower:
            return _REFUSAL_PROMPT

    # 2 — Multi-word banned phrases
    for phrase in _BANNED_PHRASES:
        if phrase in msg_lower:
            return _REFUSAL_TOPIC

    # 3 — Single-word banned terms (word-boundary match)
    words = set(re.findall(r"\b[a-z]+\b", msg_lower))
    if words & _BANNED_SINGLE_WORDS:
        return _REFUSAL_TOPIC

    return None


# ── Chat handler ──────────────────────────────────────────────────────────────
def cosmos_chat(message: str, history: list[dict]) -> str:
    """
    Main Gradio chat handler.

    Args:
        message: The user's latest input string.
        history: Gradio-managed list of {"role": ..., "content": ...} dicts.

    Returns:
        Cosmos's response as a plain string (may contain Markdown).
    """
    # ── Guardrails ────────────────────────────────────────────────────────────
    refusal = _check_guardrails(message)
    if refusal:
        return refusal

    # ── Convert Gradio history → LangChain messages ───────────────────────────
    lc_messages = []
    for turn in history:
        role    = turn.get("role", "")
        content = turn.get("content", "")
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))

    lc_messages.append(HumanMessage(content=message))

    # ── Memory trimming ───────────────────────────────────────────────────────
    lc_messages = trim_history(lc_messages)

    # ── Invoke LangGraph agent ────────────────────────────────────────────────
    result = _graph.invoke({"messages": lc_messages})

    # The final message in the state is the assistant's reply
    return result["messages"][-1].content


# ── Gradio UI ─────────────────────────────────────────────────────────────────

# Encode local logo as base64 so Gradio can embed it directly in the HTML
# without needing a separate file-serving route.
_LOGO_PATH = os.path.join(_PKG_DIR, '..', 'logo', 'Logo.jpg')
try:
    with open(_LOGO_PATH, 'rb') as _f:
        _LOGO_B64 = base64.b64encode(_f.read()).decode('utf-8')
    _LOGO_SRC = f"data:image/jpeg;base64,{_LOGO_B64}"
except FileNotFoundError:
    _LOGO_SRC = ""   # header will still render without the logo

# UofT coat of arms from Wikimedia Commons (stable public resource)
_UOFT_LOGO_SRC = (
    "https://upload.wikimedia.org/wikipedia/en/0/04/Utoronto_coa.svg"
)

_HEADER_HTML = f"""
<div style="
    background: linear-gradient(135deg, #05101f 0%, #0d1f3c 60%, #112347 100%);
    padding: 22px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-radius: 12px;
    border-bottom: 3px solid #003fa3;
    box-shadow: 0 4px 18px rgba(0,0,0,0.45);
    margin-bottom: 4px;
">
  <!-- Left: personal logo -->
  {"<img src='" + _LOGO_SRC + "' alt='Fantinatti logo' style='height:76px;border-radius:8px;object-fit:contain;' />" if _LOGO_SRC else "<div></div>"}

  <!-- Centre: title block -->
  <div style="text-align:center; color:#f0f4ff; flex:1; padding:0 24px;">
    <div style="font-size:0.72rem; letter-spacing:0.18em; text-transform:uppercase;
                color:#6fa3e0; font-weight:600; margin-bottom:4px;">
      University of Toronto &nbsp;·&nbsp; Data Sciences Institute
    </div>
    <div style="font-size:2.0rem; font-weight:800; letter-spacing:0.12em;
                color:#ffffff; line-height:1.1;">
      🔭 &nbsp;COSMOS
    </div>
    <div style="font-size:0.88rem; color:#a8c4e8; margin-top:4px; font-weight:400;">
      AI Science Companion
    </div>
    <div style="
        display:inline-block;
        margin-top:10px;
        font-size:0.70rem;
        color:#7a9ec6;
        background:rgba(255,255,255,0.06);
        border:1px solid rgba(255,255,255,0.12);
        border-radius:20px;
        padding:3px 14px;
        letter-spacing:0.04em;
    ">
      Deploying AI 3 &nbsp;·&nbsp; Assignment 2
    </div>
  </div>

  <!-- Right: UofT crest -->
  <img src="{_UOFT_LOGO_SRC}" alt="University of Toronto"
       style="height:76px; object-fit:contain;"
       onerror="this.style.display='none'" />
</div>
"""

_CUSTOM_CSS = """
/* Remove the default Gradio panel title so our header is the only heading */
.chatbot-container .label-wrap { display: none !important; }
footer { display: none !important; }
"""

with gr.Blocks(theme=gr.themes.Soft(), css=_CUSTOM_CSS) as _chat_ui:
    gr.HTML(_HEADER_HTML)
    gr.ChatInterface(
        fn=cosmos_chat,
        type="messages",
        examples=[
            "What is NASA's astronomy picture of the day?",
            "Is it true that we only use 10% of our brain?",
            "How many seconds are in a year?",
            "What does science say about the Great Wall of China being visible from space?",
            "Show me the APOD from 2024-07-04",
            "If light travels at 299,792 km/s, how far does it travel in one hour?",
        ],
    )

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Ingesting science facts database (first run may take a moment)…")
    try:
        ingest_documents()
        print("Database ready.")
    except Exception as exc:
        print(f"Warning: database ingestion encountered an error: {exc}")
        print("Science facts search will attempt lazy ingestion on first query.")

    print("Starting Cosmos…")
    _chat_ui.launch()
