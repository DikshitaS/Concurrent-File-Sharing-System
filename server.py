"""Multithreaded TCP file server with authentication and SHA-256 validation."""

import hashlib
import json
import os
import re
import socket
import threading
import uuid

import database

HOST = "0.0.0.0"
PORT = 5001
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MiB
CHUNK_SIZE = 64 * 1024
MAX_JSON_SIZE = 1024 * 1024
SOCKET_TIMEOUT = 300

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_DIR = os.path.join(BASE_DIR, "files")
os.makedirs(FILE_DIR, exist_ok=True)


def send_json(sock, data):
    message = json.dumps(data).encode("utf-8")
    sock.sendall(len(message).to_bytes(4, byteorder="big") + message)


def receive_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(min(CHUNK_SIZE, size - len(data)))
        if not chunk:
            raise ConnectionError("Connection closed while receiving data")
        data.extend(chunk)
    return bytes(data)


def receive_json(sock):
    header = receive_exact(sock, 4)
    length = int.from_bytes(header, byteorder="big")
    if length <= 0 or length > MAX_JSON_SIZE:
        raise ValueError("Invalid or oversized JSON message")
    payload = receive_exact(sock, length)
    result = json.loads(payload.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("JSON request must be an object")
    return result


def safe_filename(value):
    """Reduce a supplied path to a safe, non-empty filename."""
    if not isinstance(value, str):
        return None
    filename = value.replace("\\", "/").split("/")[-1].strip()
    if filename in ("", ".", "..") or "\x00" in filename:
        return None
    if len(filename) > 255:
        return None
    return filename


def calculate_file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def handle_upload(conn, username, request):
    filename = safe_filename(request.get("filename"))
    try:
        size = int(request.get("filesize", -1))
    except (TypeError, ValueError):
        size = -1
    expected_hash = request.get("sha256", "")

    if (
        filename is None
        or size < 0
        or size > MAX_FILE_SIZE
        or not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
    ):
        send_json(conn, {"status": "ERROR", "message": "Invalid file metadata"})
        return

    # Each transfer gets a unique server-side name and temporary path.
    extension = os.path.splitext(filename)[1][:20]
    stored_name = f"{uuid.uuid4().hex}{extension}"
    final_path = os.path.join(FILE_DIR, stored_name)
    temp_path = os.path.join(FILE_DIR, f".{uuid.uuid4().hex}.part")

    send_json(conn, {"status": "READY"})
    digest = hashlib.sha256()
    remaining = size

    try:
        with open(temp_path, "xb") as file:
            while remaining > 0:
                chunk = conn.recv(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Incomplete upload")
                file.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)

        actual_hash = digest.hexdigest()
        if actual_hash.lower() != expected_hash.lower():
            send_json(
                conn,
                {"status": "ERROR", "message": "SHA-256 verification failed; file rejected"},
            )
            return

        os.replace(temp_path, final_path)
        try:
            file_id = database.add_file(
                filename, stored_name, username, size, actual_hash
            )
        except Exception:
            if os.path.exists(final_path):
                os.remove(final_path)
            raise

        send_json(
            conn,
            {
                "status": "SUCCESS",
                "message": "Upload completed and SHA-256 verified",
                "file_id": file_id,
                "sha256": actual_hash,
            },
        )
        print(f"{username} uploaded {filename} ({size} bytes), file ID {file_id}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def handle_download(conn, request):
    file_record = database.get_file(request.get("file_id"))
    if file_record is None:
        send_json(conn, {"status": "ERROR", "message": "File ID not found"})
        return

    file_id, filename, stored_name, size, owner, stored_hash = file_record
    path = os.path.join(FILE_DIR, stored_name)

    if not os.path.isfile(path) or os.path.getsize(path) != size:
        send_json(conn, {"status": "ERROR", "message": "File unavailable or size mismatch"})
        return

    # Backfill checksums for files uploaded using the earlier database schema.
    if not stored_hash:
        stored_hash = calculate_file_sha256(path)
        database.update_file_checksum(file_id, stored_hash)

    send_json(
        conn,
        {
            "status": "READY",
            "file_id": file_id,
            "filename": filename,
            "filesize": size,
            "sha256": stored_hash,
        },
    )

    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            conn.sendall(chunk)

    print(f"File ID {file_id} ({filename}) downloaded by a client")


def authenticate_or_register(conn):
    request = receive_json(conn)
    action = request.get("action", "LOGIN").upper()
    username = request.get("username", "")
    password = request.get("password", "")

    if not isinstance(username, str) or not isinstance(password, str):
        send_json(conn, {"status": "ERROR", "message": "Invalid credentials format"})
        return None
    if not username.strip() or not password:
        send_json(conn, {"status": "ERROR", "message": "Username and password are required"})
        return None

    if action == "REGISTER":
        if database.register_user(username, password):
            send_json(conn, {"status": "SUCCESS", "message": "Registration successful"})
            print(f"New user registered: {username}")
            return username
        send_json(conn, {"status": "ERROR", "message": "Username already exists"})
        return None

    if action == "LOGIN":
        if database.authenticate_user(username, password):
            send_json(conn, {"status": "SUCCESS", "message": "Login successful"})
            print(f"{username} authenticated")
            return username
        send_json(conn, {"status": "ERROR", "message": "Invalid username or password"})
        return None

    send_json(conn, {"status": "ERROR", "message": "Unknown authentication action"})
    return None


def handle_client(conn, address):
    print(f"Client connected: {address}")
    conn.settimeout(SOCKET_TIMEOUT)

    try:
        username = authenticate_or_register(conn)
        if username is None:
            return

        while True:
            request = receive_json(conn)
            action = request.get("action", "").upper()

            if action == "UPLOAD":
                handle_upload(conn, username, request)

            elif action == "DOWNLOAD":
                handle_download(conn, request)

            elif action == "LIST":
                files = database.list_files()
                send_json(
                    conn,
                    {
                        "status": "SUCCESS",
                        "files": [
                            {
                                "id": row[0],
                                "filename": row[1],
                                "owner": row[2],
                                "size": row[3],
                                "upload_time": row[4],
                                "sha256": row[5],
                            }
                            for row in files
                        ],
                    },
                )

            elif action == "EXIT":
                break

            else:
                send_json(conn, {"status": "ERROR", "message": "Invalid request"})

    except (ConnectionError, OSError, socket.timeout) as error:
        print(f"Client disconnected or timed out ({address}): {error}")
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Invalid client request from {address}: {error}")
    except Exception as error:
        print(f"Client error from {address}: {error}")
    finally:
        conn.close()
        print(f"Connection closed: {address}")


def start_server():
    database.create_database()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(10)

    print("================================")
    print(" CONCURRENT FILE SHARING SERVER")
    print("================================")
    print(f"Server listening on {HOST}:{PORT}")
    print("Waiting for clients...")

    try:
        while True:
            conn, address = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\nServer shutting down")
    finally:
        server.close()


if __name__ == "__main__":
    start_server()
