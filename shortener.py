"""
shortener.py — candidate implementation file

Complete the URLShortener service used by app.py.
You may add helper classes, methods, and constants, but preserve the public
method names used by app.py and the tests.
"""

import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
import os
import secrets
from urllib.parse import urlsplit
from datetime import timezone, datetime, timedelta
import time
from db import get_db

AUTO_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{6,10}$")
CUSTOM_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MAX_GENERATION_ATTEMPTS = 5
BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


class ShortenerError(Exception):
    pass


class ValidationError(ShortenerError):
    pass


class NotFoundError(ShortenerError):
    pass


class ConflictError(ShortenerError):
    pass


class ExpiredLinkError(ShortenerError):
    pass


class PermissionDeniedError(ShortenerError):
    pass


class CodeGenerationError(ShortenerError):
    pass


@dataclass(frozen=True)
class Link:
    short_code: str
    long_url: str
    created_at: float
    expires_at: Optional[float]
    hit_count: int
    owner_token: str

    @property
    def is_expired(self) -> bool:
        return (
            self.expires_at is not None
            and self.expires_at <= datetime.now(timezone.utc).timestamp()
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_code": self.short_code,
            "long_url": self.long_url,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "hit_count": self.hit_count,
            # "owner_token": "tok_secret",
            "is_expired": self.is_expired,
        }


class URLShortener:
    """
    SQLite-backed short link service.

    Required public methods:
      create(long_url, custom_code=None, ttl_seconds=None) -> Link
      get(short_code) -> Link | None
      resolve_redirect(short_code) -> str
      increment_hits(short_code) -> None
      delete(short_code, owner_token) -> bool
      get_stats() -> dict
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _to_base62(self, num: int) -> str:
        """Encode an integer to Base62 string"""
        if num == 0:
            return BASE62_ALPHABET[0]
        chars = []
        while num > 0:
            num, rem = divmod(num, 62)
            chars.append(BASE62_ALPHABET[rem])
        return "".join(reversed(chars))

    def _init_db(self) -> None:
        """Create the database schema needed by the service."""
        conn: sqlite3.Connection = self._get_conn()
        QUERY = """
            CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_code TEXT NOT NULL UNIQUE COLLATE BINARY,
            original_url TEXT NOT NULL,
            owner_token TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            expires_at TEXT DEFAULT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0
            );
                """
        with self._lock:
            conn.executescript(QUERY)

    def _generate_code(self, long_url: str) -> str:
        """
        Generates a cryptographically random Base62 string matching ^[A-Za-z0-9]{6,10}$.
        """
        random_bytes = os.urandom(6)
        num = int.from_bytes(random_bytes, byteorder="big")
        code = self._to_base62(num)

        if len(code) < 6:
            code = code.zfill(6)
        elif len(code) > 10:
            code = code[:10]

        return code

    def _generate_owner_token(self) -> str:
        """Return a secret token used to authorize deletion."""
        return secrets.token_urlsafe(16)

    def _is_valid_url(self, long_url: str) -> bool:
        if not isinstance(long_url, str):
            return False
        try:
            parsed = urlsplit(long_url.strip())
            return parsed.scheme in ("http", "https") and parsed.netloc
        except ValueError:
            return False

    def create(
        self,
        long_url: str,
        custom_code: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Link:
        if not isinstance(long_url, str) or not self._is_valid_url(long_url):
            raise ValidationError("Invalid URL format or host")

        if custom_code is not None:
            if not isinstance(custom_code, str) or not CUSTOM_CODE_PATTERN.fullmatch(
                custom_code
            ):
                raise ValidationError("Invalid custom code format")

        if ttl_seconds is not None:
            if (
                isinstance(ttl_seconds, bool)
                or not isinstance(ttl_seconds, (int, float))
                or ttl_seconds <= 0
            ):
                raise ValidationError(
                    "ttl_seconds must be a positive non-boolean number"
                )

        tok = "tok_" + self._generate_owner_token()
        created_at = time.time()

        expires_at_ts = None
        expires_at_iso = None

        if ttl_seconds is not None:
            expires_at_dt = datetime.now(timezone.utc) + timedelta(
                seconds=float(ttl_seconds)
            )
            expires_at_ts = expires_at_dt.timestamp()
            expires_at_iso = expires_at_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        if custom_code is not None:
            code = custom_code
            conn = self._get_conn()
            try:
                with self._lock:
                    conn.execute(
                        """
                        INSERT INTO links (short_code, original_url, expires_at, owner_token)
                        VALUES (?, ?, ?, ?)
                        """,
                        (code, long_url, expires_at_iso, tok),
                    )
                    conn.commit()
            except sqlite3.IntegrityError:
                raise ConflictError(f"Custom code '{custom_code}' already exists")
        else:
            inserted = False
            conn = self._get_conn()

            for attempt in range(MAX_GENERATION_ATTEMPTS):
                code = self._generate_code(long_url)
                if not AUTO_CODE_PATTERN.fullmatch(code):
                    raise CodeGenerationError(
                        "Invalid custom code format generated/overriden"
                    )
                try:
                    with self._lock:
                        conn.execute(
                            """
                            INSERT INTO links (short_code, original_url, expires_at, owner_token)
                            VALUES (?, ?, ?, ?)
                            """,
                            (code, long_url, expires_at_iso, tok),
                        )
                        conn.commit()
                    inserted = True
                    break
                except sqlite3.IntegrityError:
                    continue

            if not inserted:
                raise CodeGenerationError(
                    "Repeated code collisions during automatic code generation"
                )

        return Link(
            short_code=code,
            long_url=long_url,
            created_at=created_at,
            expires_at=expires_at_ts,
            hit_count=0,
            owner_token=tok,
        )

    def get(self, short_code: str) -> Optional[Link]:
        """Return the link for short_code, or None when it does not exist."""
        conn: sqlite3.Connection = self._get_conn()
        with self._lock:
            cursor = conn.execute(
                """
                SELECT short_code, original_url, owner_token, created_at, expires_at, hit_count
                FROM links WHERE short_code = ?
                
                """,
                #     # AND (expires_at IS NULL OR strftime('%Y-%m-%dT%H:%M:%fZ','now') < expires_at)
                (short_code,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            code, url, tok, cr, exp, hit_count = row

            # conn.execute(
            #     """
            #     UPDATE links
            #     SET hit_count = hit_count + 1
            #     WHERE short_code = ?
            #     """,
            #     (short_code,)
            # )
            # conn.commit()
        crts = datetime.fromisoformat(cr.replace("Z", "+00:00")).timestamp()
        exts = None
        if exp is not None:
            exts = datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
        return Link(
            short_code=code,
            long_url=url,
            created_at=crts,
            expires_at=exts,
            hit_count=hit_count,
            owner_token=tok,
        )

    def resolve_redirect(self, short_code: str) -> str:
        """
        Return the long URL for a redirect and atomically count the hit.

        Raise NotFoundError if the code does not exist.
        Raise ExpiredLinkError if the link is expired.
        """
        conn: sqlite3.Connection = self._get_conn()
        with self._lock:
            cur1 = conn.execute(
                """
                    SELECT original_url, expires_at FROM links WHERE short_code = ?
                """,
                (short_code,),
            )
            row = cur1.fetchone()
            if row is None:
                raise NotFoundError(short_code)

            org, exp = row
            if exp is not None:
                expdt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if expdt < datetime.now(timezone.utc):
                    raise ExpiredLinkError(short_code)

            conn.execute(
                """
                    UPDATE links
                    SET hit_count = hit_count + 1
                        WHERE short_code = ?
                """,
                (short_code,),
            )
            conn.commit()
        return org

    def increment_hits(self, short_code: str) -> None:
        """
        Atomically increment hit_count for an existing short code.

        Raise NotFoundError if the code does not exist.
        """
        conn: sqlite3.Connection = self._get_conn()
        with self._lock:
            cur1 = conn.execute(
                """
                    SELECT short_code FROM links WHERE short_code = ?
                """,
                (short_code,),
            )
            if cur1.fetchone() is None:
                raise NotFoundError(short_code)

            conn.execute(
                """
                UPDATE links
                SET hit_count = hit_count + 1
                WHERE short_code = ?
                """,
                (short_code,),
            )
            conn.commit()

    def delete(self, short_code: str, owner_token: str) -> bool:
        """
        Delete a link if owner_token matches.

        Return True when deleted, False when the code does not exist.
        Raise ValidationError when owner_token is missing or empty.
        Raise PermissionDeniedError when the token is wrong.
        """
        if not isinstance(owner_token, str) or len(owner_token) == 0:
            raise ValidationError(owner_token)
        conn: sqlite3.Connection = self._get_conn()

        with self._lock:
            cur1 = conn.execute(
                """
                SELECT owner_token FROM links WHERE short_code = ?
                """,
                (short_code,),
            )
            row = cur1.fetchone()
            if row is None:
                return False
            if row[0] != owner_token:
                raise PermissionDeniedError(owner_token)

            conn.execute(
                """
                DELETE FROM links WHERE short_code = ?
                """,
                (short_code,),
            )
        return True

    def get_stats(self) -> Dict[str, Any]:
        """Return total_links, total_hits, and the top five links by hit count."""
        conn: sqlite3.Connection = self._get_conn()
        with self._lock:
            cur1 = conn.execute(
                """
                    SELECT
                        COUNT(*) OVER() as total_links,
                        SUM(hit_count) OVER() as total_hits,
                        short_code, original_url, hit_count
                    FROM links
                    ORDER BY hit_count DESC, short_code ASC
                    LIMIT 5
                """
            )
            rows = cur1.fetchall()
        if not rows:
            return {"total_links": 0, "total_hits": 0, "top_5_links": []}
        tl = rows[0][0]
        th = rows[0][1] or 0

        top_5_links = [
            {"short_code": row[2], "long_url": row[3], "hit_count": row[4]}
            for row in rows
        ]

        return {
            "total_links": tl,
            "total_hits": th,
            "top_5_links": top_5_links,
        }

    def _get_conn(self) -> sqlite3.Connection:
        return get_db(self._db_path)
