"""
Telegram Bot Signal Broadcaster for Signal Desk.
Uses Python stdlib urllib.request (JSON sendMessage + multipart/form-data sendPhoto)
so no external Telegram library is required.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


TF_SECONDS: Dict[str, int] = {
    "5s": 5,
    "10s": 10,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def parse_expiry_seconds(expiry: str, fallback_tf: str = "1m") -> int:
    raw = str(expiry or fallback_tf or "1m").strip().lower()
    m = re.match(r"^(\d+)\s*([smhd])$", raw)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit == "s":
            return max(5, val)
        if unit == "m":
            return max(60, val * 60)
        if unit == "h":
            return max(3600, val * 3600)
        if unit == "d":
            return max(86400, val * 86400)
    digits = re.sub(r"[^0-9]", "", raw)
    if digits:
        return max(60, int(digits) * 60)
    return 60


def format_tz_label(tz_offset_min: Optional[int]) -> str:
    if tz_offset_min is None:
        try:
            local_off = datetime.now().astimezone().utcoffset()
            if local_off is None:
                return "UTC"
            total_min = int(local_off.total_seconds() // 60)
        except Exception:
            return "UTC"
    else:
        total_min = int(tz_offset_min)
    sign = "+" if total_min >= 0 else "-"
    abs_min = abs(total_min)
    hh, mm = divmod(abs_min, 60)
    return f"UTC{sign}{hh:02d}:{mm:02d}"


def compute_aligned_entry_ts(
    now_ts: int,
    timeframe: str = "1m",
    align_to_candle: bool = True,
    lead_minutes: int = 0,
) -> int:
    """
    Compute the exact Quotex candle-aligned entry timestamp so Entry Time and
    Expiry Time always match Quotex's clock boundaries (:00 on 1m+ timeframes)
    and respect the user's configured future signal lead time (minutes before entry).
    """
    lead_min = max(0, min(60, int(lead_minutes or 0)))
    if not align_to_candle:
        return int(now_ts) + lead_min * 60

    tf_sec = TF_SECONDS.get(timeframe, 60)

    if lead_min > 0:
        target_ts = int(now_ts) + lead_min * 60
        if tf_sec >= 60:
            # Snap up to the next exact whole-minute (:00) Quotex entry boundary
            return ((target_ts + 59) // 60) * 60
        return ((target_ts + tf_sec - 1) // tf_sec) * tf_sec

    if tf_sec >= 60:
        bar_open = (now_ts // tf_sec) * tf_sec
        elapsed = now_ts - bar_open
        # If signal triggers within the first 4 seconds of a new candle open,
        # enter on this exact candle open; otherwise target the next exact candle open
        # so the trader gets clean preparation time and exact Quotex expiry alignment.
        if elapsed <= 4:
            return bar_open
        return bar_open + tf_sec

    # Sub-minute timeframes (5s/10s/15s/30s): snap to the next candle boundary >= 3s ahead
    lead = 3
    return ((now_ts + lead + tf_sec - 1) // tf_sec) * tf_sec


def compute_entry_and_expiry_times(
    expiry: str,
    timeframe: str = "1m",
    entry_ts: Optional[int] = None,
    tz_offset_min: Optional[int] = None,
    signal_ts: Optional[int] = None,
    align_to_candle: bool = True,
    lead_minutes: int = 0,
) -> Tuple[str, str, int]:
    """Return (entry_time_str, expiry_time_str, expiry_seconds) aligned to Quotex candle clock."""
    base_now = int(signal_ts) if signal_ts else int(time.time())
    if entry_ts:
        aligned_entry = int(entry_ts)
    else:
        aligned_entry = compute_aligned_entry_ts(
            base_now,
            timeframe=timeframe,
            align_to_candle=align_to_candle,
            lead_minutes=lead_minutes,
        )
    exp_sec = parse_expiry_seconds(expiry, timeframe)
    exp_ts = aligned_entry + exp_sec

    if tz_offset_min is not None:
        tz = timezone(timedelta(minutes=int(tz_offset_min)))
        entry_dt = datetime.fromtimestamp(aligned_entry, tz=tz)
        expiry_dt = datetime.fromtimestamp(exp_ts, tz=tz)
    else:
        entry_dt = datetime.fromtimestamp(aligned_entry)
        expiry_dt = datetime.fromtimestamp(exp_ts)

    fmt = "%Y-%m-%d %H:%M" if exp_sec >= 86400 else "%H:%M:%S"
    return entry_dt.strftime(fmt), expiry_dt.strftime(fmt), exp_sec


def format_clock_time(ts: int, tz_offset_min: Optional[int] = None) -> str:
    if tz_offset_min is not None:
        tz = timezone(timedelta(minutes=int(tz_offset_min)))
        dt = datetime.fromtimestamp(int(ts), tz=tz)
    else:
        dt = datetime.fromtimestamp(int(ts))
    return dt.strftime("%H:%M:%S")


def format_expiry_human(expiry: str, fallback_tf: str = "1m") -> str:
    raw = str(expiry or fallback_tf or "1m").strip().lower()
    m = re.match(r"^(\d+)\s*([smhd])$", raw)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit == "s":
            return f"{val} Second" if val == 1 else f"{val} Seconds"
        if unit == "m":
            return f"{val} Minute" if val == 1 else f"{val} Minutes"
        if unit == "h":
            return f"{val} Hour" if val == 1 else f"{val} Hours"
        if unit == "d":
            return f"{val} Day" if val == 1 else f"{val} Days"
    sec = parse_expiry_seconds(raw, fallback_tf)
    if sec < 60:
        return f"{sec} Seconds"
    mins = max(1, round(sec / 60))
    return f"{mins} Minute" if mins == 1 else f"{mins} Minutes"


def build_ai_analysis_summary(
    signal: str,
    candles: Optional[List[Dict[str, Any]]] = None,
    votes: Optional[List[Dict[str, Any]]] = None,
    reason_lines: Optional[List[str]] = None,
    confidence: float = 0.0,
) -> str:
    """Synthesize a crisp 1-2 sentence price-action & momentum analysis line."""
    sig_upper = str(signal or "WAIT").upper()

    # If caller supplied a natural-language rationale (e.g. from AI), prefer it cleanly
    if reason_lines:
        clean_lines = [str(r).strip() for r in reason_lines if str(r).strip()]
        if clean_lines and ":" not in clean_lines[0][:28]:
            return clean_lines[0][:240]

    # Analyze recent candle structure & momentum
    recent = (candles or [])[-12:]
    trend_phrase = ""
    momentum_phrase = ""

    if len(recent) >= 5:
        closes = [float(c["close"]) for c in recent]
        highs = [float(c["high"]) for c in recent]
        lows = [float(c["low"]) for c in recent]
        first_c, last_c = closes[0], closes[-1]
        bull_bars = sum(1 for c in recent[-5:] if float(c["close"]) >= float(c["open"]))
        bear_bars = 5 - bull_bars
        at_new_high = last_c >= max(highs[:-1]) * 0.9995
        at_new_low = last_c <= min(lows[:-1]) * 1.0005

        if sig_upper == "CALL":
            if last_c > first_c and bull_bars >= 3:
                trend_phrase = "The price action shows a clear ongoing uptrend"
                momentum_phrase = (
                    "with strong bullish momentum pushing to new highs."
                    if at_new_high
                    else "with steady buying pressure holding higher lows."
                )
            else:
                trend_phrase = "The price action shows a bullish reversal forming off support"
                momentum_phrase = "with buyers stepping in as bearish momentum fades."
        elif sig_upper == "PUT":
            if last_c < first_c and bear_bars >= 3:
                trend_phrase = "The price action shows a clear ongoing downtrend"
                momentum_phrase = (
                    "with strong bearish momentum pushing to new lows."
                    if at_new_low
                    else "with steady selling pressure rejecting higher levels."
                )
            else:
                trend_phrase = "The price action shows a bearish rejection near resistance"
                momentum_phrase = "with sellers taking control as upside momentum weakens."

    if trend_phrase and momentum_phrase:
        return f"{trend_phrase} {momentum_phrase}"

    # Fallback using top agreeing strategy votes
    agreeing = [
        v for v in (votes or []) if str(v.get("signal", "")).upper() == sig_upper
    ]
    if agreeing:
        top_names = [str(v.get("name", "")).strip() for v in agreeing[:2] if v.get("name")]
        top_reason = str(agreeing[0].get("reason", "")).strip()
        if sig_upper == "CALL":
            base = "Bullish multi-strategy confluence detected"
        elif sig_upper == "PUT":
            base = "Bearish multi-strategy confluence detected"
        else:
            base = "Market structure is currently consolidating"
        if top_names and top_reason:
            return f"{base} ({', '.join(top_names)} — {top_reason})."
        if top_reason:
            return f"{base}: {top_reason}."

    if sig_upper == "CALL":
        return "The price action shows bullish momentum with buyers driving price higher."
    if sig_upper == "PUT":
        return "The price action shows a clear downtrend with strong bearish momentum."
    return "Price action is currently ranging with mixed signals; wait for a cleaner directional break."


def format_signal_message(
    symbol: str,
    timeframe: str,
    signal: str,
    confidence: float,
    price: float,
    expiry: str,
    stake: float,
    payout: float,
    votes: List[Dict[str, Any]],
    entry_time: Optional[str] = None,
    expiry_time: Optional[str] = None,
    reason_lines: Optional[List[str]] = None,
    tz_offset_min: Optional[int] = None,
    signal_time: Optional[str] = None,
    lead_minutes: int = 0,
    candles: Optional[List[Dict[str, Any]]] = None,
    bot_title: str = "SIGNAL DESK",
) -> str:
    sig_upper = str(signal or "WAIT").upper()
    if sig_upper == "CALL":
        signal_display = "🟢 CALL"
    elif sig_upper == "PUT":
        signal_display = "🔴 PUT"
    else:
        signal_display = "⚪ WAIT"

    lead_min = max(0, min(60, int(lead_minutes or 0)))

    if not entry_time or not expiry_time:
        calc_entry, calc_expiry, _ = compute_entry_and_expiry_times(
            expiry=expiry,
            timeframe=timeframe,
            tz_offset_min=tz_offset_min,
            lead_minutes=lead_min,
        )
        entry_time = entry_time or calc_entry
        expiry_time = expiry_time or calc_expiry

    expiry_human = format_expiry_human(expiry, timeframe)
    ai_summary = build_ai_analysis_summary(
        signal=sig_upper,
        candles=candles,
        votes=votes,
        reason_lines=reason_lines,
        confidence=confidence,
    )

    divider = "━━━━━━━━━━━━━━━━━━━━"
    title_clean = html.escape(str(bot_title or "SIGNAL DESK").strip())

    caption = (
        f"🔹 <b>{title_clean}</b>\n"
        f"{divider}\n"
        f"📊 Asset: {html.escape(str(symbol))}\n"
        f"🕒 Entry Time: {html.escape(str(entry_time))}\n"
        f"⏳ Expiry: {html.escape(expiry_human)}\n"
        f"🎯 Signal: {signal_display}\n"
        f"🧠 AI Analysis: {html.escape(ai_summary)}\n"
        f"{divider}"
    )

    if len(caption) > 1000:
        caption = caption[:997] + "..."
    return caption


def detect_telegram_chat_id(bot_token: str) -> Dict[str, Any]:
    """
    Validate a Telegram Bot Token via getMe and auto-detect the most recent
    Chat ID (private chat, group, or channel) via getUpdates.
    """
    token = str(bot_token or "").strip()
    if not token or ":" not in token:
        return {"ok": False, "error": "Enter a valid Telegram Bot Token from @BotFather."}

    # 1. Validate bot token via getMe
    bot_username = ""
    bot_name = ""
    try:
        me_url = f"https://api.telegram.org/bot{token}/getMe"
        me_req = urllib.request.Request(me_url, method="GET")
        with urllib.request.urlopen(me_req, timeout=8.0) as resp:
            me_data = json.loads(resp.read().decode("utf-8"))
        if not me_data.get("ok"):
            return {"ok": False, "error": str(me_data.get("description") or "Invalid Bot Token.")}
        bot_info = me_data.get("result") or {}
        bot_username = str(bot_info.get("username") or "")
        bot_name = str(bot_info.get("first_name") or "")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code in (401, 404):
            return {"ok": False, "error": "Invalid Bot Token (Telegram rejected credentials)."}
        return {"ok": False, "error": f"Telegram HTTP {e.code}: {body[:160]}"}
    except Exception as exc:
        return {"ok": False, "error": f"Failed to reach Telegram API: {exc}"}

    # 2. Fetch recent updates to extract chat IDs
    chats: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        upd_url = f"https://api.telegram.org/bot{token}/getUpdates?limit=30"
        upd_req = urllib.request.Request(upd_url, method="GET")
        with urllib.request.urlopen(upd_req, timeout=8.0) as resp:
            upd_data = json.loads(resp.read().decode("utf-8"))
        updates = upd_data.get("result") if isinstance(upd_data, dict) else []
        if isinstance(updates, list):
            for upd in reversed(updates):
                if not isinstance(upd, dict):
                    continue
                chat_obj = None
                for key in (
                    "message",
                    "edited_message",
                    "channel_post",
                    "edited_channel_post",
                    "my_chat_member",
                    "chat_member",
                ):
                    container = upd.get(key)
                    if isinstance(container, dict) and isinstance(container.get("chat"), dict):
                        chat_obj = container["chat"]
                        break
                if chat_obj is None:
                    cb = upd.get("callback_query")
                    if isinstance(cb, dict) and isinstance(cb.get("message"), dict):
                        msg_chat = cb["message"].get("chat")
                        if isinstance(msg_chat, dict):
                            chat_obj = msg_chat

                if not isinstance(chat_obj, dict) or chat_obj.get("id") is None:
                    continue

                cid = str(chat_obj["id"])
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)

                ctype = str(chat_obj.get("type") or "private")
                title = str(
                    chat_obj.get("title")
                    or " ".join(
                        p for p in [chat_obj.get("first_name"), chat_obj.get("last_name")] if p
                    )
                    or chat_obj.get("username")
                    or cid
                )
                chats.append({
                    "id": cid,
                    "title": title,
                    "type": ctype,
                    "username": str(chat_obj.get("username") or ""),
                })
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code == 409:
            return {
                "ok": True,
                "bot_username": bot_username,
                "bot_name": bot_name,
                "chat_id": "",
                "chats": [],
                "message": (
                    f"Bot @{bot_username or bot_name} verified, but a webhook is active on this bot so getUpdates cannot read Chat IDs automatically."
                ),
            }
        return {
            "ok": True,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "chat_id": "",
            "chats": [],
            "message": f"Bot verified, but getUpdates failed ({e.code}): {body[:120]}",
        }
    except Exception as exc:
        return {
            "ok": True,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "chat_id": "",
            "chats": [],
            "message": f"Bot verified, could not fetch recent updates: {exc}",
        }

    if chats:
        primary = chats[0]
        return {
            "ok": True,
            "bot_username": bot_username,
            "bot_name": bot_name,
            "chat_id": primary["id"],
            "chats": chats[:8],
            "message": f"Connected to @{bot_username or bot_name} · Auto-filled Chat ID {primary['id']} ({primary['title']}).",
        }

    bot_handle = f"@{bot_username}" if bot_username else (bot_name or "your bot")
    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_name": bot_name,
        "chat_id": "",
        "chats": [],
        "message": (
            f"Bot {bot_handle} verified! Send /start to {bot_handle} in Telegram (or post in your channel), then click Detect Chat ID."
        ),
    }


def send_telegram_message(bot_token: str, chat_id: str, html_text: str) -> Dict[str, Any]:
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Missing bot_token or chat_id"}

    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id.strip(),
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return {"ok": False, "error": f"Telegram HTTP {e.code}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    image_bytes: bytes,
    caption_html: str,
    filename: str = "signal_chart.png",
    content_type: str = "image/png",
    *,
    timeout: float = 15.0,
    raise_errors: bool = False,
) -> Dict[str, Any]:
    """Send a chart screenshot + formatted HTML caption to Telegram via sendPhoto."""
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Missing bot_token or chat_id"}
    if not image_bytes:
        return send_telegram_message(bot_token, chat_id, caption_html)

    boundary = f"----SignalDeskBoundary{uuid.uuid4().hex}"
    fields = [
        ("chat_id", chat_id.strip()),
        ("parse_mode", "HTML"),
        ("caption", caption_html[:1020]),
    ]

    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(image_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if raise_errors:
            raise
        err_body = e.read().decode("utf-8", errors="ignore")
        return {"ok": False, "error": f"Telegram HTTP {e.code}: {err_body}"}
    except Exception as exc:
        if raise_errors:
            raise
        return {"ok": False, "error": str(exc)}
