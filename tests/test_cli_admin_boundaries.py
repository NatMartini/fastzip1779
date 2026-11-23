"""Exercise CLI-created admins through real database writes and HTTP login."""

import sys
from unittest.mock import Mock

import pytest
from fastzip import cli
from fastzip.db import session_factory
from fastzip.models import User
from fastzip.security import hasher
from sqlalchemy import func, select


def invoke_admin(monkeypatch, email, passwords):
    monkeypatch.setattr(sys, "argv", ["fastzip", "create-admin", email])
    monkeypatch.setattr(cli.getpass, "getpass", Mock(side_effect=passwords))
    cli.main()


@pytest.mark.parametrize("length", [12, 128])
def test_cli_password_boundaries_create_admin_who_can_login(client, monkeypatch, capsys, length):
    password = "p" * length
    invoke_admin(monkeypatch, "Admin@Example.com", [password, password])
    captured = capsys.readouterr()
    assert "Administrator created" in captured.out
    assert password not in captured.out + captured.err
    with session_factory()() as db:
        account = db.scalar(select(User))
        assert account.email == "admin@example.com"
        assert account.role == "admin"
        assert hasher.verify(account.password_hash, password)
    response = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": password}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert client.get("/api/admin/overview").status_code == 200


@pytest.mark.parametrize(
    "passwords",
    [["p" * 11], ["p" * 129], ["p" * 12, "different-password"]],
    ids=["too-short", "too-long", "confirmation-mismatch"],
)
def test_invalid_cli_password_does_not_create_an_account(monkeypatch, capsys, passwords):
    with pytest.raises(SystemExit) as rejected:
        invoke_admin(monkeypatch, "admin@example.com", passwords)
    assert rejected.value.code == 2
    assert "Passwords must match" in capsys.readouterr().err
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_existing_user_cannot_be_promoted_through_duplicate_cli_creation(
    account, monkeypatch, capsys
):
    with session_factory()() as db:
        before = db.scalar(select(User)).password_hash
    with pytest.raises(SystemExit) as rejected:
        invoke_admin(monkeypatch, account["email"].upper(), ["valid-password-here"] * 2)
    assert rejected.value.code == 2
    assert "Account already exists" in capsys.readouterr().err
    with session_factory()() as db:
        rows = list(db.scalars(select(User)))
        assert len(rows) == 1
        assert rows[0].role == "user"
        assert rows[0].password_hash == before


@pytest.mark.parametrize("email", ["invalid", "missing@", "@example.com"])
def test_invalid_cli_email_is_rejected_before_reading_password(monkeypatch, capsys, email):
    monkeypatch.setattr(sys, "argv", ["fastzip", "create-admin", email])

    def unexpected_prompt(_):
        pytest.fail("An invalid email must be rejected before prompting for a password")

    monkeypatch.setattr(cli.getpass, "getpass", unexpected_prompt)
    with pytest.raises(SystemExit) as rejected:
        cli.main()
    assert rejected.value.code == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "Invalid email address."
    assert captured.out == ""
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("entered", [[], ["valid-test-password"]], ids=["password", "confirmation"])
@pytest.mark.parametrize(
    "stop, code", [(EOFError, 1), (KeyboardInterrupt, 130)], ids=["eof", "ctrl-c"]
)
def test_cancelling_password_input_exits_without_creating_an_admin(
    monkeypatch, capsys, entered, stop, code
):
    with pytest.raises(SystemExit) as cancelled:
        invoke_admin(monkeypatch, "admin@example.com", [*entered, stop])
    assert cancelled.value.code == code
    captured = capsys.readouterr()
    assert captured.err.strip() == "Administrator creation cancelled."
    assert captured.out == ""
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
