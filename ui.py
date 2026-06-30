"""
SGIM UI Server — levanta la interfaz web en http://127.0.0.1:3000

Uso:
    python sgim_ui_server.py

Requiere que soa_service.py esté corriendo en el puerto 8000.
"""

import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser, threading, sys

PORT = 3000
HTML_FILE = "index.html"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # Lee el archivo en cada petición para reflejar cambios sin reiniciar
            with open(HTML_FILE, "r", encoding="utf-8") as f:
                html_content = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_content.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))
            
        except FileNotFoundError:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"Error: No se encontro el archivo '{HTML_FILE}' en el directorio actual.".encode("utf-8"))

    def log_message(self, fmt, *args):
        print(f"[UI] {self.address_string()} - {fmt % args}")


def open_browser(port):
    import time; time.sleep(0.4)
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    host = "127.0.0.1"
    server = HTTPServer((host, PORT), Handler)
    print(f"SGIM UI  ->  http://{host}:{PORT}")
    print("Requiere soa_service.py corriendo en el puerto 8000.")
    print("Ctrl+C para detener.")
    
    # Abre el HTML automáticamente
    threading.Thread(target=open_browser, args=(PORT,), daemon=True).start()
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        sys.exit(0)