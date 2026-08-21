# AI Bridge — Public Review Copy

Public code-only review copy of local bridge components for GPT/Codex, Gemini, and Google Antigravity communication.

This repository is intentionally sanitized for public inspection. It does not contain local credentials, runtime state, logs, task results, or private machine configuration.

## Security

Credentials are local-only. Set `GEMINI_API_KEY` in an ignored `google.env` file or the process environment. Do not commit API keys, OAuth codes, tokens, certificates, logs, runtime state, or task artifacts.

## Files

- `bridge.py`: bridge orchestration
- `app.py`: local application entry point
- `mcp_server.py`: Gemini MCP server
- `antigravity_mcp_server.py`: Antigravity MCP server
- `antigravity_mcp_server.mjs`: Node-based Antigravity bridge

This repository is intended for code inspection and collaboration. Each machine must configure and authenticate its own local integrations.
