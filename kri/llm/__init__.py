"""LLM-powered intelligent review system for KRI.

Provides multi-agent patch analysis using Claude API, producing
line-specific review comments formatted as lore-style email replies.
"""

from kri.llm.client import LLMClient, LLMConfig

__all__ = ["LLMClient", "LLMConfig"]
