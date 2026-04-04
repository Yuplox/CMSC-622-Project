import socket
import struct
import sys
from shared import xor_bytes

def run_server(host='0.0.0.0', port=9000):
    # Create Socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind((host, port))

    # Set TTL
    ttl = struct.pack('b', 2)
    server_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

    # Set multicast address
    multicast_group = ('224.0.0.1', 10000)

    print(f"Coding Server listening on {host}:{port}...")

    clients = {}

    while True:
        data, addr = server_socket.recvfrom(2048) # Buffer size of 2KB
        print(f"Received {len(data)} bytes from {addr}")
        
        clients[addr] = data

        # Once we have packets from 2 different terminals, we can network code them
        if len(clients) == 2:
            addresses = list(clients.keys())
            payload_A = clients[addresses[0]]
            payload_B = clients[addresses[1]]

            # Perform Network Coding (XOR)
            combined_payload = xor_bytes(payload_A, payload_B)
            print(f"XORed payloads. Broadcasting {len(combined_payload)} bytes back to terminals.")

            # Send combined packet to multicast group
            server_socket.sendto(combined_payload, multicast_group)

            # Clear buffer for the next round
            clients.clear()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server31.py [SERVER_IP]")
        sys.exit(1)

    srv_ip = sys.argv[1]
    run_server(srv_ip)