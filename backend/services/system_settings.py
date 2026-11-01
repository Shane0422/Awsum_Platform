import os
import math
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from backend.core.data_paths import PROJECT_ROOT
from backend.core.local_env import load_local_env
from backend.database.pg_platform import PlatformSessionLocal
from backend.models_admin.system_settings import SystemSettings
from backend.models_admin.system_settings_audit import SystemSettingsAudit
from backend.services.mail_common import (
    EmailConfigurationError,
    MailDeliveryError,
    validate_email_address,
)

load_local_env({"AWSUM_STORAGE_ROOT", "AWSUM_DATA_ROOT"})

ENCRYPTION_KEY_ENV = "AWSUM_SYSTEM_SETTINGS_ENCRYPTION_KEY"
DEFAULT_FROM_EMAIL = "info@awsumsolution.com"
DEFAULT_SMTP_USERNAME = "info@awsumsolution.com"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_ENCRYPTION = "SSL-TLS"
SMTP_ENV_KEYS = (
    "AWSUM_SMTP_HOST",
    "AWSUM_SMTP_PORT",
    "AWSUM_SMTP_FROM_EMAIL",
    "AWSUM_SMTP_USERNAME",
    "AWSUM_SMTP_PASSWORD",
    "AWSUM_SMTP_SENDER_NAME",
    "AWSUM_SMTP_REPLY_TO_EMAIL",
    "AWSUM_SMTP_AUTHENTICATION",
    "AWSUM_SMTP_USE_SSL",
    "AWSUM_SMTP_STARTTLS",
    "AWSUM_SMTP_TIMEOUT",
)
DATE_FORMATS = {"YYYY-MM-DD", "MM/DD/YYYY", "DD/MM/YYYY"}
TIME_FORMATS = {"12h", "24h"}


@dataclass(frozen=True)
class SMTPConfiguration:
    source: str
    sender_name: str
    from_email: str
    reply_to_email: str | None
    host: str
    port: int
    username: str | None
    password: str | None
    encryption: str
    authentication: bool
    timeout: float


def encryption_key_value() -> str:
    raw_key = os.getenv(ENCRYPTION_KEY_ENV, "").strip()
    if not raw_key:
        # The key may have been added to the server-only .env after startup.
        load_local_env({ENCRYPTION_KEY_ENV})
        raw_key = os.getenv(ENCRYPTION_KEY_ENV, "").strip()
    return raw_key


def require_encryption_key() -> Fernet:
    raw_key = encryption_key_value()
    if not raw_key:
        raise EmailConfigurationError(
            f"{ENCRYPTION_KEY_ENV} is required before an SMTP password can be stored or used. "
            "Run tools\\init_encryption_key.py once on the server, then retry."
        )
    try:
        return Fernet(raw_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise EmailConfigurationError(f"{ENCRYPTION_KEY_ENV} is not a valid Fernet key.") from exc


def get_or_create_settings(db: Session, *, create: bool = False) -> SystemSettings | None:
    row = db.query(SystemSettings).filter(SystemSettings.i_settings_id == 1).first()
    if row is not None or not create:
        return row
    row = SystemSettings(i_settings_id=1, i_revision=1, c_from_email=DEFAULT_FROM_EMAIL)
    db.add(row)
    db.flush()
    return row


def get_storage_root(db: Session | None = None) -> tuple[Path, str]:
    environment_path = os.getenv("AWSUM_STORAGE_ROOT", "").strip()
    if os.getenv("AWSUM_DATA_ROOT", "").strip():
        raise RuntimeError("AWSUM_DATA_ROOT is retired. Migrate storage and configure AWSUM_STORAGE_ROOT.")
    if environment_path:
        return _storage_path(environment_path), "environment"

    if db is not None:
        settings = get_or_create_settings(db)
        configured_path = settings.c_data_root_path if settings else None
        if configured_path:
            return _storage_path(configured_path), "system_settings"
        return PROJECT_ROOT.parent / "Awsum_Data", "default"

    engine = PlatformSessionLocal.kw.get("bind")
    inspector = inspect(engine) if engine is not None else None
    if inspector is not None and inspector.has_table("tb_system_settings") and "c_data_root_path" in {
        column["name"] for column in inspector.get_columns("tb_system_settings")
    }:
        with PlatformSessionLocal() as session:
            # Bootstrap path lookup must work before unrelated settings columns are migrated.
            configured_path = session.query(SystemSettings.c_data_root_path).filter(
                SystemSettings.i_settings_id == 1
            ).scalar()
            if configured_path:
                return _storage_path(configured_path), "system_settings"
    return PROJECT_ROOT.parent / "Awsum_Data", "default"


def _storage_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("AWSUM storage root must be an absolute path.")
    path = path.resolve()
    if path == PROJECT_ROOT.resolve() or path.is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("Runtime storage must be outside Awsum_Platform. Migrate before changing the root.")
    return path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmailConfigurationError(f"{name} must be true or false.")


def _smtp_from_environment() -> SMTPConfiguration:
    host = os.getenv("AWSUM_SMTP_HOST", "").strip() or DEFAULT_SMTP_HOST
    from_email = os.getenv("AWSUM_SMTP_FROM_EMAIL", "").strip() or DEFAULT_FROM_EMAIL
    try:
        from_email = validate_email_address(from_email)
    except ValueError as exc:
        raise EmailConfigurationError("AWSUM_SMTP_FROM_EMAIL must be a valid email address.") from exc

    starttls_value = os.getenv("AWSUM_SMTP_STARTTLS")
    use_ssl_value = os.getenv("AWSUM_SMTP_USE_SSL")
    if use_ssl_value is None:
        use_ssl = not _env_bool("AWSUM_SMTP_STARTTLS", False) if starttls_value is not None else True
    else:
        use_ssl = _env_bool("AWSUM_SMTP_USE_SSL", True)
    raw_port = os.getenv("AWSUM_SMTP_PORT", "").strip() or ("465" if use_ssl else "587")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise EmailConfigurationError("AWSUM_SMTP_PORT must be a valid port number.") from exc
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("AWSUM_SMTP_PORT must be between 1 and 65535.")

    starttls = _env_bool("AWSUM_SMTP_STARTTLS", not use_ssl)
    if use_ssl and starttls:
        raise EmailConfigurationError("Configure either SSL-TLS or STARTTLS, not both.")
    encryption = "SSL-TLS" if use_ssl else ("STARTTLS" if starttls else "NONE")
    try:
        timeout = float(os.getenv("AWSUM_SMTP_TIMEOUT", "20"))
    except ValueError as exc:
        raise EmailConfigurationError("AWSUM_SMTP_TIMEOUT must be a positive number.") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise EmailConfigurationError("AWSUM_SMTP_TIMEOUT must be a positive number.")
    username = os.getenv("AWSUM_SMTP_USERNAME", "").strip() or None
    password = os.getenv("AWSUM_SMTP_PASSWORD", "") or None
    auth_value = os.getenv("AWSUM_SMTP_AUTHENTICATION")
    authentication = True if auth_value is None else _env_bool("AWSUM_SMTP_AUTHENTICATION", True)
    if authentication and not username:
        username = DEFAULT_SMTP_USERNAME
    try:
        sender_name = os.getenv("AWSUM_SMTP_SENDER_NAME", "AwsumSolution").strip() or "AwsumSolution"
        reply_to = os.getenv("AWSUM_SMTP_REPLY_TO_EMAIL", "").strip() or None
        if reply_to:
            reply_to = validate_email_address(reply_to)
    except ValueError as exc:
        raise EmailConfigurationError("AWSUM_SMTP_REPLY_TO_EMAIL must be a valid email address.") from exc
    return SMTPConfiguration(
        "environment",
        sender_name,
        from_email,
        reply_to,
        host,
        port,
        username,
        password,
        encryption,
        authentication,
        timeout,
    )


def smtp_environment_mode() -> bool:
    return any(name in os.environ for name in SMTP_ENV_KEYS)


def _system_settings_smtp_configuration(db: Session) -> SMTPConfiguration:
    row = get_or_create_settings(db)
    sender_name = (row.c_sender_name or "").strip() if row else ""
    from_email = (row.c_from_email or "").strip() if row else ""
    from_email = from_email or DEFAULT_FROM_EMAIL
    try:
        from_email = validate_email_address(from_email)
        reply_to = validate_email_address(row.c_reply_to_email) if row and row.c_reply_to_email else None
    except ValueError as exc:
        raise EmailConfigurationError("System Settings contains an invalid SMTP email address.") from exc

    encryption = (row.c_smtp_encryption or "").strip() if row else ""
    encryption = encryption or DEFAULT_SMTP_ENCRYPTION
    port = row.i_smtp_port if row and row.i_smtp_port is not None else (
        DEFAULT_SMTP_PORT if encryption == DEFAULT_SMTP_ENCRYPTION else 587
    )
    authentication = True if not row or row.b_smtp_authentication is None else bool(row.b_smtp_authentication)
    username = (row.c_smtp_username or "").strip() if row else ""
    username = username or DEFAULT_SMTP_USERNAME if authentication else ""
    password = None
    if authentication and row and row.c_smtp_password_encrypted:
        try:
            password = require_encryption_key().decrypt(
                row.c_smtp_password_encrypted.encode("ascii")
            ).decode("utf-8")
        except (EmailConfigurationError, InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
            password = None

    return SMTPConfiguration(
        "system_settings",
        sender_name or "AwsumSolution",
        from_email,
        reply_to,
        (row.c_smtp_host or "").strip() or DEFAULT_SMTP_HOST if row else DEFAULT_SMTP_HOST,
        port,
        username or None,
        password,
        encryption,
        authentication,
        20.0,
    )


def _smtp_missing_configuration(config: SMTPConfiguration, db: Session) -> list[str]:
    missing = []
    if not config.host:
        missing.append("SMTP Host missing")
    if not 1 <= config.port <= 65535:
        missing.append("SMTP Port is invalid")
    if config.authentication:
        if not config.username or not config.password:
            missing.append("SMTP authentication credentials not configured")
        if config.source == "system_settings":
            row = get_or_create_settings(db)
            if not encryption_key_value():
                missing.append("SMTP encryption key not configured")
            elif row and row.c_smtp_password_encrypted:
                try:
                    require_encryption_key().decrypt(row.c_smtp_password_encrypted.encode("ascii"))
                except (EmailConfigurationError, InvalidToken, UnicodeEncodeError):
                    missing.append("SMTP encryption key cannot decrypt the saved password")
    return missing


def get_smtp_configuration(db: Session) -> SMTPConfiguration:
    if smtp_environment_mode():
        config = _smtp_from_environment()
    else:
        config = _system_settings_smtp_configuration(db)
    missing = _smtp_missing_configuration(config, db)
    if missing:
        raise EmailConfigurationError("Email delivery is not ready: " + " / ".join(missing))
    return config


def smtp_public_status(db: Session) -> dict[str, Any]:
    try:
        config = _smtp_from_environment() if smtp_environment_mode() else _system_settings_smtp_configuration(db)
    except EmailConfigurationError as exc:
        environment_mode = smtp_environment_mode()
        use_ssl = os.getenv("AWSUM_SMTP_USE_SSL", "true").strip().lower() in {"1", "true", "yes", "on"}
        raw_port = os.getenv("AWSUM_SMTP_PORT", "").strip()
        try:
            fallback_port = int(raw_port) if raw_port else (465 if use_ssl else 587)
        except ValueError:
            fallback_port = 465 if use_ssl else 587
        auth_value = os.getenv("AWSUM_SMTP_AUTHENTICATION")
        authentication = (
            auth_value.strip().lower() not in {"0", "false", "no", "off"}
            if auth_value is not None else True
        )
        fallback_from_email = (
            os.getenv("AWSUM_SMTP_FROM_EMAIL", "").strip() or DEFAULT_FROM_EMAIL
            if environment_mode else DEFAULT_FROM_EMAIL
        )
        fallback_username = (
            os.getenv("AWSUM_SMTP_USERNAME", "").strip() or DEFAULT_SMTP_USERNAME
            if environment_mode and authentication else None
        )
        fallback_sender_name = "AwsumSolution"
        fallback_reply_to = None
        fallback_host = DEFAULT_SMTP_HOST
        fallback_encryption = "SSL-TLS" if use_ssl else "STARTTLS"
        fallback_password = os.getenv("AWSUM_SMTP_PASSWORD") or None if environment_mode else None
        fallback_authentication = authentication if environment_mode else True
        if not environment_mode:
            row = get_or_create_settings(db)
            fallback_from_email = (row.c_from_email or "").strip() or DEFAULT_FROM_EMAIL if row else DEFAULT_FROM_EMAIL
            fallback_encryption = (
                (row.c_smtp_encryption or "").strip() or DEFAULT_SMTP_ENCRYPTION
                if row else DEFAULT_SMTP_ENCRYPTION
            )
            fallback_port = row.i_smtp_port if row and row.i_smtp_port is not None else (
                DEFAULT_SMTP_PORT if fallback_encryption == DEFAULT_SMTP_ENCRYPTION else 587
            )
            fallback_authentication = (
                True if not row or row.b_smtp_authentication is None else bool(row.b_smtp_authentication)
            )
            fallback_username = (
                (row.c_smtp_username or "").strip() or DEFAULT_SMTP_USERNAME
                if row and fallback_authentication else None
            )
            fallback_sender_name = (row.c_sender_name or "").strip() or "AwsumSolution" if row else "AwsumSolution"
            fallback_reply_to = (row.c_reply_to_email or "").strip() or None if row else None
            fallback_host = (row.c_smtp_host or "").strip() or DEFAULT_SMTP_HOST if row else DEFAULT_SMTP_HOST
        config = SMTPConfiguration(
            "environment" if environment_mode else "system_settings",
            os.getenv("AWSUM_SMTP_SENDER_NAME", "").strip() or "AwsumSolution" if environment_mode else fallback_sender_name,
            fallback_from_email,
            os.getenv("AWSUM_SMTP_REPLY_TO_EMAIL", "").strip() or None if environment_mode else fallback_reply_to,
            os.getenv("AWSUM_SMTP_HOST", "").strip() if environment_mode else fallback_host,
            fallback_port,
            fallback_username,
            fallback_password,
            fallback_encryption,
            fallback_authentication,
            20.0,
        )
        validation_error = str(exc)
    else:
        validation_error = None

    missing = _smtp_missing_configuration(config, db)
    if config.authentication and not config.password and not any(
        item.startswith("SMTP authentication") for item in missing
    ):
        missing.append("SMTP authentication credentials not configured")
    ready = not missing and validation_error is None
    return {
        "source": config.source,
        "ready": ready,
        "status": "Configured" if ready else "Not configured",
        "host": config.host,
        "port": config.port,
        "from_email": config.from_email,
        "sender_name": config.sender_name,
        "reply_to_email": config.reply_to_email,
        "username": config.username or "",
        "encryption": config.encryption,
        "authentication": config.authentication,
        "missing": missing,
        "error": validation_error,
    }


def open_smtp_connection(config: SMTPConfiguration):
    use_ssl = config.encryption == "SSL-TLS"
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    options = {"context": ssl.create_default_context()} if use_ssl else {}
    smtp = smtp_class(config.host, config.port, timeout=config.timeout, **options)
    try:
        smtp.ehlo()
        if config.encryption == "STARTTLS":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        if config.authentication:
            smtp.login(config.username, config.password)
        return smtp
    except Exception:
        smtp.close()
        raise


def safe_mail_error(exc: Exception, config: SMTPConfiguration) -> str:
    detail = str(exc)
    for secret in (config.password, config.username):
        if secret:
            detail = detail.replace(secret, "[redacted]")
    return detail[:1000]


def send_configured_email(db: Session, message: EmailMessage) -> tuple[str, str]:
    from email.utils import make_msgid

    try:
        config = get_smtp_configuration(db)
    except EmailConfigurationError:
        raise

    if not message.get("From"):
        message["From"] = f"{config.sender_name} <{config.from_email}>"
    if config.reply_to_email and not message.get("Reply-To"):
        message["Reply-To"] = config.reply_to_email
    if not message.get("Message-ID"):
        message["Message-ID"] = make_msgid()
    message_id = str(message["Message-ID"])
    try:
        with open_smtp_connection(config) as smtp:
            refused = smtp.send_message(message)
            if refused:
                raise MailDeliveryError("Mail server refused the recipient.")
    except MailDeliveryError:
        raise
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailDeliveryError("Mail server refused the recipient.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailDeliveryError(safe_mail_error(exc, config)) from exc
    return message_id, "Accepted by SMTP server."


def record_settings_change(
    db: Session,
    row: SystemSettings,
    actor_id: int,
    changed_fields: list[str],
) -> None:
    row.i_revision = (row.i_revision or 0) + 1
    row.i_updated_by = actor_id
    row.dt_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(SystemSettingsAudit(
        i_settings_id=row.i_settings_id,
        i_revision=row.i_revision,
        i_changed_by=actor_id,
        c_changed_fields=",".join(sorted(set(changed_fields))),
    ))
