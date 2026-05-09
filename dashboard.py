#!/usr/bin/env python3
"""Web UI for Paper Trading Dashboard — serves from paper_trades.json."""
import json
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8501
BASE_DIR = Path(__file__).parent


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = (BASE_DIR / "docs" / "index.html").read_bytes()
            self.wfile.write(html)
        elif self.path == "/paper_trades.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write((BASE_DIR / "paper_trades.json").read_bytes())
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print(f"Dashboard running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop")
    HTTPServer(("", PORT), DashboardHandler).serve_forever()
