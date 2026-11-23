"""/users/me — profile, settings, password, account deletion."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.deps import CurrentUser, DbSession
from app.modules.users import service
from app.modules.users.schemas import PasswordChange, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def get_me(user: CurrentUser) -> UserOut:
    """The current user's profile and settings."""
    return service.user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: UserUpdate, user: CurrentUser, db: DbSession) -> UserOut:
    """Update name, timezone (IANA) and/or settings (partial merge)."""
    return service.user_out(service.update_user(db, user, body))


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, user: CurrentUser, db: DbSession) -> Response:
    """Change (or, for Google-only accounts, set) the password."""
    service.change_password(db, user, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_me(user: CurrentUser, db: DbSession) -> Response:
    """Permanently delete the account, its data and its stored media."""
    service.delete_user(db, user)
    from app.modules.auth.service import clear_auth_cookies

    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(resp)
    return resp
