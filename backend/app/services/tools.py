"""Compatibility import for the shared LLM JSON normalizer.

The former module contained a second, unused tool framework. Production tools
are implemented under :mod:`app.agent_base.tools`.
"""

from app.core.json_utils import clean_llm_json_response

__all__ = ["clean_llm_json_response"]
