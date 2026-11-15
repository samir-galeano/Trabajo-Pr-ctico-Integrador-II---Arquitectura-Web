"""Rotación en caliente del token de BookStack (F7-E14)."""
import importlib
import os

import pytest


@pytest.fixture
def creds(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    monkeypatch.setenv("BOOKSTACK_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("BOOKSTACK_TOKEN_ID", "env-id")
    monkeypatch.setenv("BOOKSTACK_TOKEN_SECRET", "env-secret")
    from app import credentials
    module = importlib.reload(credentials)
    yield module, token_file
    monkeypatch.delenv("BOOKSTACK_TOKEN_FILE")
    importlib.reload(credentials)


def write(path, content, mtime_ns):
    # Escritura en el mismo inode (como hace el script de rotación)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_falls_back_to_env_without_file(creds):
    module, _ = creds
    assert module.get_token() == ("env-id", "env-secret")


def test_rotation_is_picked_up_without_restart(creds, capsys):
    module, token_file = creds

    write(token_file, "old-id:old-secret\n", 1_000_000_000)
    assert module.authorization_header() == "Token old-id:old-secret"

    write(token_file, "new-id:new-secret\n", 2_000_000_000)
    assert module.authorization_header() == "Token new-id:new-secret"

    logs = capsys.readouterr().out
    assert "new-id" in logs
    assert "secret" not in logs.replace("Token de BookStack", "")


def test_incomplete_file_keeps_previous_token(creds):
    module, token_file = creds

    write(token_file, "good-id:good-secret", 1_000_000_000)
    assert module.get_token() == ("good-id", "good-secret")

    write(token_file, "half-written", 2_000_000_000)
    assert module.get_token() == ("good-id", "good-secret")
    assert module.has_token()
