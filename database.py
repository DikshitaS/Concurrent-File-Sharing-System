"""SQLite persistence layer for the concurrent file-sharing server."""

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "metadata.db")


def get_connection():
    """Return a thread-safe SQLite connection with a busy timeout."""
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def database_connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_database():
    """Create tables and migrate older project databases if necessary."""
    with database_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                salt TEXT NOT NULL,
                password_hash TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                stored_name TEXT NOT NULL UNIQUE,
                owner TEXT NOT NULL,
                size INTEGER NOT NULL CHECK(size >= 0),
                upload_time TEXT NOT NULL,
                sha256 TEXT NOT NULL DEFAULT ''
            )
            """
        )

        # Existing versions of the project did not have a checksum column.
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(files)").fetchall()
        }
        if "sha256" not in columns:
            conn.execute(
                "ALTER TABLE files ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_filename ON files(filename)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner)"
        )


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        200_000,
    ).hex()


def register_user(username, password):
    username = (username or "").strip()
    if not username or not password:
        return False

    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)

    try:
        with database_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                (username, salt, password_hash),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def authenticate_user(username, password):
    with database_connection() as conn:
        result = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if result is None:
        return False

    salt, stored_hash = result
    return secrets.compare_digest(hash_password(password, salt), stored_hash)


def add_file(filename, stored_name, owner, size, sha256):
    """Insert one uploaded file record; stored_name is unique per upload."""
    with database_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO files
                (filename, stored_name, owner, size, upload_time, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                stored_name,
                owner,
                size,
                datetime.now().isoformat(timespec="seconds"),
                sha256,
            ),
        )
        return cursor.lastrowid


def get_file(file_id):
    """Look up a file by its unique database ID."""
    try:
        numeric_id = int(file_id)
    except (TypeError, ValueError):
        return None

    with database_connection() as conn:
        return conn.execute(
            """
            SELECT id, filename, stored_name, size, owner, sha256
            FROM files WHERE id = ?
            """,
            (numeric_id,),
        ).fetchone()


def update_file_checksum(file_id, sha256):
    with database_connection() as conn:
        conn.execute(
            "UPDATE files SET sha256 = ? WHERE id = ?",
            (sha256, file_id),
        )


def list_files():
    """Return file records, newest first, including IDs and checksums."""
    with database_connection() as conn:
        return conn.execute(
            """
            SELECT id, filename, owner, size, upload_time, sha256
            FROM files
            ORDER BY upload_time DESC, id DESC
            """
        ).fetchall()
