import sys
from types import ModuleType
from unittest.mock import MagicMock

try:
    import httpx  # noqa: F401
except ImportError:
    httpx_stub = ModuleType("httpx")
    httpx_stub.AsyncClient = MagicMock()
    sys.modules["httpx"] = httpx_stub

try:
    import groq  # noqa: F401
except ImportError:
    groq_stub = ModuleType("groq")
    groq_stub.Groq = MagicMock()
    sys.modules["groq"] = groq_stub
