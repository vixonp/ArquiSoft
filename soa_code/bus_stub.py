# soa_code/bus_stub.py
import socket, threading

def handle(conn, addr):
    try:
        while True:
            raw_len = conn.recv(5)
            if not raw_len:
                break
            try:
                amount = int(raw_len)
            except:
                break
            data = b''
            while len(data) < amount:
                chunk = conn.recv(amount - len(data))
                if not chunk:
                    break
                data += chunk
            print(f"[{addr}] recv:", data)
            # Echo: devolver misma estructura (longitud + contenido)
            conn.sendall(raw_len + data)
    finally:
        conn.close()

def main(host='localhost', port=5000):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    print(f"Bus stub escuchando en {host}:{port}")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    finally:
        srv.close()

if __name__ == '__main__':
    main()