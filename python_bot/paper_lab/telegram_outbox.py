"""
Persistent Telegram PAPER SIGNAL Outbox.
- Bot token read strictly from environment / external secret file (NEVER stored in SQLite).
- Token redacted from all logs and error strings.
- Strict 5-condition delivery gate before enqueueing.
- One logical alert per (signal_id, chat_id).
- Stores Telegram message_id on success.
- Retries confirmed transient failures ONLY while signal is still fresh & actionable.
- Marks network timeouts with uncertain delivery as 'UNCERTAIN' (NEVER blindly resends).
"""

from __future__ import annotations

from .config import STUBAY_NOTICE_SECONDS

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from telegram_notifier import send_telegram_photo

from .config import (
    PaperLabConfig,
    compute_break_even_probability,
    get_telegram_bot_token,
    redact_text,
)
from .storage import PaperLabStore, make_stable_id, ts_to_iso, utc_now_ts


class TelegramUncertainTimeout(Exception):
    """Raised when a Telegram HTTP request timed out and delivery status is unknown."""


class TelegramTransientFailure(Exception):
    """Raised on confirmed transient HTTP 429/5xx or connection-refused before delivery."""


def format_paper_signal_message(
    asset: str,
    direction: str,
    strike: float,
    signal_ts: int,
    expiry_ts: int,
    payout: float,
    estimated_prob: float,
    model_version_id: str,
    rule_version_id: str,
    feed_kind: str,
) -> str:
    dir_icon = "🟢 CALL" if direction.upper() == "CALL" else "🔴 PUT"
    feed_tag = "Platform-Settled" if feed_kind == "platform_settled" else "Proxy Simulation"
    return (
        "📄 <b>PAPER SIGNAL</b> — Stubay Ai\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Asset: <b>{asset}</b> ({feed_tag})\n"
        f"🎯 Direction: <b>{dir_icon}</b>\n"
        f"💵 Reference Price / Strike: <code>{strike:.6f}</code>\n"
        f"🕒 Signal Time (UTC): <code>{ts_to_iso(signal_ts)}</code>\n"
        f"⏳ Expiry Time (UTC): <code>{ts_to_iso(expiry_ts)}</code>\n"
        f"💰 Assumed Payout: <b>{payout * 100:.1f}%</b>\n"
        f"📈 Estimated Win Probability (Estimate): <b>{estimated_prob * 100:.1f}%</b>\n"
        f"🧩 Strategy Version: <code>{model_version_id} / {rule_version_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def default_telegram_http_transport(
    bot_token: str, chat_id: str, text: str, timeout_sec: int = 8, image: Optional[bytes] = None
) -> Dict[str, Any]:
    """
    Send HTML message via Telegram Bot API.
    Distinguishes confirmed transient failures from uncertain timeouts.
    """
    if not bot_token:
        raise TelegramTransientFailure("TELEGRAM_BOT_TOKEN is not configured in environment.")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        if image:
            data = send_telegram_photo(bot_token, chat_id, image, text, timeout=timeout_sec, raise_errors=True)
        else:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return {"ok": True, "message_id": str(data.get("result", {}).get("message_id", ""))}
        raise RuntimeError(redact_text(str(data.get("description", "Telegram API error"))))
    except (socket.timeout, TimeoutError) as exc:
        raise TelegramUncertainTimeout(
            f"Telegram request timed out (delivery uncertain): {redact_text(str(exc))}"
        ) from exc
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code == 429 or code >= 500:
            raise TelegramTransientFailure(f"Telegram transient HTTP {code}") from exc
        raise RuntimeError(f"Telegram permanent HTTP {code}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            raise TelegramUncertainTimeout(
                f"Telegram network timeout (delivery uncertain): {redact_text(str(reason))}"
            ) from exc
        raise TelegramTransientFailure(
            f"Telegram connection error (not delivered): {redact_text(str(reason))}"
        ) from exc


class TelegramOutboxManager:
    """Manages strict signal gating and persistent Telegram delivery outbox."""

    def __init__(
        self,
        store: PaperLabStore,
        config: PaperLabConfig,
        transport: Optional[Callable[[str, str, str, int], Dict[str, Any]]] = None,
    ):
        self.store = store
        self.config = config
        self.transport = transport or default_telegram_http_transport

    def check_eligibility(
        self,
        asset: str,
        expiry_sec: int,
        quote_age_sec: int,
        expiry_ts: int,
        now_ts: int,
        payout: Optional[float],
        estimated_prob: float,
        fee_rate: float,
        tie_rule: str,
        min_edge_margin: float,
        model_record: Optional[Dict[str, Any]],
        rule_record: Optional[Dict[str, Any]],
        strategy_paused: bool,
    ) -> Tuple[bool, str]:
        if not self.config.telegram_enabled:
            return False, "telegram_disabled"
        if not self.config.telegram_allowed_chat_ids:
            return False, "no_configured_chat_ids"
        if asset.upper() not in self.config.asset_allowlist:
            return False, "asset_not_in_allowlist"
        if int(expiry_sec) not in self.config.expiry_allowlist_sec:
            return False, "expiry_not_in_allowlist"
        if strategy_paused:
            return False, "strategy_paused_or_rolled_back"
        if not model_record or model_record.get("status") != "APPROVED" or not int(model_record.get("verified_for_telegram", 0)):
            return False, "model_not_verified_for_telegram"
        if not rule_record or rule_record.get("status") != "APPROVED" or not int(rule_record.get("verified_for_telegram", 0)):
            return False, "rule_not_verified_for_telegram"
        if quote_age_sec > int(self.config.max_quote_age_sec):
            return False, f"stale_quote({quote_age_sec}s)"
        if payout is None or payout <= 0:
            return False, "payout_unknown"
        sec_left = int(expiry_ts) - int(now_ts)
        if sec_left < int(self.config.telegram_min_actionable_sec_left):
            return False, f"insufficient_time_left({sec_left}s)"
        be_prob = compute_break_even_probability(payout=payout, fee_rate=fee_rate, tie_rule=tie_rule)
        if estimated_prob < (be_prob + float(min_edge_margin)):
            return False, f"edge_below_margin({estimated_prob:.4f}<{be_prob + min_edge_margin:.4f})"
        return True, "eligible"

    def enqueue_paper_signal(
        self,
        signal_id: str,
        asset: str,
        direction: str,
        strike: float,
        signal_ts: int,
        expiry_ts: int,
        payout: float,
        estimated_prob: float,
        model_version_id: str,
        rule_version_id: str,
        feed_kind: str,
        now_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        now = now_ts if now_ts is not None else utc_now_ts()
        text = format_paper_signal_message(
            asset=asset,
            direction=direction,
            strike=strike,
            signal_ts=signal_ts,
            expiry_ts=expiry_ts,
            payout=payout,
            estimated_prob=estimated_prob,
            model_version_id=model_version_id,
            rule_version_id=rule_version_id,
            feed_kind=feed_kind,
        )
        return self.enqueue_alert(signal_id, asset, direction, text, expiry_ts, now)

    def enqueue_alert(
        self, signal_id: str, asset: str, direction: str, text: str,
        expiry_ts: int, now: int, image: Optional[bytes] = None,
    ) -> List[Dict[str, Any]]:
        created: List[Dict[str, Any]] = []
        with self.store.transaction() as conn:
            for chat_id in self.config.telegram_allowed_chat_ids:
                existing = conn.execute(
                    "SELECT * FROM telegram_outbox WHERE signal_id = ? AND chat_id = ?",
                    (signal_id, chat_id),
                ).fetchone()
                if existing:
                    created.append(dict(existing))
                    continue
                outbox_id = make_stable_id("tgo", f"{signal_id}|{chat_id}")
                conn.execute(
                    """
                    INSERT INTO telegram_outbox(
                        outbox_id, signal_id, chat_id, asset, direction, payload_text,
                        status, attempts, max_attempts, expiry_ts, created_ts, created_utc, updated_ts, payload_image
                    ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outbox_id,
                        signal_id,
                        chat_id,
                        asset,
                        direction,
                        text,
                        int(self.config.telegram_max_retries),
                        int(expiry_ts),
                        now,
                        ts_to_iso(now),
                        now,
                        image,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM telegram_outbox WHERE outbox_id = ?", (outbox_id,)
                ).fetchone()
                if row:
                    created.append(dict(row))
        return created

    def process_outbox(self, now_ts: Optional[int] = None) -> Dict[str, Any]:
        """
        Deliver pending/retryable outbox items.
        - Never sends expired or stale alerts (marks EXPIRED).
        - Never resends SENT or UNCERTAIN alerts.
        - Never interrupts paper trading on Telegram outage.
        """
        now = now_ts if now_ts is not None else utc_now_ts()
        min_left = int(self.config.telegram_min_actionable_sec_left)
        bot_token = get_telegram_bot_token()

        with self.store.transaction() as conn:
            interrupted = conn.execute(
                "UPDATE telegram_outbox SET status = 'UNCERTAIN', "
                "last_error = 'Delivery was interrupted; it may have reached Telegram', updated_ts = ? "
                "WHERE status = 'SENDING' AND updated_ts <= ?",
                (now, now - max(120, int(self.config.telegram_request_timeout_sec) * 2)),
            ).rowcount
            rows = conn.execute(
                """
                SELECT * FROM telegram_outbox
                WHERE status IN ('PENDING', 'RETRY_WAIT')
                ORDER BY created_ts ASC
                """
            ).fetchall()

        sent = 0
        expired = 0
        uncertain = interrupted
        retried = 0
        failed = 0

        for row in rows:
            if now_ts is None:
                now = utc_now_ts()
            item = dict(row)
            outbox_id = str(item["outbox_id"])
            expiry_ts = int(item["expiry_ts"])
            attempts = int(item["attempts"])
            max_attempts = int(item["max_attempts"])

            notice = max(min_left, STUBAY_NOTICE_SECONDS) if (
                str(item["signal_id"]).startswith("stubay:") and "<b>Time zone</b>:" in item["payload_text"]
            ) else min_left
            if (expiry_ts - now) < notice:
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE telegram_outbox SET status = 'EXPIRED', last_error = 'Signal expired before delivery', updated_ts = ? WHERE outbox_id = ? AND status IN ('PENDING', 'RETRY_WAIT')",
                        (now, outbox_id),
                    )
                expired += 1
                self.store.log_event(
                    "Telegram",
                    "INFO",
                    f"Skipped expired Telegram outbox item {outbox_id} (signal {item['signal_id']})",
                    ts=now,
                )
                continue

            if item["chat_id"] not in self.config.telegram_allowed_chat_ids:
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE telegram_outbox SET status = 'FAILED', last_error = 'Chat ID not in configured allowlist', updated_ts = ? WHERE outbox_id = ?",
                        (now, outbox_id),
                    )
                failed += 1
                continue

            new_attempts = attempts + 1
            with self.store.transaction() as conn:
                claimed = conn.execute(
                    "UPDATE telegram_outbox SET status = 'SENDING', attempts = ?, updated_ts = ? "
                    "WHERE outbox_id = ? AND status IN ('PENDING', 'RETRY_WAIT') AND attempts = ?",
                    (new_attempts, now, outbox_id, attempts),
                ).rowcount
            if not claimed:
                continue
            try:
                transport_args = (
                    bot_token,
                    str(item["chat_id"]),
                    str(item["payload_text"]),
                    int(self.config.telegram_request_timeout_sec),
                )
                if item["payload_image"] and self.transport is default_telegram_http_transport:
                    res = default_telegram_http_transport(*transport_args, image=item["payload_image"])
                else:
                    res = self.transport(*transport_args)
                msg_id = str(res.get("message_id", ""))
                with self.store.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE telegram_outbox
                        SET status = 'SENT', attempts = ?, telegram_message_id = ?,
                            last_error = NULL, updated_ts = ?
                        WHERE outbox_id = ?
                        """,
                        (new_attempts, msg_id, now, outbox_id),
                    )
                sent += 1
                self.store.log_event(
                    "Telegram",
                    "INFO",
                    f"Delivered PAPER SIGNAL {item['signal_id']} to chat {item['chat_id']} (msg_id={msg_id})",
                    ts=now,
                )
            except TelegramUncertainTimeout as exc:
                err = redact_text(str(exc))
                # Delivery is uncertain — record UNCERTAIN and NEVER blindly resend!
                with self.store.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE telegram_outbox
                        SET status = 'UNCERTAIN', attempts = ?, last_error = ?, updated_ts = ?
                        WHERE outbox_id = ?
                        """,
                        (new_attempts, err, now, outbox_id),
                    )
                uncertain += 1
                self.store.log_event(
                    "Telegram",
                    "WARN",
                    f"Telegram delivery UNCERTAIN (timeout) for {item['signal_id']} — will NOT blindly resend.",
                    {"outbox_id": outbox_id, "error": err},
                    ts=now,
                )
            except TelegramTransientFailure as exc:
                err = redact_text(str(exc))
                next_status = "FAILED" if new_attempts >= max_attempts else "RETRY_WAIT"
                with self.store.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE telegram_outbox
                        SET status = ?, attempts = ?, last_error = ?, updated_ts = ?
                        WHERE outbox_id = ?
                        """,
                        (next_status, new_attempts, err, now, outbox_id),
                    )
                if next_status == "RETRY_WAIT":
                    retried += 1
                else:
                    failed += 1
                self.store.log_event(
                    "Telegram",
                    "WARN",
                    f"Telegram transient failure ({next_status}) for {item['signal_id']}: {err}",
                    ts=now,
                )
            except Exception as exc:
                err = redact_text(str(exc))
                with self.store.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE telegram_outbox
                        SET status = 'FAILED', attempts = ?, last_error = ?, updated_ts = ?
                        WHERE outbox_id = ?
                        """,
                        (new_attempts, err, now, outbox_id),
                    )
                failed += 1
                self.store.log_event(
                    "Telegram",
                    "ERROR",
                    f"Telegram permanent failure for {item['signal_id']}: {err}",
                    ts=now,
                )

        return {
            "ok": True,
            "sent": sent,
            "expired": expired,
            "uncertain": uncertain,
            "retried": retried,
            "failed": failed,
        }
