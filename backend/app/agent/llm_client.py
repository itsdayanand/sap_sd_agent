import os
from openai import AsyncOpenAI

# OpenAI-compatible client. Works with GPT-4o / GPT-4o-mini directly,
# or any OpenAI-compatible endpoint via LLM_BASE_URL.
client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY", ""),
    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
)

MODEL = os.getenv("LLM_MODEL", "gpt-4o")


async def call_llm(messages: list, tools: list = None, stream: bool = False):
    """
    Call the LLM with the given messages and optional tool schemas.
    Returns a streaming or non-streaming completion object.
    """
    kwargs = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2000,
        "stream": stream,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    return await client.chat.completions.create(**kwargs)
