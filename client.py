
import socket
import json
import struct
import os
import time

SERVER_HOST = "10.0.0.2"
SERVER_PORT = 5001

def send_json(sock, data):
    message = json.dumps(data).encode()
    header = struct.pack("!I", len(message))
    sock.sendall(header + message)

def receive_exact(sock, size):
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(min(65536, size - len(data)))

        if not chunk:
            raise ConnectionError("Server disconnected")

        data.extend(chunk)

    return bytes(data)

def receive_json(sock):
    header = receive_exact(sock, 4)
    length = struct.unpack("!I", header)[0]

    if length > 1024 * 1024:
        raise ValueError("Message too large")

    return json.loads(receive_exact(sock, length).decode())

def connect_server(username, password):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((SERVER_HOST, SERVER_PORT))

    send_json(sock, {
        "username": username,
        "password": password
    })

    response = receive_json(sock)

    if response["status"] != "SUCCESS":
        print("Login failed:", response["message"])
        sock.close()
        return None

    print("Login successful!")
    return sock

def upload_file(sock, filename):
    if not os.path.isfile(filename):
        print("Local file not found")
        return

    name = os.path.basename(filename)
    size = os.path.getsize(filename)

    send_json(sock, {
        "action": "UPLOAD",
        "filename": name,
        "filesize": size
    })

    response = receive_json(sock)

    if response["status"] != "READY":
        print(response["message"])
        return

    start = time.perf_counter()

    with open(filename, "rb") as file:
        while True:
            chunk = file.read(65536)

            if not chunk:
                break

            sock.sendall(chunk)

    response = receive_json(sock)

    duration = time.perf_counter() - start

    if response["status"] == "SUCCESS":
        speed = (size / (1024 * 1024)) / duration if duration > 0 else 0

        print("Upload successful!")
        print(f"File size: {size} bytes")
        print(f"Transfer time: {duration:.4f} seconds")
        print(f"Transfer speed: {speed:.2f} MB/s")

    else:
        print("Upload failed:", response["message"])

def download_file(sock, filename):
    send_json(sock, {
        "action": "DOWNLOAD",
        "filename": filename
    })

    response = receive_json(sock)

    if response["status"] != "READY":
        print(response["message"])
        return

    size = response["filesize"]
    start = time.perf_counter()

    local_name = "downloaded_" + os.path.basename(filename)

    with open(local_name, "wb") as file:
        remaining = size

        while remaining > 0:
            chunk = sock.recv(min(65536, remaining))

            if not chunk:
                raise ConnectionError("Incomplete download")

            file.write(chunk)
            remaining -= len(chunk)

    duration = time.perf_counter() - start
    speed = (size / (1024 * 1024)) / duration if duration > 0 else 0

    print("Download successful!")
    print("Saved as:", local_name)
    print(f"File size: {size} bytes")
    print(f"Transfer time: {duration:.4f} seconds")
    print(f"Transfer speed: {speed:.2f} MB/s")

def list_files(sock):
    send_json(sock, {"action": "LIST"})

    response = receive_json(sock)

    if response["status"] != "SUCCESS":
        print("Unable to retrieve files")
        return

    print("\nAVAILABLE FILES")
    print("----------------------------")

    if not response["files"]:
        print("No files available")
        return

    for file in response["files"]:
        size_mb = file["size"] / (1024 * 1024)

        print("Filename:", file["filename"])
        print("Owner:", file["owner"])
        print(f"Size: {size_mb:.2f} MB")
        print("Uploaded:", file["upload_time"])
        print("----------------------------")


def main():
    print("================================")
    print("     FILE SHARING CLIENT")
    print("================================")

    username = input("Username: ")
    password = input("Password: ")

    sock = None

    try:
        sock = connect_server(username, password)

        if sock is None:
            print("Authentication failed. Exiting...")
            return

        while True:
            print("\n1. Upload File")
            print("2. Download File")
            print("3. List Files")
            print("4. Exit")

            choice = input("Enter your choice: ")

            if choice == "4":
                print("Exiting...")
                break

            if choice == "1":
                filename = input("Enter local file path: ")
                upload_file(sock, filename)

            elif choice == "2":
                filename = input("Enter filename to download: ")
                download_file(sock, filename)

            elif choice == "3":
                list_files(sock)

            else:
                print("Invalid choice")

    except Exception as error:
        print("Error:", error)

    finally:
        if sock is not None:
            sock.close()

if __name__ == "__main__":
    main()
