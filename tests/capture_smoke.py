"""Real headless capture against an offline responsive fixture: python -m tests.capture_smoke."""
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from unittest.mock import patch
from backend.capture import CaptureFailed, capture_url


class Page(BaseHTTPRequestHandler):
    private_hit = False

    def do_GET(self):
        if self.path == "/private":
            Page.private_hit = True
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/private")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        mobile = self.path == "/mobile"
        self.wfile.write(f'''<meta name="viewport" content="width=device-width, initial-scale=1">
            <h1>Responsive capture fixture</h1><img src="http://127.0.0.1:{self.server.server_port}/private"><script>
            if (innerWidth !== {390 if mobile else 1440} || Boolean(navigator.maxTouchPoints) !== {str(mobile).lower()})
                document.title = 'access denied';
            </script>'''.encode())

    def log_message(self, *args):
        pass


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_lookup = socket.getaddrinfo
    original_connect = socket.create_connection
    def lookup(host, port, *args, **kwargs):
        if host == "fixture.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return original_lookup(host, port, *args, **kwargs)
    def connect(address, *args, **kwargs):
        if address == ("93.184.216.34", 80):
            address = ("127.0.0.1", server.server_port)
        return original_connect(address, *args, **kwargs)
    try:
        with patch("socket.getaddrinfo", side_effect=lookup), patch("socket.create_connection", side_effect=connect):
            for mobile, size in [(False, (1440, 900)), (True, (390, 844))]:
                image, mime = capture_url(f"http://fixture.example/{'mobile' if mobile else 'desktop'}", viewport=size, mobile=mobile)
                assert mime == "image/png" and struct.unpack(">II", image[16:24]) == size
            for url in ["http://fixture.example/redirect", f"http://127.0.0.1:{server.server_port}/private"]:
                try:
                    capture_url(url)
                    raise AssertionError("Private destination unexpectedly captured")
                except CaptureFailed:
                    pass
            assert not Page.private_hit, "Browser bypassed the capture proxy"
            print("PASS: real desktop/mobile capture and blocked private redirects/subresources")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run()
