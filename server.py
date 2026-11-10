
import socket
import threading
import json
import struct
import os
import time

import database

HOST = "0.0.0.0"
PORT = 5001

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_DIR = os.path.join(BASE_DIR, "files")

os.makedirs(FILE_DIR, exist_ok=True)

def send_json(sock, data):
    message = json.dumps(data).encode()
    header = struct.pack("!I", len(message))
    sock.sendall(header + message)

def receive_exact(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(min(65536, size - len(data)))

        if not chunk:
            raise ConnectionError("Connection closed")

        data.extend(chunk)

    return bytes(data)

def receive_json(sock):
    header = receive_exact(sock, 4)
    length = struct.unpack("!I", header)[0]

    if length > 1024 * 1024:
        raise ValueError("Message too large")

    return json.loads(receive_exact(sock, length).decode())

def handle_upload(conn, username, request):
    filename = os.path.basename(request["filename"])
    size = int(request["filesize"])

    if not filename or size < 0 or size > 100 * 1024 * 1024:
        send_json(conn, {"status": "ERROR", "message": "Invalid file"})
        return

    stored_name = f"{username}_{filename}"

    path = os.path.join(FILE_DIR, stored_name)
    temp_path = path + ".part"

    send_json(conn, {"status": "READY"})

    try:
        with open(temp_path, "wb") as file:
            remaining = size

            while remaining > 0:
                chunk = conn.recv(min(65536, remaining))

                if not chunk:
                    raise ConnectionError("Incomplete upload")

                file.write(chunk)
                remaining -= len(chunk)

        os.replace(temp_path, path)

        database.add_file(filename, stored_name, username, size)

        send_json(conn, {
            "status": "SUCCESS",
            "message": "Upload completed"
        })

        print(f"{username} uploaded {filename} ({size} bytes)")

    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

def handle_download(conn, request):
    filename = os.path.basename(request["filename"])
    result = database.get_file(filename)

    if result is None:
        send_json(conn, {
            "status": "ERROR",
            "message": "File not found"
        })
        return

    stored_name, size, owner = result
    path = os.path.join(FILE_DIR, stored_name)

    if not os.path.isfile(path) or os.path.getsize(path) != size:
        send_json(conn, {
            "status": "ERROR",
            "message": "File unavailable"
        })
        return

    send_json(conn, {
        "status": "READY",
        "filesize": size
    })

    with open(path, "rb") as file:
        while True:
            chunk = file.read(65536)

            if not chunk:
                break

            conn.sendall(chunk)

    print(f"File {filename} downloaded by a client")


def handle_client(conn, address):
    print(f"Client connected: {address}")

    try:
        login = receive_json(conn)

        username = login.get("username", "")
        password = login.get("password", "")

        if not database.authenticate_user(username, password):
            send_json(conn, {
                "status": "ERROR",
                "message": "Invalid username or password"
            })
            return

        send_json(conn, {"status": "SUCCESS"})
        print(f"{username} authenticated")

        while True:
            request = receive_json(conn)
            action = request.get("action")

            if action == "UPLOAD":
                handle_upload(conn, username, request)

            elif action == "DOWNLOAD":
                handle_download(conn, request)

            elif action == "LIST":
                files = database.list_files()

                send_json(conn, {
                    "status": "SUCCESS",
                    "files": [
                        {
                            "filename": f[0],
                            "owner": f[1],
                            "size": f[2],
                            "upload_time": f[3]
                        }
                        for f in files
                    ]
                })

            elif action == "EXIT":
                break

            else:
                send_json(conn, {
                    "status": "ERROR",
                    "message": "Invalid request"
                })

    except (ConnectionError, OSError):
        print(f"Client disconnected: {address}")

    except Exception as error:
        print(f"Client error: {error}")

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
    print(f"Server running on port {PORT}")
    print("Waiting for clients...")

    try:
        while True:
            conn, address = server.accept()

            threading.Thread(
                target=handle_client,
                args=(conn, address),
                daemon=True
            ).start()

    except KeyboardInterrupt:
        print("\nServer shutting down")

    finally:
        server.close()

if __name__ == "__main__":
    start_server()
