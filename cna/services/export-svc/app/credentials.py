"""Token de la API de BookStack con rotación en caliente (C4, F7-E14).

Si BOOKSTACK_TOKEN_FILE apunta a un archivo con el formato "token_id:token_secret",
el token se lee de ahí y se vuelve a leer cuando el archivo cambia: rotarlo no
requiere reconstruir la imagen ni reiniciar el contenedor. Si el archivo no
existe, se usan BOOKSTACK_TOKEN_ID y BOOKSTACK_TOKEN_SECRET del entorno.
"""
import json
import os
import threading
from datetime import datetime, timezone

TOKEN_FILE = os.getenv("BOOKSTACK_TOKEN_FILE", "")
ENV_TOKEN = (
    os.getenv("BOOKSTACK_TOKEN_ID", ""),
    os.getenv("BOOKSTACK_TOKEN_SECRET", ""),
)

_lock = threading.Lock()
_cache = {"stamp": None, "token": ENV_TOKEN}


def _log(message, **extra):
    print(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": os.getenv("OTEL_SERVICE_NAME", "bookstack-client"),
        "request_id": "system",
        "message": message,
        **extra,
    }, ensure_ascii=False), flush=True)


def get_token():
    """Devuelve (token_id, token_secret); cadenas vacías si no hay token."""
    if not TOKEN_FILE:
        return ENV_TOKEN

    try:
        stat = os.stat(TOKEN_FILE)
    except OSError:
        return ENV_TOKEN

    stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    with _lock:
        if stamp != _cache["stamp"]:
            try:
                with open(TOKEN_FILE, encoding="utf-8") as handle:
                    token_id, _, secret = handle.read().strip().partition(":")
            except OSError:
                return _cache["token"]

            token = (token_id.strip(), secret.strip())
            if all(token):
                previous = _cache["token"][0]
                _cache["token"] = token
                if previous != token[0]:
                    # Solo el ID del token: el secreto nunca se escribe en logs.
                    _log("Token de BookStack cargado desde archivo", token_id=token[0])
            _cache["stamp"] = stamp

        return _cache["token"]


def has_token():
    return all(get_token())


def authorization_header():
    token_id, secret = get_token()
    return f"Token {token_id}:{secret}"
