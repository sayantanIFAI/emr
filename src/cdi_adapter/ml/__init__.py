"""Model gateway client.

Pipeline stages talk to the vision-language model ONLY through this client (HTTP
to ``cdi_adapter.mlserve``). Swapping the serving backend (transformers now,
vLLM+AWQ+XGrammar later) never touches pipeline code.
"""
from .client import MLError, StubMLClient, HttpMLClient, get_client

__all__ = ["MLError", "StubMLClient", "HttpMLClient", "get_client"]
