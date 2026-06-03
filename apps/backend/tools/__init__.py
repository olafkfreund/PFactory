"""
Reusable Tool Execution Package
================================

Provider-agnostic tool definitions and executor for LLM tool calling.

Any LLM provider that supports tool/function calling (Ollama, OpenAI-compatible,
llama.cpp, etc.) can use this package to execute tools locally with security
enforcement.

Modules:
    definitions — Tool JSON schemas in Ollama/OpenAI format
    executor    — ToolExecutor class for safe local execution
"""

from .definitions import get_tool_definitions
from .executor import ToolExecutor

__all__ = ["get_tool_definitions", "ToolExecutor"]
