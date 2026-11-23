"""Auth API models."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from app.modules.users.schemas import UserOut


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    full_name: str = Field(min_length=1, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class RefreshIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class AuthOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class AuthorizationUrlOut(BaseModel):
    authorization_url: str
