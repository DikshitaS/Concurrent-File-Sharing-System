"""Interactive TCP client for the concurrent file-sharing application."""

import csv
import hashlib
import json
import os
import socket
import time
from datetime import datetime

# Mininet mapping: h1 is the server, h2 and h3 are clients.
SERVER_HOST = "10.0.0.2"
SERVER_PORT = 5001
CHUNK_SIZE = 64 * 1024
MAX_JSON_SIZE = 1024 * 1024
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERFORMANCE_CSV = os.path.join(BASE_DIR, "performance.csv")


def send_json(sock, data):
    message = json.dumps(data).encode("utf-8")
    sock.sendall(len(message).to_bytes(4, byteorder="big") + message)


def receive_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(min(CHUNK_SIZE, size - len(data)))
        if not chunk:
            raise ConnectionError("Server disconnected")
        data.extend(chunk)
    return bytes(data)


def receive_json(sock):
    header = receive_exact(sock, 4)
    length = int.from_bytes(header, byteorder="big")
    if length <= 0 or length > MAX_JSON_SIZE:
        raise ValueError("Invalid or oversized server message")
    response = json.loads(receive_exact(sock, length).decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("Invalid server response")
    return response


def calculate_sha256(filename):
    digest = hashlib.sha256()
    with open(filename, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_performance(operation, username, file_id, filename, size, duration, speed, verified):
    """Append one successful transfer measurement to performance.csv."""
    file_exists = os.path.exists(PERFORMANCE_CSV)
    with open(PERFORMANCE_CSV, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        if not file_exists or os.path.getsize(PERFORMANCE_CSV) == 0:
            writer.writerow(
                [
                    "Timestamp",
                    "Username",
                    "Operation",
                    "File_ID",
                    "Filename",
                    "Size_Bytes",
                    "Duration_Seconds",
                    "Throughput_MBps",
                    "SHA256_Verified",
                ]
            )
        writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                username,
                operation,
                file_id,
                filename,
                size,
                round(duration, 4),
                round(speed, 4),
                verified,
            ]
        )


def connect_server(username, password, auth_action="LOGIN"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((SERVER_HOST, SERVER_PORT))
        send_json(
            sock,
            {
                "action": auth_action,
                "username": username,
                "password": password,
            },
        )
        response = receive_json(sock)
        if response.get("status") != "SUCCESS":
            print("Authentication failed:", response.get("message", "Unknown error"))
            sock.close()
            return None
        print(response.get("message", "Connected successfully"))
        return sock
    except Exception:
        sock.close()
        raise


def upload_file(sock, username, filename):
    if not os.path.isfile(filename):
        print("Local file not found")
        return

    name = os.path.basename(filename)
    size = os.path.getsize(filename)
    checksum = calculate_sha256(filename)

    send_json(
        sock,
        {
            "action": "UPLOAD",
            "filename": name,
            "filesize": size,
            "sha256": checksum,
        },
    )

    response = receive_json(sock)
    if response.get("status") != "READY":
        print("Upload rejected:", response.get("message", "Unknown error"))
        return

    start = time.perf_counter()
    with open(filename, "rb") as file:
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            sock.sendall(chunk)

    response = receive_json(sock)
    duration = time.perf_counter() - start

    if response.get("status") == "SUCCESS":
        speed = (size / (1024 * 1024)) / duration if duration > 0 else 0
        verified = response.get("sha256", "").lower() == checksum.lower()
        print("Upload successful!")
        print("File ID:", response.get("file_id"))
        print("SHA-256 verified:", "YES" if verified else "NO")
        print(f"File size: {size} bytes")
        print(f"Transfer time: {duration:.4f} seconds")
        print(f"Transfer speed: {speed:.2f} MB/s")
        save_performance(
            "UPLOAD", username, response.get("file_id"), name,
            size, duration, speed, verified
        )
    else:
        print("Upload failed:", response.get("message", "Unknown error"))


def download_file(sock, username, file_id):
    send_json(sock, {"action": "DOWNLOAD", "file_id": file_id})
    response = receive_json(sock)

    if response.get("status") != "READY":
        print("Download failed:", response.get("message", "Unknown error"))
        return

    size = int(response["filesize"])
    expected_hash = response["sha256"]
    original_name = os.path.basename(response["filename"])
    safe_user = "".join(ch for ch in username if ch.isalnum() or ch in "-_") or "user"
    local_name = f"downloaded_{safe_user}_{response['file_id']}_{original_name}"
    digest = hashlib.sha256()
    start = time.perf_counter()
    remaining = size

    try:
        with open(local_name, "wb") as file:
            while remaining > 0:
                chunk = sock.recv(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Incomplete download")
                file.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
    except Exception:
        if os.path.exists(local_name):
            os.remove(local_name)
        raise

    duration = time.perf_counter() - start
    actual_hash = digest.hexdigest()
    verified = actual_hash.lower() == expected_hash.lower()
    speed = (size / (1024 * 1024)) / duration if duration > 0 else 0

    if not verified:
        os.remove(local_name)
        print("Download rejected: SHA-256 mismatch. The incomplete/corrupt copy was removed.")
        return

    print("Download successful!")
    print("Saved as:", local_name)
    print("SHA-256 verified: YES")
    print(f"File size: {size} bytes")
    print(f"Transfer time: {duration:.4f} seconds")
    print(f"Transfer speed: {speed:.2f} MB/s")
    save_performance(
        "DOWNLOAD", username, response.get("file_id"), original_name,
        size, duration, speed, verified
    )


def list_files(sock):
    send_json(sock, {"action": "LIST"})
    response = receive_json(sock)

    if response.get("status") != "SUCCESS":
        print("Unable to retrieve files")
        return

    print("\nAVAILABLE FILES")
    print("-" * 90)
    if not response.get("files"):
        print("No files available")
        return

    for file_info in response["files"]:
        size_mb = file_info["size"] / (1024 * 1024)
        print(f"File ID: {file_info['id']}")
        print(f"Filename: {file_info['filename']}")
        print(f"Owner: {file_info['owner']}")
        print(f"Size: {size_mb:.2f} MB")
        print(f"Uploaded: {file_info['upload_time']}")
        print("-" * 90)


def main():
    print("================================")
    print("     FILE SHARING CLIENT")
    print("================================")
    print("1. Login")
    print("2. Register a new account")
    auth_choice = input("Choose an option: ").strip()
    if auth_choice not in ("1", "2"):
        print("Invalid option")
        return

    username = input("Username: ").strip()
    password = input("Password: ")
    auth_action = "LOGIN" if auth_choice == "1" else "REGISTER"
    sock = None

    try:
        sock = connect_server(username, password, auth_action)
        if sock is None:
            return

        while True:
            print("\n1. Upload File")
            print("2. Download File (use File ID from List Files)")
            print("3. List Files")
            print("4. Exit")
            choice = input("Enter your choice: ").strip()

            if choice == "1":
                filename = input("Enter local file path: ").strip().strip('"')
                upload_file(sock, username, filename)
            elif choice == "2":
                file_id = input("Enter File ID: ").strip()
                download_file(sock, username, file_id)
            elif choice == "3":
                list_files(sock)
            elif choice == "4":
                try:
                    send_json(sock, {"action": "EXIT"})
                except OSError:
                    pass
                print("Exiting...")
                break
            else:
                print("Invalid choice")

    except Exception as error:
        print("Error:", error)
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    main()
