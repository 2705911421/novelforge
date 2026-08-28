"""Small shared seam for HTTP authentication-to-Host identity propagation.

The current deployment supports one bearer credential.  That credential is
therefore represented by one configured Host principal; request payloads are
never allowed to replace it on an authenticated request.  Multi-user token
issuance and role management remain an application concern outside this
module.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
import os
from typing import Any


DEFAULT_API_PRINCIPAL = "studio"
REQUEST_PRINCIPAL_STATE = "novelforge_principal"
_REQUEST_PRINCIPAL_CONTEXT: ContextVar[str | None] = ContextVar(
    "novelforge_request_principal",
    default=None,
)


class RequestPrincipalUnavailable(RuntimeError):
    """Raised when an authenticated route has no middleware-established identity."""


def configured_api_principal() -> str:
    """Return the trusted principal represented by the configured API key.

    This value is deployment configuration, not request input.  ``studio`` is
    the deliberately compatible default for the single-user local Host.
    """
    return (
        str(os.environ.get("NOVELFORGE_API_PRINCIPAL", DEFAULT_API_PRINCIPAL)).strip().lower()
        or DEFAULT_API_PRINCIPAL
    )


def set_request_principal(request: Any, principal: str | None) -> None:
    """Attach the result of bearer authentication to the current request."""
    setattr(request.state, REQUEST_PRINCIPAL_STATE, principal)


def bind_request_principal(request: Any, principal: str | None) -> Token:
    """Attach and bind one authenticated principal for the current request task.

    The request-state copy serves route authorization.  The context copy serves
    Host-owned seams that do not receive a FastAPI ``Request`` object (for
    example the Studio task proxy).  Middleware must reset the returned token
    after ``call_next`` so a worker or a later request cannot inherit identity.
    """
    set_request_principal(request, principal)
    normalized = str(principal or "").strip().lower() or None
    return _REQUEST_PRINCIPAL_CONTEXT.set(normalized)


def reset_request_principal(token: Token) -> None:
    """Restore the request-principal context captured before middleware entry."""
    _REQUEST_PRINCIPAL_CONTEXT.reset(token)


def current_request_principal() -> str | None:
    """Return the middleware-bound principal for the current async task."""
    return _REQUEST_PRINCIPAL_CONTEXT.get()


def request_actor(
    request: Any,
    requested_actor: str | None = None,
    *,
    auth_required: bool = False,
) -> str:
    """Resolve an audit/authority actor without trusting authenticated input.

    Development mode preserves the legacy body-actor contract for local test
    and embedded callers.  Once authentication is required, only the
    middleware-established principal is accepted; an absent principal is a
    fail-closed programming/deployment error rather than a body-actor fallback.
    """
    principal = getattr(request.state, REQUEST_PRINCIPAL_STATE, None)
    if auth_required:
        if not isinstance(principal, str) or not principal.strip():
            raise RequestPrincipalUnavailable("authenticated request has no Host principal")
        return principal.strip().lower()
    return str(requested_actor or DEFAULT_API_PRINCIPAL).strip() or DEFAULT_API_PRINCIPAL


__all__ = [
    "DEFAULT_API_PRINCIPAL",
    "REQUEST_PRINCIPAL_STATE",
    "RequestPrincipalUnavailable",
    "bind_request_principal",
    "configured_api_principal",
    "current_request_principal",
    "request_actor",
    "reset_request_principal",
    "set_request_principal",
]
