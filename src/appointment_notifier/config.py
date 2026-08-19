from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _csv(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass(frozen=True)
class TelegramSettings:
    api_id: int
    api_hash: str
    session_path: Path
    channel: str
    history_limit: int


@dataclass(frozen=True)
class TelegramAlertSettings:
    enabled: bool
    bot_token: str
    chat_ids: tuple[str, ...]
    allowed_usernames: tuple[str, ...]
    owner_chat_ids: tuple[str, ...]
    owner_usernames: tuple[str, ...]


@dataclass(frozen=True)
class EmailSettings:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]
    starttls: bool


@dataclass(frozen=True)
class SmsSettings:
    enabled: bool
    account_sid: str
    auth_token: str
    sender: str
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class WhatsAppSettings:
    enabled: bool
    provider: str
    account_sid: str
    auth_token: str
    sender: str
    recipients: tuple[str, ...]
    openwa_url: str
    openwa_api_key: str


@dataclass(frozen=True)
class IMessageSettings:
    enabled: bool
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class TrendSettings:
    ollama_enabled: bool
    ollama_url: str
    ollama_model: str
    timezone: str
    chat_history_messages: int
    context_tokens: int
    response_tokens: int
    provider_order: tuple[str, ...] = ("nvidia", "ollama_cloud", "ollama")
    nvidia_enabled: bool = False
    nvidia_url: str = "https://integrate.api.nvidia.com"
    nvidia_model: str = "openai/gpt-oss-20b"
    nvidia_api_key_file: str = "/run/secrets/nvidia_api_key"
    nvidia_timeout_seconds: int = 90
    ollama_cloud_enabled: bool = False
    ollama_cloud_url: str = "https://ollama.com"
    ollama_cloud_model: str = "gpt-oss:20b"
    ollama_cloud_api_key_file: str = "/run/secrets/ollama_api_key"


@dataclass(frozen=True)
class AppSettings:
    telegram: TelegramSettings
    telegram_alert: TelegramAlertSettings
    email: EmailSettings
    sms: SmsSettings
    whatsapp: WhatsAppSettings
    imessage: IMessageSettings
    trend: TrendSettings
    sqlite_path: Path
    dry_run: bool
    log_level: str
    required_terms: tuple[str, ...]
    suppress_terms: tuple[str, ...]
    media_temp_dirs: tuple[Path, ...]


def load_settings(require_telegram: bool = True) -> AppSettings:
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if require_telegram and (not api_id or not api_hash):
        raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    return AppSettings(
        telegram=TelegramSettings(
            api_id=int(api_id or "0"),
            api_hash=api_hash,
            session_path=Path(os.getenv("TELEGRAM_SESSION_PATH", ".state/telegram.session")),
            channel=os.getenv("TELEGRAM_CHANNEL", "@Regular_H1B_H4_VisaSlotsChecking"),
            history_limit=_int("TELEGRAM_HISTORY_LIMIT", 25),
        ),
        telegram_alert=TelegramAlertSettings(
            enabled=_bool("TELEGRAM_ALERT_ENABLED"),
            bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_ids=_csv("TELEGRAM_ALERT_CHAT_ID"),
            allowed_usernames=_csv("TELEGRAM_BOT_ALLOWED_USERNAMES"),
            owner_chat_ids=_csv("TELEGRAM_BOT_OWNER_CHAT_ID"),
            owner_usernames=_csv("TELEGRAM_BOT_OWNER_USERNAMES"),
        ),
        email=EmailSettings(
            enabled=_bool("EMAIL_ENABLED"),
            host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            port=_int("SMTP_PORT", 587),
            username=os.getenv("SMTP_USERNAME", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            sender=os.getenv("SMTP_FROM", ""),
            recipients=_csv("SMTP_TO"),
            starttls=_bool("SMTP_STARTTLS", True),
        ),
        sms=SmsSettings(
            enabled=_bool("SMS_ENABLED"),
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            sender=os.getenv("TWILIO_FROM", ""),
            recipients=_csv("SMS_TO"),
        ),
        whatsapp=WhatsAppSettings(
            enabled=_bool("WHATSAPP_ENABLED"),
            provider=os.getenv("WHATSAPP_PROVIDER", "twilio").strip().lower(),
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            sender=os.getenv("WHATSAPP_FROM", ""),
            recipients=_csv("WHATSAPP_TO"),
            openwa_url=os.getenv(
                "OPENWA_URL", "http://host.docker.internal:8081"
            ).strip(),
            openwa_api_key=os.getenv("OPENWA_API_KEY", ""),
        ),
        imessage=IMessageSettings(
            enabled=_bool("IMESSAGE_ENABLED"),
            recipients=_csv("IMESSAGE_TO"),
        ),
        trend=TrendSettings(
            ollama_enabled=_bool("OLLAMA_ENABLED"),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:1b").strip(),
            timezone=os.getenv("TREND_TIMEZONE", "Asia/Kolkata").strip(),
            chat_history_messages=_int("OLLAMA_CHAT_HISTORY_MESSAGES", 8),
            context_tokens=_int("OLLAMA_CONTEXT_TOKENS", 2048),
            response_tokens=_int("OLLAMA_RESPONSE_TOKENS", 220),
            provider_order=_csv("LLM_PROVIDER_ORDER")
            or ("nvidia", "ollama_cloud", "ollama"),
            nvidia_enabled=_bool("NVIDIA_NIM_ENABLED"),
            nvidia_url=os.getenv(
                "NVIDIA_NIM_URL", "https://integrate.api.nvidia.com"
            ).rstrip("/"),
            nvidia_model=os.getenv(
                "NVIDIA_NIM_MODEL", "openai/gpt-oss-20b"
            ).strip(),
            nvidia_api_key_file=os.getenv(
                "NVIDIA_API_KEY_FILE", "/run/secrets/nvidia_api_key"
            ).strip(),
            nvidia_timeout_seconds=_int("NVIDIA_NIM_TIMEOUT_SECONDS", 90),
            ollama_cloud_enabled=_bool("OLLAMA_CLOUD_ENABLED"),
            ollama_cloud_url=os.getenv(
                "OLLAMA_CLOUD_URL", "https://ollama.com"
            ).rstrip("/"),
            ollama_cloud_model=os.getenv(
                "OLLAMA_CLOUD_MODEL", "gpt-oss:20b"
            ).strip(),
            ollama_cloud_api_key_file=os.getenv(
                "OLLAMA_CLOUD_API_KEY_FILE", "/run/secrets/ollama_api_key"
            ).strip(),
        ),
        sqlite_path=Path(os.getenv("SQLITE_PATH", ".state/appointment-notifier.sqlite3")),
        dry_run=_bool("DRY_RUN"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        required_terms=_csv("REQUIRED_TERMS"),
        suppress_terms=_csv("SUPPRESS_TERMS"),
        media_temp_dirs=tuple(
            Path(value)
            for value in (_csv("MEDIA_TEMP_DIRS") or ("/mnt/drive1/appointment-notifier-media", "/mnt/drive2/appointment-notifier-media", "/tmp/appointment-notifier-media"))
        ),
    )
