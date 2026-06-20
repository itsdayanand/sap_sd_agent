from pydantic import BaseModel, Field
from typing import List


class ChatHistoryTurn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., max_length=20_000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4_000)
    session_id: str = Field(..., min_length=1, max_length=36)
    history: List[ChatHistoryTurn] = Field(default_factory=list, max_length=200)


class ToolSummary(BaseModel):
    name: str
    description: str
