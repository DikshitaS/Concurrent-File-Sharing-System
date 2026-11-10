
import sqlite3
import hashlib
import secrets
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "metadata.db")

def create_database():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            owner TEXT NOT NULL,
            size INTEGER NOT NULL,
            upload_time TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        bytes.fromhex(salt),
        200000
    ).hex()

def register_user(username, password):
    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            (username, salt, password_hash)
        )

        conn.commit()
        conn.close()
        return True

    except sqlite3.IntegrityError:
        return False

def authenticate_user(username, password):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT salt, password_hash FROM users WHERE username=?",
        (username,)
    )

    result = cursor.fetchone()
    conn.close()

    if result is None:
        return False

    salt, stored_hash = result
    return secrets.compare_digest(
        hash_password(password, salt),
        stored_hash
    )

def add_file(filename, stored_name, owner, size):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO files
        (filename, stored_name, owner, size, upload_time)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(stored_name)
        DO UPDATE SET
            filename = excluded.filename,
            owner = excluded.owner,
            size = excluded.size,
            upload_time = excluded.upload_time
    """, (
        filename,
        stored_name,
        owner,
        size,
        datetime.now().isoformat(timespec="seconds")
    ))

    conn.commit()
    conn.close()

def get_file(filename):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT stored_name, size, owner FROM files WHERE filename=?",
        (filename,)
    )

    result = cursor.fetchone()
    conn.close()

    return result

def list_files():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT filename, owner, size, upload_time
        FROM files
        ORDER BY upload_time DESC
    """)

    result = cursor.fetchall()
    conn.close()

    return result
