"""
Records the webhooks that NetBox event rules send, to test them against this plugin.

`make webhook` starts it and follows its log. A NetBox webhook posts to
http://webhook:9000/hook, and http://localhost:8892/ lists the deliveries, newest first.
"""

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

deliveries: list[dict] = []


class Receiver(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.reply(200, {'received': len(deliveries), 'deliveries': deliveries[::-1]})

    def do_POST(self) -> None:
        if self.path == '/reset':
            deliveries.clear()
        elif self.path == '/hook':
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = {'_raw': body.decode('utf-8', 'replace')}
            record = {'at': datetime.now(UTC).isoformat(), 'headers': dict(self.headers), 'payload': payload}
            deliveries.append(record)
            print(json.dumps(record, indent=2), flush=True)
        else:
            self.reply(404, {'error': f'no such path: {self.path}'})
            return
        self.reply(200, {'ok': True})

    def reply(self, status: int, data: dict) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Each delivery is printed in full, so the access log line would only repeat it.
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 9000), Receiver).serve_forever()
