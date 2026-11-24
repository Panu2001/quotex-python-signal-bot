"""
Authenticated Dashboard Session Manager backed by SQLite.
- Uses PBKDF2-HMAC-SHA256 password hashing with random per-user salt.
- Issues opaque random session tokens (stored as SHA-256 hashes in auth_sessions).
- Logging out revokes ONLY the user's interface session; the background paper-trading
  bot service remains online and continues collecting data and settling trades.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Any, Dict, Optional

from .storage import PaperLabStore, make_stable_id, ts_to_iso, utc_now_ts


PBKDF2_ITERATIONS = 120_000
DEFAULT_SESSION_TTL_SEC = 86400 * 7  # 7 days


def _hash_password(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iter_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthManager:
    def __init__(self, store: PaperLabStore):
        self.store = store
        self._ensure_default_owner()

    def _ensure_default_owner(self) -> None:
        """
        Ensure at least one owner account exists for single-machine local dashboard access.
        Credentials can be customized via PAPER_LAB_USERNAME / PAPER_LAB_PASSWORD env vars.
        """
        with self.store._lock:
            row = self.store._conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            if row and int(row["c"]) > 0:
                return
        default_user = os.environ.get("PAPER_LAB_USERNAME", "trader").strip() or "trader"
        default_pass = os.environ.get("PAPER_LAB_PASSWORD", "signaldesk-paper").strip() or "signaldesk-paper"
        self.create_user(default_user, default_pass, role="owner")

    def create_user(self, username: str, password: str, role: str = "owner") -> Dict[str, Any]:
        uname = username.strip().lower()
        if len(uname) < 3 or len(password) < 6:
            return {"ok": False, "error": "Username must be >= 3 chars and password >= 6 chars."}
        now = utc_now_ts()
        user_id = make_stable_id("usr", uname)
        pw_hash = _hash_password(password)
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO users(user_id, username, password_hash, role, created_ts, created_utc)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, uname, pw_hash, role, now, ts_to_iso(now)),
                )
            return {"ok": True, "user_id": user_id, "username": uname, "role": role}
        except Exception as exc:
            return {"ok": False, "error": f"Could not create user: {exc}"}

    def login(self, username: str, password: str, ttl_sec: int = DEFAULT_SESSION_TTL_SEC) -> Dict[str, Any]:
        uname = (username or "").strip().lower()
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT * FROM users WHERE username = ?", (uname,)
            ).fetchone()
        if not row or not _verify_password(password or "", str(row["password_hash"])):
            self.store.log_event("Auth", "WARN", f"Failed login attempt for username '{uname}'")
            return {"ok": False, "error": "Invalid username or password."}

        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        now = utc_now_ts()
        session_id = make_stable_id("ses")
        expires_ts = now + max(300, int(ttl_sec))
        with self.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions(session_id, user_id, token_hash, created_ts, expires_ts, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (session_id, str(row["user_id"]), token_hash, now, expires_ts),
            )
        self.store.log_event("Auth", "INFO", f"User '{uname}' signed in (session {session_id})")
        return {
            "ok": True,
            "session_token": token,
            "session_id": session_id,
            "user": {
                "user_id": str(row["user_id"]),
                "username": str(row["username"]),
                "role": str(row["role"]),
            },
            "expires_utc": ts_to_iso(expires_ts),
        }

    def verify_session(self, session_token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_token or not session_token.strip():
            return None
        token_hash = _hash_token(session_token.strip())
        now = utc_now_ts()
        with self.store._lock:
            row = self.store._conn.execute(
                """
                SELECT s.session_id, s.expires_ts, s.revoked, u.user_id, u.username, u.role
                FROM auth_sessions s
                JOIN users u ON u.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        if int(row["revoked"]) != 0 or int(row["expires_ts"]) <= now:
            return None
        return {
            "session_id": str(row["session_id"]),
            "user_id": str(row["user_id"]),
            "username": str(row["username"]),
            "role": str(row["role"]),
            "expires_utc": ts_to_iso(int(row["expires_ts"])),
        }

    def logout(self, session_token: Optional[str]) -> Dict[str, Any]:
        """Ends ONLY the UI session. Background paper-trading service stays online."""
        if not session_token or not session_token.strip():
            return {"ok": True, "logged_out": True}
        token_hash = _hash_token(session_token.strip())
        now = utc_now_ts()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE auth_sessions SET revoked = 1, revoked_ts = ? WHERE token_hash = ?",
                (now, token_hash),
            )
        self.store.log_event(
            "Auth", "INFO", "User logged out of dashboard interface (background bot continues running)."
        )
        return {"ok": True, "logged_out": True}

    def change_password(
        self, user_id: str, current_password: str, new_password: str
    ) -> Dict[str, Any]:
        if len(new_password or "") < 6:
            return {"ok": False, "error": "New password must be at least 6 characters."}
        with self.store._lock:
            row = self.store._conn.execute(
                "SELECT user_id, username, password_hash FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "Account not found."}
        if not _verify_password(current_password or "", str(row["password_hash"])):
            return {"ok": False, "error": "Current password is incorrect."}
        new_hash = _hash_password(new_password)
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (new_hash, user_id),
            )
        self.store.log_event("Auth", "INFO", f"Password updated for user '{row['username']}'.")
        return {"ok": True, "updated": True}

