"""Agen: a fault-tolerant language for agentic AI, automation and cloud deployment."""

__version__ = "0.1.0"

from .interpreter import Interpreter, RuntimeOptions  # noqa: E402
from .parser import parse  # noqa: E402

__all__ = ["Interpreter", "RuntimeOptions", "parse", "__version__"]
