import os

from google import genai
from mcp.server import MCPServer


mcp = MCPServer("gemini")


@mcp.tool()
def ask_gemini(question: str) -> str:
    """Ask Gemini a question and return its answer."""

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return "ERROR: GEMINI_API_KEY is not set."

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
    )

    return response.text or "(Gemini returned no text.)"


if __name__ == "__main__":
    mcp.run()