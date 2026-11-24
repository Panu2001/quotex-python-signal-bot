"""Native Messaging host. Accepts market fields only."""
import json
import math
import os
import re
import struct
import sys
import time
from pathlib import Path

HEADER = struct.Struct("<I")
FIELDS = {"price": (0, 1e12), "change_24h": (-100, 1e6), "payout_1m": (0, 100), "payout_5m": (0, 100)}
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{1,31}")
CAPTURE = re.compile(r"[0-9a-f-]{36}")


def handle(message, path):
    if not isinstance(message, dict) or not CAPTURE.fullmatch(str(message.get("captureId", ""))):
        return {"message": "Invalid capture session."}
    capture = message["captureId"]
    if message.get("type") == "stop":
        if path.exists() and json.loads(path.read_text(encoding="utf8")).get("capture") == capture:
            path.unlink()
        return {"message": "Sharing stopped."}
    if message.get("type") != "quotes" or not isinstance(message.get("rows"), list):
        return {"message": "Invalid market data."}
    quotes = {}
    for row in message["rows"][:500]:
        if not isinstance(row, dict) or not SYMBOL.fullmatch(str(row.get("symbol", ""))):
            continue
        clean = {}
        for key, (low, high) in FIELDS.items():
            value = row.get(key)
            if type(value) in (int, float) and low <= value <= high and math.isfinite(value):
                if key != "price" or value > 0:
                    clean[key] = value
        if clean:
            quotes[row["symbol"]] = clean
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"market-bridge-{os.getpid()}.tmp")
    temp.write_text(json.dumps({"capture": capture, "received": int(time.time()), "quotes": quotes}), encoding="utf8")
    temp.replace(path)
    return {"message": f"Sharing {len(quotes)} market rows." if quotes else "No matching market rows. Open the asset list and check the row selector."}


def main():
    path = Path(os.environ["LOCALAPPDATA"]) / "SignalDesk Data" / "market-bridge.json"
    while True:
        header = sys.stdin.buffer.read(HEADER.size)
        if len(header) != HEADER.size:
            return
        size = HEADER.unpack(header)[0]
        if not 0 < size <= 256_000:
            return
        payload = sys.stdin.buffer.read(size)
        if len(payload) != size:
            return
        try:
            response = handle(json.loads(payload), path)
        except (ValueError, OSError, TypeError):
            response = {"message": "Could not update the local market bridge."}
        body = json.dumps(response).encode("utf8")
        sys.stdout.buffer.write(HEADER.pack(len(body)) + body)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
