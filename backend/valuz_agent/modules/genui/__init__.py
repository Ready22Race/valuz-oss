"""Generative-UI module — the ``generate_ui`` MCP tool.

Produces OpenUI Lang (from the vendored ``genui-lib`` prompt) via a one-shot
ephemeral-session LLM call; the frontend renders the result with OpenUI's
``<Renderer>``. Mirrors ``modules/memory/`` (tool + completer seam).
"""
