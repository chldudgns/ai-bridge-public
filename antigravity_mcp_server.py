"""Small, local MCP bridge from Codex to the Google Antigravity CLI.

The bridge deliberately invokes agy without a shell. It does not handle or
store OAuth credentials; the user must authenticate agy once interactively.
"""

from __future__ import annotations

import os
import subprocess

from mcp.server import MCPServer


# Set AGY_PATH when agy is not available on PATH.
AGY = os.environ.get("AGY_PATH", "agy")
mcp = MCPServer("antigravity")


@mcp.tool()
def ask_antigravity(prompt: str) -> str:
    """Send one prompt to Google Antigravity and return its text response."""

    if not isinstance(prompt, str) or not prompt.strip():
        return "ERROR: prompt must be a non-empty string."

    try:
        result = subprocess.run(
            [
                AGY,
                "--print",
                prompt,
                "--output-format",
                "text",
                "--print-timeout",
                "120s",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=135,
            shell=False,
        )
    except FileNotFoundError:
        return f"ERROR: Antigravity CLI not found at {AGY!r}."
    except subprocess.TimeoutExpired:
        return "ERROR: Antigravity request timed out after 135 seconds."

    output = (result.stdout or result.stderr).strip()
    if not output:
        return f"ERROR: Antigravity exited with code {result.returncode}."
    return output


if __name__ == "__main__":
    mcp.run()
