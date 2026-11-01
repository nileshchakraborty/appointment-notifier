"""Durable Telegram delivery through a SQLite outbox and Kafka wake-up stream."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
from dataclasses import replace
from datetime import datetime, timezone

from .config import KafkaQueueSettings
from .models import Alert
from .notifiers import TelegramBotNotifier
from .store import AlertStore

LOGGER = logging.getLogger(__name__)


class KafkaPublisher:
    def __init__(self, settings: KafkaQueueSettings) -> None:
        from confluent_kafka import Producer

        self.settings = settings
        self.producer = Producer({
            "bootstrap.servers": settings.bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "delivery.timeout.ms": 5000,
        })

    def publish(self, job_ids: list[str]) -> list[str]:
        reports: dict[str, object] = {}
        for job_id in job_ids:
            try:
                self.producer.produce(
                    self.settings.topic,
                    key=job_id.encode(),
                    value=job_id.encode(),
                    on_delivery=lambda error, _message, key=job_id: reports.__setitem__(key, error),
                )
            except BufferError:
                LOGGER.warning("Kafka producer queue full; outbox will retry")
                break
        self.producer.flush(6)
        return [job_id for job_id, error in reports.items() if error is None]


async def run_publisher(store: AlertStore, settings: KafkaQueueSettings) -> None:
    publisher = KafkaPublisher(settings)
    while True:
        try:
            job_ids = store.due_telegram_jobs()
            if job_ids:
                published = await asyncio.to_thread(publisher.publish, job_ids)
                for job_id in published:
                    store.mark_telegram_job_published(job_id)
                if published:
                    LOGGER.info("Published %s Telegram delivery jobs to Kafka", len(published))
            await asyncio.sleep(0.2 if job_ids else 0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Telegram outbox publication failed; retrying")
            await asyncio.sleep(2)


def process_telegram_job(
    store: AlertStore,
    notifier: TelegramBotNotifier,
    job_id: str,
    *,
    now: datetime | None = None,
) -> str:
    job = store.get_telegram_job(job_id)
    if job is None:
        LOGGER.error("Kafka referenced unknown Telegram job %s", job_id)
        return "unknown"
    if job["status"] != "pending":
        return str(job["status"])
    current = now or datetime.now(timezone.utc)
    if float(job["next_attempt_at"]) > current.timestamp():
        return "waiting"

    data = json.loads(str(job["alert_json"]))
    sent_at = datetime.fromisoformat(data["sent_at"]) if data["sent_at"] else None
    alert = Alert(
        title=data["title"], body=data["body"], source=data["source"],
        message_id=data["message_id"], sent_at=sent_at, silent=data["silent"],
    )
    if sent_at and (current - sent_at).total_seconds() > 300:
        alert = replace(alert, body=f"Delayed report from {sent_at.isoformat()}; availability may have changed.\n\n{alert.body}")
    try:
        notifier.send_to(str(job["chat_id"]), alert)
    except urllib.error.HTTPError as exc:
        reason = f"Telegram HTTP {exc.code}"
        if exc.code in {400, 403}:
            store.mark_telegram_job_dead(job_id, reason)
            LOGGER.warning("Telegram job %s permanently rejected: %s", job_id, reason)
            return "dead"
        store.mark_telegram_job_retry(job_id, reason, now=current.timestamp())
        LOGGER.warning("Telegram job %s will retry: %s", job_id, reason)
        return "retry"
    except Exception as exc:
        store.mark_telegram_job_retry(job_id, type(exc).__name__, now=current.timestamp())
        LOGGER.warning("Telegram job %s will retry after %s", job_id, type(exc).__name__)
        return "retry"

    store.mark_telegram_job_delivered(job_id)
    LOGGER.info(
        "Telegram job %s delivered in %.0fms from enqueue",
        job_id,
        (time.time() - float(job["queued_at"])) * 1000,
    )
    return "delivered"


def run_worker(store: AlertStore, notifier: TelegramBotNotifier, settings: KafkaQueueSettings) -> None:
    from confluent_kafka import Consumer, KafkaException

    consumer = Consumer({
        "bootstrap.servers": settings.bootstrap_servers,
        "group.id": "appointment-telegram-delivery-v1",
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([settings.topic])
    LOGGER.info("Telegram delivery worker listening on Kafka topic %s", settings.topic)
    try:
        while True:
            record = consumer.poll(0.2)
            if record is None:
                continue
            if record.error():
                raise KafkaException(record.error())
            try:
                process_telegram_job(store, notifier, record.value().decode())
                consumer.commit(message=record, asynchronous=False)
            except Exception:
                LOGGER.exception("Telegram Kafka record processing failed; leaving offset uncommitted")
                raise
    finally:
        consumer.close()
