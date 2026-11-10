# Concurrent File Sharing System

## Project Objective
To develop a multi-user file-sharing application using TCP sockets integrated with Software Defined Networking (SDN).

## Technologies Used
- Python
- TCP Sockets
- Mininet
- Ryu SDN Controller
- Open vSwitch
- SQLite Database
- Ubuntu

## Project Architecture
The system consists of three virtual hosts connected through an Open vSwitch.

- h1: Client 1
- h2: File Server
- h3: Client 2
- s1: OpenFlow Switch
- Ryu: SDN Controller

## Main Features
1. File Upload
2. File Download
3. Concurrent File Transfers
4. User Authentication
5. File Metadata Management
6. SDN Access Policies
7. Bandwidth Monitoring

## Testing Plan
1. Test single-client file upload.
2. Test file download.
3. Test simultaneous uploads.
4. Test simultaneous downloads.
5. Test authentication.
6. Test SDN access restrictions.
7. Measure transfer time and bandwidth.

## Expected Result
Multiple authenticated clients should be able to upload and download files simultaneously through an SDN-controlled network.
