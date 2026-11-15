"""Autenticación Bearer de la API pública (esquema BearerAuth de los contratos).

Si API_AUTH_TOKEN está definido (uno o varios tokens separados por comas,
para poder rotarlos sin corte), toda ruta salvo /healthz, /readyz y /metrics
exige "Authorization: Bearer <token>"; si no, responde 401 JSON. Sin
API_AUTH_TOKEN la autenticación queda desactivada (desarrollo).
"""
import hmac
import os

from fastapi.responses import JSONResponse

TOKENS = [token.strip() for token in os.getenv("API_AUTH_TOKEN", "").split(",") if token.strip()]
PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics"}


def is_authorized(request):
    if not TOKENS or request.url.path in PUBLIC_PATHS:
        return True
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    return scheme.lower() == "bearer" and any(hmac.compare_digest(token, valid) for valid in TOKENS)


def unauthorized():
    return JSONResponse(
        status_code=401,
        content={"detail": "Token Bearer ausente o inválido"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def middleware(request, call_next):
    if not is_authorized(request):
        return unauthorized()
    return await call_next(request)
