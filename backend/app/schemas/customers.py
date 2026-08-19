"""Pydantic request/response schemas for Customers."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CustomerBase(BaseModel):
    email: EmailStr
    name: str = Field(..., max_length=255)


class CustomerCreate(CustomerBase):
    auth_user_id: str | None = None


class CustomerResponse(CustomerBase):
    id: UUID
    auth_user_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
