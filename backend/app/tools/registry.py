from typing import Dict
from .base import SAPTool


class ToolRegistry:
    """
    Central registry for all agent tools.
    Tools are registered by name and looked up
    during the agent loop when the LLM requests a tool call.
    """

    def __init__(self):
        self._tools: Dict[str, SAPTool] = {}

    def register(self, tool: SAPTool):
        self._tools[tool.name] = tool
        print(f"[ToolRegistry] Registered tool: {tool.name}")

    def get(self, name: str) -> SAPTool:
        if name not in self._tools:
            raise KeyError(
                f"Tool '{name}' not found in registry. "
                f"Available tools: {list(self._tools.keys())}"
            )
        return self._tools[name]

    def all_schemas(self) -> list:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def list_tools(self) -> list:
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]


# Global singleton registry
registry = ToolRegistry()
