"""
Nous - Persistent Memory System

HTTP client for the TypeScript Nous server.
The knowledge graph that makes Hynous remember.

Usage:
    from hynous.nous.client import get_client

    client = get_client()
    node = client.create_node(
        type="concept",
        subtype="custom:lesson",
        title="NVDA earnings spike",
        body="Volume surged after earnings beat..."
    )
    results = client.search("earnings spike")
"""

from .client import NousClient, get_client
from .server import ensure_running

__all__ = ["NousClient", "get_client", "ensure_running"]
