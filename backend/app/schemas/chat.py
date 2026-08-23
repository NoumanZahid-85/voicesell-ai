"""Chat request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="Customer message")
    session_id: str = Field(..., min_length=1, max_length=200, description="Conversation session identifier")
    debug: bool = Field(default=False, description="Include gate diagnostics in the response (temporary)")


class ChatSource(BaseModel):
    product_id: str
    name: str
    price: float
    score: float


class ChatResponse(BaseModel):
    reply: str
    sources: list[ChatSource]
    cached: bool = False
    debug: dict | None = None
