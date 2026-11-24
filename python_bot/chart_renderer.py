"""
Pure-Python zero-dependency PNG Candlestick Chart Screenshot Renderer.

Generates a clean dark-obsidian (960x540) Quotex-style candlestick chart PNG
matching the exact Telegram signal card format:
  - Pure dark background (#07090F) with crisp hairline plot box & subtle grid
  - Top-left header: "{Asset Name} ({payout}%) | TF: M1"
  - Crisp emerald-green & coral-red candlesticks with centered wicks
  - Filled directional triangle marker above (PUT ▼) or below (CALL ▲) the latest candle
  - Right-side Y-axis price labels & bottom X-axis timestamps
  - Subtle bottom-left watermark

Uses only Python stdlib (zlib, struct) so it works everywhere (Linux, macOS,
Windows desktop app, standalone bot) without Pillow or matplotlib.
"""

from __future__ import annotations

import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Compact 5x7 crisp bitmap font (uppercase + lowercase + digits + punctuation)
_FONT_5X7: Dict[str, Tuple[str, ...]] = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "11110", "10001", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "a": ("00000", "00000", "01110", "00001", "01111", "10001", "01111"),
    "b": ("10000", "10000", "11110", "10001", "10001", "10001", "11110"),
    "c": ("00000", "00000", "01110", "10000", "10000", "10001", "01110"),
    "d": ("00001", "00001", "01111", "10001", "10001", "10001", "01111"),
    "e": ("00000", "00000", "01110", "10001", "11111", "10000", "01110"),
    "f": ("00110", "01001", "01000", "11100", "01000", "01000", "01000"),
    "g": ("00000", "01111", "10001", "10001", "01111", "00001", "01110"),
    "h": ("10000", "10000", "11110", "10001", "10001", "10001", "10001"),
    "i": ("00100", "00000", "01100", "00100", "00100", "00100", "01110"),
    "j": ("00010", "00000", "00110", "00010", "00010", "10010", "01100"),
    "k": ("10000", "10000", "10010", "10100", "11000", "10100", "10010"),
    "l": ("01100", "00100", "00100", "00100", "00100", "00100", "01110"),
    "m": ("00000", "00000", "11010", "10101", "10101", "10101", "10101"),
    "n": ("00000", "00000", "11110", "10001", "10001", "10001", "10001"),
    "o": ("00000", "00000", "01110", "10001", "10001", "10001", "01110"),
    "p": ("00000", "00000", "11110", "10001", "11110", "10000", "10000"),
    "q": ("00000", "00000", "01111", "10001", "01111", "00001", "00001"),
    "r": ("00000", "00000", "10110", "11001", "10000", "10000", "10000"),
    "s": ("00000", "00000", "01111", "10000", "01110", "00001", "11110"),
    "t": ("01000", "01000", "11100", "01000", "01000", "01001", "00110"),
    "u": ("00000", "00000", "10001", "10001", "10001", "10011", "01101"),
    "v": ("00000", "00000", "10001", "10001", "10001", "01010", "00100"),
    "w": ("00000", "00000", "10001", "10001", "10101", "10101", "01010"),
    "x": ("00000", "00000", "10001", "01010", "00100", "01010", "10001"),
    "y": ("00000", "00000", "10001", "10001", "01111", "00001", "01110"),
    "z": ("00000", "00000", "11111", "00010", "00100", "01000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
    ",": ("00000", "00000", "00000", "00000", "00100", "00100", "01000"),
    "$": ("00100", "01111", "10100", "01110", "00101", "11110", "00100"),
    "@": ("01110", "10001", "10111", "10101", "10110", "10000", "01110"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    "^": ("00100", "01010", "10001", "00000", "00000", "00000", "00000"),
    ">": ("10000", "01000", "00100", "00010", "00100", "01000", "10000"),
}


class _RGBCanvas:
    def __init__(self, width: int, height: int, bg: Tuple[int, int, int] = (7, 9, 15)) -> None:
        self.w = width
        self.h = height
        row = bytes(bg) * width
        self.buf = bytearray(row * height)

    def set_px(self, x: int, y: int, color: Tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            idx = (y * self.w + x) * 3
            self.buf[idx] = color[0]
            self.buf[idx + 1] = color[1]
            self.buf[idx + 2] = color[2]

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, color: Tuple[int, int, int]) -> None:
        x0 = max(0, min(self.w, x0))
        x1 = max(0, min(self.w, x1))
        y0 = max(0, min(self.h, y0))
        y1 = max(0, min(self.h, y1))
        if x1 <= x0 or y1 <= y0:
            return
        span = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * self.w + x0) * 3
            self.buf[start : start + len(span)] = span

    def draw_rect_outline(self, x0: int, y0: int, x1: int, y1: int, color: Tuple[int, int, int]) -> None:
        self.fill_rect(x0, y0, x1, y0 + 1, color)
        self.fill_rect(x0, y1 - 1, x1, y1, color)
        self.fill_rect(x0, y0, x0 + 1, y1, color)
        self.fill_rect(x1 - 1, y0, x1, y1, color)

    def draw_hline(
        self,
        x0: int,
        x1: int,
        y: int,
        color: Tuple[int, int, int],
        dashed: bool = False,
        dash_len: int = 4,
        gap_len: int = 4,
    ) -> None:
        if y < 0 or y >= self.h:
            return
        x0 = max(0, min(self.w - 1, x0))
        x1 = max(0, min(self.w - 1, x1))
        if x1 < x0:
            x0, x1 = x1, x0
        if not dashed:
            self.fill_rect(x0, y, x1 + 1, y + 1, color)
            return
        period = dash_len + gap_len
        for x in range(x0, x1 + 1):
            if ((x - x0) % period) < dash_len:
                self.set_px(x, y, color)

    def draw_vline(
        self,
        x: int,
        y0: int,
        y1: int,
        color: Tuple[int, int, int],
        dashed: bool = False,
    ) -> None:
        if x < 0 or x >= self.w:
            return
        y0 = max(0, min(self.h - 1, y0))
        y1 = max(0, min(self.h - 1, y1))
        if y1 < y0:
            y0, y1 = y1, y0
        if not dashed:
            self.fill_rect(x, y0, x + 1, y1 + 1, color)
            return
        for y in range(y0, y1 + 1):
            if ((y - y0) % 8) < 4:
                self.set_px(x, y, color)

    def draw_filled_triangle(
        self,
        cx: int,
        tip_y: int,
        half_base: int,
        height: int,
        direction: str,
        color: Tuple[int, int, int],
    ) -> None:
        """Draw a crisp solid triangle pointing UP ('CALL') or DOWN ('PUT')."""
        h = max(3, height)
        for row in range(h):
            frac = row / float(h - 1) if h > 1 else 1.0
            hw = int(round(half_base * frac))
            if direction == "DOWN":
                # tip at tip_y (bottom), base at tip_y - h + 1 (top)
                y = tip_y - row
            else:
                # tip at tip_y (top), base at tip_y + h - 1 (bottom)
                y = tip_y + row
            self.fill_rect(cx - hw, y, cx + hw + 1, y + 1, color)

    def draw_text(
        self,
        x: int,
        y: int,
        text: str,
        color: Tuple[int, int, int],
        scale: int = 1,
    ) -> int:
        cursor_x = x
        for ch in text:
            glyph = _FONT_5X7.get(ch) or _FONT_5X7.get(ch.upper()) or _FONT_5X7[" "]
            for row_i, row_bits in enumerate(glyph):
                for col_i, bit in enumerate(row_bits):
                    if bit == "1":
                        if scale == 1:
                            self.set_px(cursor_x + col_i, y + row_i, color)
                        else:
                            self.fill_rect(
                                cursor_x + col_i * scale,
                                y + row_i * scale,
                                cursor_x + (col_i + 1) * scale,
                                y + (row_i + 1) * scale,
                                color,
                            )
            cursor_x += 6 * scale
        return cursor_x

    def to_png_bytes(self) -> bytes:
        raw = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw.append(0)
            start = y * stride
            raw.extend(self.buf[start : start + stride])

        def chunk(chunk_type: bytes, data: bytes) -> bytes:
            crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        idat = zlib.compress(bytes(raw), level=6)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", idat)
            + chunk(b"IEND", b"")
        )


def format_tf_code(timeframe: str) -> str:
    raw = str(timeframe or "1m").strip().lower()
    m = re.match(r"^(\d+)\s*([smhd])$", raw)
    if m:
        val, unit = m.group(1), m.group(2).upper()
        return f"{unit}{val}"
    return raw.upper()


def _format_axis_time(ts: int, timeframe: str, tz_offset_min: Optional[int] = None) -> str:
    if tz_offset_min is not None:
        tz = timezone(timedelta(minutes=tz_offset_min))
        dt = datetime.fromtimestamp(ts, tz=tz)
    else:
        dt = datetime.fromtimestamp(ts)
    if timeframe in ("5s", "10s", "15s", "30s"):
        return dt.strftime("%H:%M:%S")
    if timeframe == "1d":
        return dt.strftime("%d %b")
    return dt.strftime("%H:%M")


def render_signal_chart_png(
    candles: List[Dict[str, Any]],
    overlays: Optional[Dict[str, Any]],
    symbol_label: str,
    timeframe: str,
    signal: str,
    confidence: float,
    price: float,
    decimals: int = 5,
    entry_time_str: str = "",
    expiry_time_str: str = "",
    expiry_label: str = "",
    feed_source: str = "",
    tz_offset_min: Optional[int] = None,
    payout: float = 85.0,
    watermark: str = "SignalDesk",
    width: int = 960,
    height: int = 540,
) -> bytes:
    """Render a crisp dark-black Quotex candlestick chart PNG matching the Telegram signal layout."""
    bg_col = (7, 9, 15)
    canvas = _RGBCanvas(width, height, bg=bg_col)

    border_col = (22, 27, 40)
    grid_col = (15, 19, 30)
    text_bright = (242, 246, 252)
    text_axis = (196, 206, 222)
    text_muted = (140, 154, 178)
    bull_col = (24, 178, 108)
    bear_col = (228, 82, 82)
    bull_wick = (24, 160, 98)
    bear_wick = (204, 74, 74)

    # Top-left clean header: "{Asset Name} ({payout}%) | TF: M1"
    payout_int = int(round(float(payout or 85)))
    tf_code = format_tf_code(expiry_label or timeframe)
    header_title = f"{symbol_label} ({payout_int}%) | TF: {tf_code}"
    canvas.draw_text(18, 14, header_title, text_bright, scale=2)

    # Full-height clean candlestick plot box
    pad_left = 14
    pad_right = 76
    plot_top = 40
    plot_bottom = height - 30
    plot_right = width - pad_right
    plot_w = plot_right - pad_left
    plot_h = plot_bottom - plot_top

    canvas.draw_rect_outline(pad_left, plot_top, plot_right, plot_bottom, border_col)

    # Slice last 85 candles for the crisp medium-density look in the screenshot
    visible = min(85, len(candles))
    start_idx = max(0, len(candles) - visible)
    c_slice = candles[start_idx:]

    if not c_slice:
        canvas.draw_text(width // 2 - 120, height // 2, "NO LIVE CANDLES AVAILABLE", text_muted, scale=2)
        return canvas.to_png_bytes()

    # Right breathing space (6 empty slots on the right like the screenshot)
    right_pad_slots = 6
    active_slots = max(len(c_slice), 60)
    total_slots = active_slots + right_pad_slots
    offset_slots = active_slots - len(c_slice)
    step_x = plot_w / float(total_slots)
    candle_w = max(3, min(9, int(step_x * 0.68)))

    def idx_to_x(i: int) -> int:
        return int(pad_left + (offset_slots + i) * step_x + step_x * 0.5)

    # Price bounds based purely on visible candles with comfortable vertical padding
    lo = min(float(c["low"]) for c in c_slice)
    hi = max(float(c["high"]) for c in c_slice)
    span = max(hi - lo, 1e-6)
    min_p = lo - span * 0.12
    max_p = hi + span * 0.12
    p_span = max(max_p - min_p, 1e-9)

    def price_to_y(p: float) -> int:
        ratio = (p - min_p) / p_span
        return int(plot_top + plot_h * (1.0 - ratio))

    # Subtle horizontal grid lines + right Y-axis labels (9 evenly spaced levels)
    fmt = f"{{:.{max(2, min(5, int(decimals or 5)))}f}}"
    num_y_ticks = 9
    for i in range(num_y_ticks):
        frac = i / float(num_y_ticks - 1)
        p_val = min_p + p_span * frac
        y = price_to_y(p_val)
        if plot_top < y < plot_bottom:
            canvas.draw_hline(pad_left + 1, plot_right - 1, y, grid_col, dashed=False)
        lbl_y = max(plot_top, min(plot_bottom - 7, y - 3))
        canvas.draw_text(plot_right + 8, lbl_y, fmt.format(p_val), text_axis, scale=1)

    # Subtle vertical grid lines + bottom X-axis timestamps (10 evenly spaced ticks)
    tick_count = min(10, max(2, len(c_slice)))
    seen_ticks = set()
    for t_i in range(tick_count):
        c_idx = int(round(t_i * (len(c_slice) - 1) / max(1, tick_count - 1)))
        if c_idx in seen_ticks:
            continue
        seen_ticks.add(c_idx)
        x = idx_to_x(c_idx)
        if pad_left < x < plot_right:
            canvas.draw_vline(x, plot_top + 1, plot_bottom - 1, grid_col, dashed=False)
        lbl = _format_axis_time(int(c_slice[c_idx]["time"]), timeframe, tz_offset_min)
        lbl_x = max(pad_left, min(plot_right - len(lbl) * 6, x - (len(lbl) * 3)))
        canvas.draw_text(lbl_x, plot_bottom + 9, lbl, text_axis, scale=1)

    # Candlesticks
    for i, c in enumerate(c_slice):
        cx = idx_to_x(i)
        o = float(c["open"])
        h = float(c["high"])
        l_val = float(c["low"])
        cl = float(c["close"])
        is_bull = cl >= o
        body_col = bull_col if is_bull else bear_col
        wick_col = bull_wick if is_bull else bear_wick

        yh = price_to_y(h)
        yl = price_to_y(l_val)
        yo = price_to_y(o)
        yc = price_to_y(cl)

        canvas.draw_vline(cx, yh, yl, wick_col)
        top = min(yo, yc)
        bot = max(yo, yc)
        if bot - top < 2:
            bot = top + 2
        half_w = max(1, candle_w // 2)
        canvas.fill_rect(cx - half_w, top, cx + half_w + 1, bot, body_col)

    # Crisp filled directional triangle marker right above (PUT ▼) or below (CALL ▲) the latest candle
    sig_upper = str(signal or "").upper()
    last_c = c_slice[-1]
    last_x = idx_to_x(len(c_slice) - 1)
    if sig_upper == "PUT":
        tip_y = max(plot_top + 14, price_to_y(float(last_c["high"])) - 12)
        canvas.draw_filled_triangle(
            cx=last_x,
            tip_y=tip_y,
            half_base=6,
            height=9,
            direction="DOWN",
            color=bear_col,
        )
    elif sig_upper == "CALL":
        tip_y = min(plot_bottom - 16, price_to_y(float(last_c["low"])) + 12)
        canvas.draw_filled_triangle(
            cx=last_x,
            tip_y=tip_y,
            half_base=6,
            height=9,
            direction="UP",
            color=bull_col,
        )

    # Subtle bottom-left watermark inside chart area
    if watermark:
        canvas.draw_text(pad_left + 8, plot_bottom - 14, watermark, text_muted, scale=1)

    return canvas.to_png_bytes()
