"""Real headless capture against an offline responsive fixture: python -m tests.capture_smoke."""
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.capture import capture_url


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        mobile = self.path == "/mobile"
        self.wfile.write(f'''<meta name="viewport" content="width=device-width, initial-scale=1">
            <h1>Responsive capture fixture</h1><script>
            if (innerWidth !== {390 if mobile else 1440} || Boolean(navigator.maxTouchPoints) !== {str(mobile).lower()})
                document.title = 'access denied';
            </script>'''.encode())

    def log_message(self, *args):
        pass


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for mobile, size in [(False, (1440, 900)), (True, (390, 844))]:
            image, mime = capture_url(f"http://127.0.0.1:{server.server_port}/{'mobile' if mobile else 'desktop'}", viewport=size, mobile=mobile)
            assert mime == "image/png" and struct.unpack(">II", image[16:24]) == size
        print("PASS: real Chromium desktop/mobile capture, viewport and touch emulation")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    run()
