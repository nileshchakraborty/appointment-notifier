from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any
from pathlib import Path

from .models import Alert, SlotSignal, TelegramMessage
from .parser import VisaSlotParser


class AlertStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            create table if not exists alerts (
              digest text primary key,
              message_id integer not null,
              title text,
              body text,
              source text,
              sent_at text,
              silent integer not null default 0,
              created_at text not null default current_timestamp
            )
            """
        )
        self.conn.execute(
            """
            create table if not exists bot_state (
              key text primary key,
              value text not null
            )
            """
        )
        self.conn.commit()
        self._migrate_alert_columns()
        self._init_availability_state()
        self._init_bot_users()
        self._init_observations()
        self._init_chat_history()

    def record_observation(self, message: TelegramMessage, signal: SlotSignal) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into observed_messages
                  (message_id, source_chat_id, text, sent_at, source, has_image, matched, reason,
                   silent, available_state, category, ocr_text, portal_state, locations, visa_terms)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(source_chat_id, message_id) do update set
                  text = excluded.text,
                  source_chat_id = excluded.source_chat_id,
                  sent_at = excluded.sent_at,
                  source = excluded.source,
                  has_image = excluded.has_image,
                  matched = excluded.matched,
                  reason = excluded.reason,
                  silent = excluded.silent,
                  available_state = excluded.available_state,
                  category = excluded.category,
                  ocr_text = excluded.ocr_text,
                  portal_state = excluded.portal_state,
                  locations = excluded.locations,
                  visa_terms = excluded.visa_terms
                """,
                (
                    message.message_id,
                    message.source_chat_id,
                    message.text,
                    message.sent_at.isoformat() if message.sent_at else None,
                    message.url,
                    1 if message.has_image else 0,
                    1 if signal.matched else 0,
                    signal.reason,
                    1 if signal.silent else 0,
                    None if signal.available_state is None else (1 if signal.available_state else 0),
                    signal.category,
                    getattr(signal, "ocr_text", ""),
                    getattr(signal, "portal_state", None),
                    json.dumps(signal.locations),
                    json.dumps(signal.visa_terms),
                ),
            )

    def record_telegram_message(self, message: TelegramMessage, *, media_sha256: str | None = None) -> None:
        """Persist raw canonical source metadata before deriving a classification."""
        with self.conn:
            self.conn.execute(
                """
                insert into telegram_messages
                  (chat_id, message_id, text, sent_at, source, has_image, media_sha256)
                values (?, ?, ?, ?, ?, ?, ?)
                on conflict(chat_id, message_id) do update set
                  text = excluded.text, sent_at = excluded.sent_at,
                  source = excluded.source, has_image = excluded.has_image,
                  media_sha256 = coalesce(excluded.media_sha256, telegram_messages.media_sha256),
                  updated_at = current_timestamp
                """,
                (message.source_chat_id, message.message_id, message.text,
                 message.sent_at.isoformat() if message.sent_at else None,
                 message.url, 1 if message.has_image else 0, media_sha256),
            )

    def set_backfill_cursor(self, chat_id: str, message_id: int | None) -> None:
        self.set_state(f"backfill:{chat_id}", "" if message_id is None else str(message_id))

    def backfill_cursor(self, chat_id: str) -> int | None:
        value = self.get_state(f"backfill:{chat_id}")
        return int(value) if value and value.isdigit() else None

    def get_media_analysis(self, sha256: str) -> dict[str, object] | None:
        row = self.conn.execute("select * from media_analysis where sha256 = ?", (sha256,)).fetchone()
        return dict(row) if row else None

    def record_media_analysis(self, analysis) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into media_analysis (sha256, ocr_text, features, portal_state)
                values (?, ?, ?, ?)
                on conflict(sha256) do update set
                  ocr_text = excluded.ocr_text,
                  features = excluded.features,
                  portal_state = excluded.portal_state
                """,
                (analysis.sha256, analysis.ocr_text, json.dumps(analysis.features, sort_keys=True), analysis.portal_state),
            )

    def reclassify_observations(self, parser) -> int:
        """Upgrade rows written before category-aware parsing was introduced."""
        rows = self.conn.execute(
            "select message_id, source_chat_id, text, has_image from observed_messages where category = 'unknown'"
        ).fetchall()
        with self.conn:
            for row in rows:
                signal = parser.parse(str(row["text"]), bool(row["has_image"]))
                self.conn.execute(
                    """
                    update observed_messages
                    set matched = ?, reason = ?, silent = ?, available_state = ?,
                        category = ?, locations = ?, visa_terms = ?
                    where source_chat_id = ? and message_id = ?
                    """,
                    (
                        1 if signal.matched else 0,
                        signal.reason,
                        1 if signal.silent else 0,
                        None if signal.available_state is None else (1 if signal.available_state else 0),
                        signal.category,
                        json.dumps(signal.locations),
                        json.dumps(signal.visa_terms),
                        row["source_chat_id"], row["message_id"],
                    ),
                )
        return len(rows)

    def trend_points(self) -> tuple[list[dict[str, object]], str]:
        observed = self.conn.execute(
            """
            select message_id, sent_at, text, source, has_image, category, ocr_text, portal_state, locations, visa_terms
            from observed_messages
            where matched = 1 and sent_at is not null
            order by sent_at
            """
        ).fetchall()
        first_matched_at = str(observed[0]["sent_at"]) if observed else None
        legacy_query = """
            select message_id, sent_at, body as text, source, 0 as has_image,
                   'legacy' as category, '' as ocr_text, '' as portal_state, '[]' as locations, '[]' as visa_terms
            from alerts
            where title is not null and sent_at is not null
        """
        params: tuple[object, ...] = ()
        if first_matched_at:
            legacy_query += " and sent_at < ?"
            params = (first_matched_at,)
        legacy_query += " order by sent_at"
        legacy = self.conn.execute(legacy_query, params).fetchall()
        legacy_parser = VisaSlotParser()
        legacy_rows = []
        for row in legacy:
            item = dict(row)
            signal = legacy_parser.parse(str(item.get("text") or ""), has_image=False)
            if signal.matched:
                item["category"] = signal.category
            legacy_rows.append(item)
        combined = legacy_rows + [dict(row) for row in observed]
        combined.sort(key=lambda row: str(row.get("sent_at") or ""))
        if observed and legacy:
            source = "classified observations with legacy baseline"
        elif observed:
            source = "classified observations"
        else:
            source = "legacy alerts"
        return combined, source

    def classification_summary(self, since: str | None = None) -> dict[str, int]:
        query = "select category, count(*) as count from observed_messages"
        params: tuple[object, ...] = ()
        if since:
            query += " where sent_at >= ?"
            params = (since,)
        query += " group by category"
        summary = {str(row["category"]): int(row["count"]) for row in self.conn.execute(query, params)}
        # Include pre-observation alerts in exclusion counts. Older databases
        # have useful history only in ``alerts`` and otherwise make /trend
        # report an artificially clean dataset.
        observed_ids = {
            int(row["message_id"])
            for row in self.conn.execute("select message_id from observed_messages")
        }
        legacy_query = "select message_id, body, sent_at from alerts where title is not null"
        legacy_params: tuple[object, ...] = ()
        if since:
            legacy_query += " and sent_at >= ?"
            legacy_params = (since,)
        parser = VisaSlotParser()
        for row in self.conn.execute(legacy_query, legacy_params):
            if int(row["message_id"]) in observed_ids:
                continue
            signal = parser.parse(str(row["body"] or ""))
            if signal.category:
                summary[signal.category] = summary.get(signal.category, 0) + 1
        return summary

    def recent_chat_messages(self, chat_id: str, limit: int) -> list[dict[str, str]]:
        rows = self.conn.execute(
            """
            select role, content
            from telegram_chat_messages
            where chat_id = ?
            order by id desc
            limit ?
            """,
            (chat_id, max(0, limit)),
        ).fetchall()
        return [
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in reversed(rows)
        ]

    def record_chat_exchange(
        self,
        chat_id: str,
        question: str,
        answer: str,
        history_limit: int,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "insert into telegram_chat_messages (chat_id, role, content) values (?, 'user', ?)",
                (chat_id, question),
            )
            self.conn.execute(
                "insert into telegram_chat_messages (chat_id, role, content) values (?, 'assistant', ?)",
                (chat_id, answer),
            )
            self.conn.execute(
                """
                delete from telegram_chat_messages
                where chat_id = ? and id not in (
                  select id from telegram_chat_messages
                  where chat_id = ?
                  order by id desc
                  limit ?
                )
                """,
                (chat_id, chat_id, max(2, history_limit)),
            )

    def clear_chat_history(self, chat_id: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "delete from telegram_chat_messages where chat_id = ?",
                (chat_id,),
            )
        return cursor.rowcount

    def has_alert(self, message: TelegramMessage) -> bool:
        digest = self._digest(message)
        # Telegram message edits retain the same message id but change text;
        # do not notify repeatedly for every edited copy.
        row = self.conn.execute(
            "select 1 from alerts where digest = ? or (source_chat_id = ? and message_id = ?)",
            (digest, message.source_chat_id, message.message_id),
        ).fetchone()
        return row is not None

    def is_new(self, message: TelegramMessage) -> bool:
        """Backward-compatible read-only dedupe check.

        Reservation before delivery loses alerts permanently when a notifier
        fails.  Callers should record the alert only after successful delivery.
        """
        return not self.has_alert(message)

    def record_alert(self, message: TelegramMessage, alert: Alert) -> None:
        digest = self._digest(message)
        with self.conn:
            self.conn.execute(
                """
                insert into alerts (digest, message_id, source_chat_id, title, body, source, sent_at, silent)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(digest) do update set
                  title = excluded.title,
                  source_chat_id = excluded.source_chat_id,
                  body = excluded.body,
                  source = excluded.source,
                  sent_at = excluded.sent_at,
                  silent = excluded.silent
                """,
                (
                    digest,
                    message.message_id,
                    message.source_chat_id,
                    alert.title,
                    alert.body,
                    alert.source,
                    alert.sent_at.isoformat() if alert.sent_at else None,
                    1 if alert.silent else 0,
                ),
            )

    def recent_alerts(self, limit: int = 5) -> list[dict[str, object]]:
        cursor = self.conn.execute(
            """
            select message_id, title, body, source, sent_at, silent, created_at
            from alerts
            where title is not null and body is not null
            order by sent_at desc, created_at desc
            limit ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("select value from bot_state where key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def save_trend_snapshot(self, report: dict[str, object], *, source: str = "backfill") -> None:
        with self.conn:
            self.conn.execute(
                "insert into trend_snapshots (source, report_json) values (?, ?)",
                (source, json.dumps(report, sort_keys=True)),
            )

    def latest_trend_snapshot(self) -> dict[str, object] | None:
        row = self.conn.execute(
            "select report_json from trend_snapshots order by id desc limit 1"
        ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(str(row["report_json"]))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def set_state(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "insert into bot_state (key, value) values (?, ?) on conflict(key) do update set value = excluded.value",
                (key, value),
            )

    def set_availability(
        self,
        available: bool,
        message: TelegramMessage,
        reason: str,
        alert: Alert | None = None,
    ) -> None:
        source = alert.source if alert else message.url
        summary = alert.body if alert else message.text
        sent_at = message.sent_at.isoformat() if message.sent_at else None
        with self.conn:
            self.conn.execute(
                """
                update availability_state
                set currently_available = ?,
                    current_message_id = ?,
                    current_source = ?,
                    current_summary = ?,
                    current_reason = ?,
                    current_seen_at = ?,
                    last_available_at = case when ? then ? else last_available_at end,
                    last_available_message_id = case when ? then ? else last_available_message_id end,
                    last_available_source = case when ? then ? else last_available_source end,
                    updated_at = current_timestamp
                where id = 1
                """,
                (
                    1 if available else 0,
                    message.message_id,
                    source,
                    summary,
                    reason,
                    sent_at,
                    1 if available else 0,
                    sent_at,
                    1 if available else 0,
                    message.message_id,
                    1 if available else 0,
                    source,
                ),
            )

    def availability_state(self) -> dict[str, object]:
        row = self.conn.execute(
            """
            select currently_available, current_message_id, current_source, current_summary,
                   current_reason, current_seen_at, last_available_at,
                   last_available_message_id, last_available_source, updated_at
            from availability_state
            where id = 1
            """
        ).fetchone()
        if not row:
            self._init_availability_state()
            row = self.conn.execute("select * from availability_state where id = 1").fetchone()
        data = dict(row)
        data["currently_available"] = bool(data["currently_available"])
        return data

    def ensure_bot_user(
        self,
        *,
        chat_id: str | None = None,
        username: str | None = None,
        display_name: str | None = None,
        is_owner: bool = False,
        alerts_enabled: bool = True,
    ) -> None:
        chat_id = _clean_optional(chat_id)
        username = _normalize_username(username or "")
        display_name = _clean_optional(display_name)
        if not chat_id and not username:
            raise ValueError("chat_id or username is required")

        existing = self._find_bot_user(chat_id=chat_id, username=username)
        with self.conn:
            if existing:
                self.conn.execute(
                    """
                    update telegram_bot_users
                    set chat_id = coalesce(?, chat_id),
                        username = coalesce(?, username),
                        display_name = coalesce(?, display_name),
                        is_owner = case when ? then 1 else is_owner end,
                        alerts_enabled = case when ? then 1 else alerts_enabled end,
                        updated_at = current_timestamp
                    where id = ?
                    """,
                    (
                        chat_id,
                        username,
                        display_name,
                        1 if is_owner else 0,
                        1 if alerts_enabled else 0,
                        existing["id"],
                    ),
                )
                return

            self.conn.execute(
                """
                insert into telegram_bot_users
                  (chat_id, username, display_name, is_owner, alerts_enabled)
                values (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    username,
                    display_name,
                    1 if is_owner else 0,
                    1 if alerts_enabled else 0,
                ),
            )

    def remove_bot_user(self, identifier: str) -> bool:
        chat_id, username = _split_bot_user_identifier(identifier)
        existing = self._find_bot_user(chat_id=chat_id, username=username)
        if not existing or existing["is_owner"]:
            return False
        with self.conn:
            cursor = self.conn.execute("delete from telegram_bot_users where id = ?", (existing["id"],))
        return cursor.rowcount == 1

    def bot_user_allowed(self, *, chat_id: str | None, username: str | None) -> bool:
        chat_id = _clean_optional(chat_id)
        username = _normalize_username(username or "")
        return self._bot_user_matches(chat_id=chat_id, username=username, owner_only=False)

    def bot_user_is_owner(self, *, chat_id: str | None, username: str | None) -> bool:
        chat_id = _clean_optional(chat_id)
        username = _normalize_username(username or "")
        return self._bot_user_matches(chat_id=chat_id, username=username, owner_only=True)

    def list_bot_users(self) -> list[dict[str, Any]]:
        cursor = self.conn.execute(
            """
            select chat_id, username, display_name, is_owner, alerts_enabled, created_at, updated_at
            from telegram_bot_users
            order by is_owner desc, username is null, username, chat_id
            """
        )
        users = []
        for row in cursor.fetchall():
            data = dict(row)
            data["is_owner"] = bool(data["is_owner"])
            data["alerts_enabled"] = bool(data["alerts_enabled"])
            users.append(data)
        return users

    def bot_user_count(self) -> int:
        row = self.conn.execute("select count(*) as count from telegram_bot_users").fetchone()
        return int(row["count"])

    @staticmethod
    def _digest(message: TelegramMessage) -> str:
        normalized = " ".join(message.text.lower().split())
        key = f"{message.source_chat_id}:{message.message_id}:{normalized}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _migrate_alert_columns(self) -> None:
        existing = {
            row["name"]
            for row in self.conn.execute("pragma table_info(alerts)").fetchall()
        }
        migrations = {
            "source_chat_id": "alter table alerts add column source_chat_id text not null default 'legacy'",
            "title": "alter table alerts add column title text",
            "body": "alter table alerts add column body text",
            "source": "alter table alerts add column source text",
            "sent_at": "alter table alerts add column sent_at text",
            "silent": "alter table alerts add column silent integer not null default 0",
        }
        with self.conn:
            for column, statement in migrations.items():
                if column not in existing:
                    self.conn.execute(statement)

    def _init_availability_state(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                create table if not exists availability_state (
                  id integer primary key check (id = 1),
                  currently_available integer not null default 0,
                  current_message_id integer,
                  current_source text,
                  current_summary text,
                  current_reason text,
                  current_seen_at text,
                  last_available_at text,
                  last_available_message_id integer,
                  last_available_source text,
                  updated_at text not null default current_timestamp
                )
                """
            )
            self.conn.execute(
                "insert or ignore into availability_state (id, currently_available) values (1, 0)"
            )

    def _init_bot_users(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                create table if not exists telegram_bot_users (
                  id integer primary key,
                  chat_id text unique,
                  username text unique,
                  display_name text,
                  alerts_enabled integer not null default 1,
                  is_owner integer not null default 0,
                  created_at text not null default current_timestamp,
                  updated_at text not null default current_timestamp,
                  check (chat_id is not null or username is not null)
                )
                """
            )

    def _init_observations(self) -> None:
        with self.conn:
            existing = self.conn.execute("pragma table_info(observed_messages)").fetchall()
            if existing and any(row[1] == "message_id" and row[5] == 1 for row in existing):
                self.conn.execute("alter table observed_messages rename to observed_messages_legacy")
                legacy_columns = {row[1] for row in self.conn.execute("pragma table_info(observed_messages_legacy)")}
                for name, definition in {
                    "category": "text not null default 'unknown'",
                    "ocr_text": "text not null default ''",
                    "portal_state": "text",
                    "locations": "text not null default '[]'",
                    "visa_terms": "text not null default '[]'",
                }.items():
                    if name not in legacy_columns:
                        self.conn.execute(f"alter table observed_messages_legacy add column {name} {definition}")
                self.conn.execute(
                    """
                    create table observed_messages (
                      message_id integer not null,
                      source_chat_id text not null default 'legacy',
                      text text not null, sent_at text, source text,
                      has_image integer not null default 0, matched integer not null,
                      reason text not null, silent integer not null default 0,
                      available_state integer, category text not null default 'unknown',
                      ocr_text text not null default '', portal_state text,
                      locations text not null default '[]', visa_terms text not null default '[]',
                      created_at text not null default current_timestamp,
                      primary key (source_chat_id, message_id)
                    )
                    """
                )
                self.conn.execute(
                    """
                    insert into observed_messages
                    (message_id, source_chat_id, text, sent_at, source, has_image, matched,
                     reason, silent, available_state, category, ocr_text, portal_state, locations, visa_terms, created_at)
                    select message_id, 'legacy', text, sent_at, source, has_image, matched,
                           reason, silent, available_state, category, ocr_text, portal_state, locations, visa_terms, created_at
                    from observed_messages_legacy
                    """
                )
                self.conn.execute("drop table observed_messages_legacy")
            self.conn.execute(
                """
                create table if not exists observed_messages (
                  message_id integer not null,
                  source_chat_id text not null default 'legacy',
                  text text not null,
                  sent_at text,
                  source text,
                  has_image integer not null default 0,
                  matched integer not null,
                  reason text not null,
                  silent integer not null default 0,
                  available_state integer,
                  category text not null default 'unknown',
                  ocr_text text not null default '',
                  portal_state text,
                  locations text not null default '[]',
                  visa_terms text not null default '[]',
                  created_at text not null default current_timestamp,
                  primary key (source_chat_id, message_id)
                )
                """
            )
            columns = {row[1] for row in self.conn.execute("pragma table_info(observed_messages)")}
            if "category" not in columns:
                self.conn.execute("alter table observed_messages add column category text not null default 'unknown'")
            if "ocr_text" not in columns:
                self.conn.execute("alter table observed_messages add column ocr_text text not null default ''")
            if "portal_state" not in columns:
                self.conn.execute("alter table observed_messages add column portal_state text")
            if "source_chat_id" not in columns:
                self.conn.execute("alter table observed_messages add column source_chat_id text not null default 'legacy'")
            self.conn.execute(
                """
                create table if not exists telegram_messages (
                  chat_id text not null,
                  message_id integer not null,
                  text text not null default '',
                  sent_at text,
                  source text,
                  has_image integer not null default 0,
                  media_sha256 text,
                  created_at text not null default current_timestamp,
                  updated_at text not null default current_timestamp,
                  primary key (chat_id, message_id)
                )
                """
            )
            self.conn.execute(
                """
                create table if not exists media_analysis (
                  sha256 text primary key,
                  ocr_text text not null default '',
                  features text not null default '{}',
                  portal_state text not null,
                  created_at text not null default current_timestamp
                )
                """
            )

    def _init_chat_history(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                create table if not exists telegram_chat_messages (
                  id integer primary key,
                  chat_id text not null,
                  role text not null check (role in ('user', 'assistant')),
                  content text not null,
                  created_at text not null default current_timestamp
                )
                """
            )

            self.conn.execute(
                """
                create table if not exists trend_snapshots (
                  id integer primary key,
                  source text not null,
                  report_json text not null,
                  created_at text not null default current_timestamp
                )
                """
            )
            self.conn.execute(
                """
                create index if not exists telegram_chat_messages_chat_id_id
                on telegram_chat_messages (chat_id, id)
                """
            )

    def _find_bot_user(
        self,
        *,
        chat_id: str | None = None,
        username: str | None = None,
    ) -> sqlite3.Row | None:
        chat_id = _clean_optional(chat_id)
        username = _normalize_username(username or "")
        if chat_id:
            row = self.conn.execute(
                "select * from telegram_bot_users where chat_id = ?",
                (chat_id,),
            ).fetchone()
            if row:
                return row
        if username:
            return self.conn.execute(
                "select * from telegram_bot_users where username = ?",
                (username,),
            ).fetchone()
        return None

    def _bot_user_matches(
        self,
        *,
        chat_id: str | None,
        username: str | None,
        owner_only: bool,
    ) -> bool:
        clauses = []
        params = []
        if chat_id:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if username:
            clauses.append("username = ?")
            params.append(username)
        if not clauses:
            return False
        owner_clause = " and is_owner = 1" if owner_only else ""
        row = self.conn.execute(
            f"select 1 from telegram_bot_users where ({' or '.join(clauses)}){owner_clause} limit 1",
            tuple(params),
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self.conn.close()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _normalize_username(username: str) -> str | None:
    cleaned = username.strip().removeprefix("@").lower()
    return cleaned or None


def _split_bot_user_identifier(identifier: str) -> tuple[str | None, str | None]:
    cleaned = identifier.strip()
    if not cleaned:
        raise ValueError("identifier is required")
    if cleaned.startswith("@") or not cleaned.lstrip("-").isdigit():
        return None, _normalize_username(cleaned)
    return cleaned, None
