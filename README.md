# ⚡️ mcpo

Expose any MCP tool as an OpenAPI-compatible HTTP server—instantly.

mcpo is a dead-simple proxy that takes an MCP command and makes it accessible via standard RESTful OpenAPI, so your tools "just work" with LLM agents and apps expecting OpenAPI servers.

No custom protocol. No glue code. No hassle.

## 🚀 Quick Usage

We recommend using uv for lightning-fast startup and zero config.

```bash
uvx mcpo --port 8000 -- uvx mcp-server-time --local-timezone=America/New_York
```

Or, if you’re using Python:

```bash
pip install mcpo
mcpo --host 0.0.0.0 --port 8000 -- uvx mcp-server-time --local-timezone=America/New_York
```

That’s it. Your MCP tool is now available at http://localhost:8000 with a generated OpenAPI schema.

## 🔧 Requirements

- Python 3.8+
- MCP tool installed (e.g. mcp-server-time)
- uv (optional, but highly recommended for performance + packaging)

## 🪪 License

MIT