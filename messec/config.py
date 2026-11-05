"""Application configuration, read from environment variables."""

import os
import secrets
from datetime import timedelta
from pathlib import Path


class ConfigError(RuntimeError):
    """The server is configured in a way that is unsafe to start."""


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _database_url(instance_path):
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{Path(instance_path) / 'messec.db'}"
    # Hosts hand out postgres:// URLs; SQLAlchemy needs the driver spelled out.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _secret_key(instance_path, production):
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    if production:
        raise ConfigError("SECRET_KEY must be set when MESSEC_ENV=production")
    # Local use: keep one random key on disk so logins survive restarts.
    path = Path(instance_path) / "secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key


def build_config(instance_path):
    Path(instance_path).mkdir(parents=True, exist_ok=True)
    production = os.environ.get("MESSEC_ENV", "development").strip().lower() == "production"
    device_key = os.environ.get("MESSEC_DEVICE_KEY", "").strip()
    if production and device_key == "change-me":
        raise ConfigError("MESSEC_DEVICE_KEY is still the example value 'change-me'")

    return {
        "MESSEC_PRODUCTION": production,
        "SECRET_KEY": _secret_key(instance_path, production),
        "SQLALCHEMY_DATABASE_URI": _database_url(instance_path),
        "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True},
        "MESSEC_DEVICE_KEY": device_key,
        "MESSEC_SIMULATE": env_flag("MESSEC_SIMULATE", False),
        "MESSEC_AUTO_ASSIGN_SECONDS": int(os.environ.get("MESSEC_AUTO_ASSIGN_SECONDS", "60")),
        "MESSEC_STATION_ACK_SECONDS": int(os.environ.get("MESSEC_STATION_ACK_SECONDS", "120")),
        "MESSEC_SWEEP_SECONDS": 5,
        "MESSEC_TRUST_PROXY": env_flag("MESSEC_TRUST_PROXY", production),
        "MESSEC_START_BACKGROUND": False,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": production,
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=12),
        "WTF_CSRF_TIME_LIMIT": None,  # tokens live as long as the 12-hour session
        "RATELIMIT_STORAGE_URI": os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_ENABLED": True,
        "MAX_CONTENT_LENGTH": 1024 * 1024,
    }
