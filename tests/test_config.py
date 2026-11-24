import pytest

from omebox.config import Settings, settings


def test_placeholder_secrets_are_rejected():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings(
            secret_key="replace-with-at-least-32-random-characters",
            encryption_key=settings.encryption_key,
            mongodb_url="mongodb://127.0.0.1:27017",
        )
    with pytest.raises(RuntimeError, match="ADMIN_API_KEY"):
        Settings(
            secret_key=settings.secret_key,
            encryption_key=settings.encryption_key,
            mongodb_url="mongodb://127.0.0.1:27017",
            admin_api_key="replace-with-a-separate-random-token",
        )


def test_short_secret_is_rejected():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings(
            secret_key="too-short",
            encryption_key=settings.encryption_key,
            mongodb_url="mongodb://127.0.0.1:27017",
        )
