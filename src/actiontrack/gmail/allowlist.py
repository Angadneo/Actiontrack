"""Hard-coded Gmail safety allow-list (spec GM-02, G-16; T-M1-04).

Single source of truth for the host and the tools the Gmail gateways may use. These are
code constants: no config key, environment variable or CLI option may supply or widen
them. ``config.py`` only *validates* ``gmail.google_mcp_url`` against ``ALLOWED_MCP_HOST``;
the M7/M8 gateway imports the same constants.

Never add a send-capable tool here (``send_message``, ``reply``, ``forward``,
``update_draft``, ``delete_draft``, ``trash_*``, ``mark_*_spam``).
"""

from __future__ import annotations

ALLOWED_MCP_HOST: str = "gmailmcp.googleapis.com"
ALLOWED_TOOLS: frozenset[str] = frozenset({"create_draft", "get_draft"})
