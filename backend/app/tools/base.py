from abc import ABC, abstractmethod
from typing import Any


class SAPTool(ABC):
    """
    Base class for all SAP agent tools.
    Every tool must declare its name, description,
    and implement the execute() method.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name used by the LLM to invoke this tool."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """
        Plain-language description of what this tool does.
        This is injected into the LLM system prompt / function schema.
        """
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """
        JSON Schema definition of the tool's input parameters.
        Used to generate the OpenAI function calling schema.
        """
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """
        Execute the tool with the given parameters.
        Returns a JSON-serializable result.
        """
        ...

    def to_openai_schema(self) -> dict:
        """Convert this tool to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
