from __future__ import annotations

import json
import logging
import smtplib
import subprocess
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import EmailSettings, IMessageSettings, SmsSettings, TelegramAlertSettings, WhatsAppSettings
from .models import Alert

LOGGER = logging.getLogger(__name__)


class Notifier:
    supports_silent = False

    def send(self, alert: Alert) -> None:
        raise NotImplementedError


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier], dry_run: bool = False) -> None:
        self.notifiers = notifiers
        self.dry_run = dry_run

    def send(self, alert: Alert) -> None:
        if self.dry_run:
            LOGGER.info("DRY_RUN alert: %s\n%s", alert.title, alert.body)
            return

        if not self.notifiers:
            LOGGER.warning("No notifiers enabled; alert was not delivered: %s", alert.title)
            return

        delivered = False
        for notifier in self.notifiers:
            if alert.silent and not notifier.supports_silent:
                LOGGER.info("Skipping %s for silent alert", notifier.__class__.__name__)
                continue
            notifier.send(alert)
            delivered = True

        if not delivered:
            LOGGER.warning("No compatible notifiers delivered alert: %s", alert.title)


class EmailNotifier(Notifier):
    def __init__(self, settings: EmailSettings) -> None:
        self.settings = settings
        self._validate()

    def _validate(self) -> None:
        missing = []
        if not self.settings.host:
            missing.append("SMTP_HOST")
        if not self.settings.sender:
            missing.append("SMTP_FROM")
        if not self.settings.recipients:
            missing.append("SMTP_TO")
        if missing:
            raise ValueError(f"Email enabled but missing: {', '.join(missing)}")

    def send(self, alert: Alert) -> None:
        message = EmailMessage()
        message["Subject"] = alert.title
        message["From"] = self.settings.sender
        message["To"] = ", ".join(self.settings.recipients)
        message.set_content(alert.body)

        with smtplib.SMTP(self.settings.host, self.settings.port, timeout=30) as smtp:
            if self.settings.starttls:
                smtp.starttls()
            if self.settings.username:
                smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(message)


class TelegramBotNotifier(Notifier):
    supports_silent = True

    def __init__(self, settings: TelegramAlertSettings) -> None:
        self.settings = settings
        missing = []
        if not settings.bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not settings.chat_ids:
            missing.append("TELEGRAM_ALERT_CHAT_ID")
        if missing:
            raise ValueError(f"Telegram alert enabled but missing: {', '.join(missing)}")

    def send(self, alert: Alert) -> None:
        text = f"*{_escape_markdown(alert.title)}*\n{_escape_markdown(alert.body)}"
        url = f"https://api.telegram.org/bot{self.settings.bot_token}/sendMessage"
        for chat_id in self.settings.chat_ids:
            payload = urllib.parse.urlencode(
                {
                    "chat_id": chat_id,
                    "text": text[:4096],
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": "true",
                    "disable_notification": "true" if alert.silent else "false",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AppointmentNotifier/1.0)"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"Telegram Bot API returned HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="ignore")
                LOGGER.warning(
                    "Telegram Bot send failed for chat_id=%s (HTTP %s): %s",
                    chat_id,
                    exc.code,
                    err_body,
                )


class TwilioSmsNotifier(Notifier):
    def __init__(self, settings: SmsSettings) -> None:
        self.settings = settings
        self._validate()

    def _validate(self) -> None:
        missing = []
        if not self.settings.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.settings.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.settings.sender:
            missing.append("TWILIO_FROM")
        if not self.settings.recipients:
            missing.append("SMS_TO")
        if missing:
            raise ValueError(f"SMS enabled but missing: {', '.join(missing)}")

    def send(self, alert: Alert) -> None:
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError("Install SMS support with: pip install -e '.[sms]'") from exc

        client = Client(self.settings.account_sid, self.settings.auth_token)
        body = f"{alert.title}\n{alert.body}"
        for recipient in self.settings.recipients:
            client.messages.create(body=body[:1500], from_=self.settings.sender, to=recipient)


class TwilioWhatsAppNotifier(Notifier):
    def __init__(self, settings: WhatsAppSettings) -> None:
        self.settings = settings
        missing = []
        if not self.settings.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.settings.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.settings.sender:
            missing.append("WHATSAPP_FROM")
        if not self.settings.recipients:
            missing.append("WHATSAPP_TO")
        if missing:
            raise ValueError(f"WhatsApp enabled but missing: {', '.join(missing)}")

    def send(self, alert: Alert) -> None:
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise RuntimeError("Install WhatsApp support with: pip install -e '.[sms]'") from exc

        client = Client(self.settings.account_sid, self.settings.auth_token)
        body = f"{alert.title}\n{alert.body}"
        sender = _format_whatsapp_address(self.settings.sender)
        for recipient in self.settings.recipients:
            client.messages.create(
                body=body[:1500],
                from_=sender,
                to=_format_whatsapp_address(recipient),
            )


class OpenWaWhatsAppNotifier(Notifier):
    def __init__(self, settings: WhatsAppSettings) -> None:
        self.settings = settings
        missing = []
        if not settings.openwa_url:
            missing.append("OPENWA_URL")
        if not settings.openwa_api_key:
            missing.append("OPENWA_API_KEY")
        if not settings.recipients:
            missing.append("WHATSAPP_TO")
        if missing:
            raise ValueError(f"OpenWA WhatsApp enabled but missing: {', '.join(missing)}")

    def send(self, alert: Alert) -> None:
        url = f"{self.settings.openwa_url.rstrip('/')}/api/messages/sendText"
        content = f"{alert.title}\n{alert.body}"[:4096]
        for recipient in self.settings.recipients:
            payload = json.dumps(
                {
                    "args": {
                        "to": _format_openwa_chat_id(recipient),
                        "content": content,
                    }
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.settings.openwa_api_key,
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
                if response.status >= 400 or not result.get("success"):
                    error = result.get("error", "unknown OpenWA error")
                    if isinstance(error, dict):
                        error = error.get("message", "unknown OpenWA error")
                    raise RuntimeError(f"OpenWA sendText failed: {error}")


class IMessageNotifier(Notifier):
    def __init__(self, settings: IMessageSettings) -> None:
        self.settings = settings
        if not settings.recipients:
            raise ValueError("iMessage enabled but missing IMESSAGE_TO")

    def send(self, alert: Alert) -> None:
        body = f"{alert.title}\n{alert.body}"
        for recipient in self.settings.recipients:
            script = (
                'tell application "Messages"\n'
                f'  set targetService to 1st service whose service type = iMessage\n'
                f'  set targetBuddy to buddy "{_escape_applescript(recipient)}" of targetService\n'
                f'  send "{_escape_applescript(body)}" to targetBuddy\n'
                "end tell\n"
            )
            subprocess.run(["osascript", "-e", script], check=True, timeout=30)


def build_notifier(
    telegram_alert: TelegramAlertSettings,
    email: EmailSettings,
    sms: SmsSettings,
    whatsapp: WhatsAppSettings,
    imessage: IMessageSettings,
    dry_run: bool,
) -> CompositeNotifier:
    notifiers: list[Notifier] = []
    if telegram_alert.enabled:
        notifiers.append(TelegramBotNotifier(telegram_alert))
    if email.enabled:
        notifiers.append(EmailNotifier(email))
    if sms.enabled:
        notifiers.append(TwilioSmsNotifier(sms))
    if whatsapp.enabled:
        if whatsapp.provider == "twilio":
            notifiers.append(TwilioWhatsAppNotifier(whatsapp))
        elif whatsapp.provider in {"openwa", "open-wa"}:
            notifiers.append(OpenWaWhatsAppNotifier(whatsapp))
        else:
            raise ValueError(
                f"Unsupported WHATSAPP_PROVIDER: {whatsapp.provider}. Expected twilio or openwa."
            )
    if imessage.enabled:
        notifiers.append(IMessageNotifier(imessage))
    return CompositeNotifier(notifiers, dry_run=dry_run)


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _escape_markdown(value: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{char}" if char in special else char for char in value)


def _format_whatsapp_address(value: str) -> str:
    value = value.strip()
    if value.startswith("whatsapp:"):
        return value
    return f"whatsapp:{value}"


def _format_openwa_chat_id(value: str) -> str:
    value = value.strip()
    if value.endswith(("@c.us", "@g.us")):
        return value
    if value.startswith("whatsapp:"):
        value = value.removeprefix("whatsapp:")
    digits = "".join(char for char in value if char.isdigit())
    if not digits:
        raise ValueError(f"Invalid OpenWA recipient: {value!r}")
    return f"{digits}@c.us"
