from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from .app import AppointmentNotifierApp
from .chat import OllamaChatService
from .config import load_settings
from .models import Alert
from .llm import build_llm_client
from .notifiers import build_notifier
from .parser import VisaSlotParser
from .store import AlertStore
from .telegram_watcher import run_async
from .trend import TrendService


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="appointment-notifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Watch Telegram and send slot alerts.")
    subparsers.add_parser("test-notify", help="Send a test alert through enabled notifiers.")
    parse_text = subparsers.add_parser("parse-text", help="Check how a Telegram message would be classified.")
    parse_text.add_argument("text")
    trend = subparsers.add_parser("trend", help="Analyze historical slot-posting trends.")
    trend.add_argument("--no-llm", action="store_true", help="Skip the optional Ollama summary.")
    trend.add_argument("--json", action="store_true", help="Print deterministic statistics as JSON.")
    ask = subparsers.add_parser("ask", help="Ask the local appointment assistant a question.")
    ask.add_argument("question")

    args = parser.parse_args(argv)

    if args.command == "parse-text":
        slot_parser = VisaSlotParser()
        signal = slot_parser.parse(args.text)
        print(f"matched={signal.matched} silent={signal.silent} reason={signal.reason}")
        if signal.locations:
            print(f"locations={','.join(signal.locations)}")
        if signal.visa_terms:
            print(f"visa_terms={','.join(signal.visa_terms)}")
        return

    settings = load_settings(require_telegram=args.command == "run")
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("twilio").setLevel(logging.WARNING)

    if args.command in {"trend", "ask"}:
        store = AlertStore(settings.sqlite_path)
        try:
            llm_client = build_llm_client(settings.trend)
            service = TrendService(store, settings.trend, llm_client)
            if args.command == "ask":
                chat = OllamaChatService(store, service, settings.trend, llm_client)
                if not chat.enabled:
                    parser.error("OLLAMA_ENABLED must be true for the ask command")
                print(run_async(chat.answer("cli", args.question)))
            elif args.json:
                print(json.dumps(service.report().as_dict(), indent=2))
            else:
                print(service.summarize(use_llm=not args.no_llm))
        finally:
            store.close()
        return

    notifier = build_notifier(
        settings.telegram_alert,
        settings.email,
        settings.sms,
        settings.whatsapp,
        settings.imessage,
        settings.dry_run,
    )

    if args.command == "test-notify":
        notifier.send(
            Alert(
                title="Appointment notifier test",
                body="This is a test alert from appointment-notifier.",
                source="local",
                message_id=0,
                sent_at=datetime.now(timezone.utc),
                silent=False,
            )
        )
        return

    store = AlertStore(settings.sqlite_path)
    try:
        app = AppointmentNotifierApp(
            settings=settings,
            parser=VisaSlotParser(settings.required_terms, settings.suppress_terms),
            store=store,
            notifier=notifier,
        )
        run_async(app.run())
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    finally:
        store.close()


if __name__ == "__main__":
    main()
