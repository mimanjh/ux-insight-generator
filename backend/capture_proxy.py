"""A capture-only proxy that connects to validated numeric public addresses."""
import ipaddress
import select
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def public_address(host: str, port: int) -> str:
    if not host or port not in (80, 443):
        raise ValueError("Only public web pages on ports 80 and 443 are supported.")
    # ponytail: IPv4 transport only; add explicit IPv6 transition-address rules before enabling IPv6.
    addresses = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError("Private and special network addresses are not allowed.")
    return str(ips[0])


class CaptureProxy(BaseHTTPRequestHandler):
    rbufsize = 0  # Leave request bodies on the socket for forwarding.
    timeout = 10

    def forward(self):
        connected = False
        try:
            tunnel = self.command == "CONNECT"
            target = urlsplit(f"https://{self.path}" if tunnel else self.path)
            if target.scheme not in ("http", "https") or target.username or target.password:
                raise ValueError("Unsupported destination")
            port = target.port or (443 if target.scheme == "https" else 80)
            address = public_address(target.hostname, port)
            # Connect to the checked numeric address, never resolve the hostname again.
            with socket.create_connection((address, port), timeout=10) as upstream:
                if tunnel:
                    self.send_response(200)
                    self.end_headers()
                    connected = True
                else:
                    path = target.path or "/"
                    if target.query:
                        path += "?" + target.query
                    headers = [(k, v) for k, v in self.headers.items() if k.lower() not in {"connection", "proxy-connection", "proxy-authorization"}]
                    request = f"{self.command} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers) + "Connection: close\r\n\r\n"
                    upstream.sendall(request.encode("latin-1"))
                    connected = True
                deadline = time.monotonic() + 60
                transferred = 0
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 1)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        transferred += len(data)
                        if transferred > 32 * 1024 * 1024:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
        except (OSError, ValueError):
            if not connected:
                self.send_error(403, "Capture destination unavailable or not allowed")
        finally:
            self.close_connection = True

    do_CONNECT = do_GET = do_POST = do_HEAD = do_OPTIONS = do_PUT = do_PATCH = do_DELETE = forward

    def log_message(self, *args):
        pass


class ProxyServer(ThreadingHTTPServer):
    def __init__(self, *args):
        super().__init__(*args)
        self.slots = threading.BoundedSemaphore(32)

    def process_request(self, request, address):
        if self.slots.acquire(blocking=False):
            super().process_request(request, address)
        else:
            self.shutdown_request(request)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


@contextmanager
def capture_proxy():
    server = ProxyServer(("127.0.0.1", 0), CaptureProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
