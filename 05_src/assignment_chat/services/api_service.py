"""
api_service.py — Service 1: NASA Astronomy Picture of the Day (APOD).

Fetches the APOD from NASA's public API and returns a natural-language summary
instead of the raw JSON payload.  The tool uses a free-tier NASA key
(DEMO_KEY) by default; set NASA_API_KEY in .secrets for higher rate limits.

Reference: https://api.nasa.gov/
"""
import os
import requests
from langchain.tools import tool
from dotenv import load_dotenv

# ── Secrets ─────────────────────────────────────────────────────────────────
_secrets = os.path.join(os.path.dirname(__file__), '..', '.secrets')
if not os.path.exists(_secrets):
    _secrets = os.path.join(os.path.dirname(__file__), '..', '..', '.secrets')
load_dotenv(_secrets)

# NASA APOD endpoint
_APOD_URL = "https://api.nasa.gov/planetary/apod"

# Use NASA_API_KEY from .secrets if present; otherwise fall back to the
# public demo key (30 req/hour, 50 req/day).
_NASA_KEY = os.getenv("NASA_API_KEY", "DEMO_KEY")


# ── Tool ─────────────────────────────────────────────────────────────────────
@tool
def get_astronomy_picture(date: str = "") -> str:
    """
    Retrieve NASA's Astronomy Picture of the Day (APOD) and return a
    natural-language description of the featured cosmic image or video.

    Args:
        date: Optional date in YYYY-MM-DD format.  Leave blank for today's picture.

    Returns:
        A descriptive summary of the astronomy picture, including its title,
        date, and a condensed explanation.
    """
    params: dict = {"api_key": _NASA_KEY}
    if date:
        params["date"] = date.strip()

    try:
        resp = requests.get(_APOD_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return (
            f"I was unable to retrieve the astronomy picture (HTTP {status}). "
            "This may be a temporary issue with NASA's API or the request rate limit. "
            "Please try again shortly."
        )
    except requests.RequestException as exc:
        return (
            f"I could not reach NASA's APOD API right now. ({exc}) "
            "Please check your internet connection and try again."
        )

    title       = data.get("title", "Untitled")
    explanation = data.get("explanation", "No description available.")
    date_str    = data.get("date", "today")
    media_type  = data.get("media_type", "image")
    url         = data.get("url", "")
    copyright_  = data.get("copyright", "").strip()

    # Condense the explanation to avoid overwhelming the context window
    max_len = 700
    if len(explanation) > max_len:
        explanation = explanation[:max_len].rsplit(" ", 1)[0] + "…"

    media_label = "image" if media_type == "image" else "video"
    credit = f"\n\n*Credit: {copyright_}*" if copyright_ else ""

    lines = [
        f"**{title}** — {date_str}",
        "",
        f"Today's astronomy {media_label} invites us to contemplate the cosmos:",
        "",
        explanation,
        credit,
    ]

    if url:
        link_label = "View the image" if media_type == "image" else "Watch the video"
        lines += ["", f"[{link_label}]({url})"]

    return "\n".join(lines)
