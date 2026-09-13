"""Shared OpenAI client bootstrap for everything under game.chat -- narration
(main.py), NPC hearing determination and dialogue replies (dialogue.py).
"""

import os

try:
    from openai import OpenAI
except ModuleNotFoundError:
    OpenAI = None

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def client():
    if OpenAI is None:
        raise RuntimeError("openai SDK is not installed; run: pip install -r requirements.txt")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured; add it to .env")
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])
