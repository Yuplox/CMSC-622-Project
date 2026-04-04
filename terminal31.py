import socket
import sys
import time
import struct
from shared import xor_bytes

def run_terminal(server_ip, server_port, msg):
    # Creat client socket
    send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Encode msg as bytes
    data = msg.encode('utf-8')
    
    # Send data to server
    print(f"Sending message to {server_ip}:{server_port}...")
    send_socket.sendto(data, (server_ip, server_port))

    # Set multicast address
    multicast_address = '224.0.0.1'
    multicast_port = 10000

    # Create multicast listener
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(('', multicast_port))

    # Join multicast group
    mreq = struct.pack("4s4s", socket.inet_aton(multicast_address), socket.inet_aton('0.0.0.0'))
    listen_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    
    listen_socket.settimeout(5.0)

    try:
        coded_data, addr = listen_socket.recvfrom(2048)
        print(f"Received multicast packet ({len(coded_data)} bytes) from {addr}.")

        decoded_data = xor_bytes(coded_data, my_data)
        decoded_string = decoded_data.decode('utf-8').rstrip('\x00')
        print(f"DECODED MESSAGE: '{decoded_string}'")

    except socket.timeout:
        print("Timed out waiting for multicast response.")
    finally:
        send_socket.close()
        listen_socket.close()

    

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 terminal31.py [SERVER_IP] [MESSAGE]")
        sys.exit(1)
    
    srv_ip = sys.argv[1]
    msg = sys.argv[2]

    # Small delay to ensure server is fully spun up before terminals fire
    time.sleep(1) 

    run_terminal(srv_ip, 9000, msg)