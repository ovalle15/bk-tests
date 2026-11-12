"""Notes API: a tiny HTTP service used for the Buildkite 101 demo.

Standard library only, so it runs on a clean macOS hosted agent with no
dependency install step.

Endpoints:
  GET  /health         -> 200 {"status": "ok"}
  GET  /notes          -> 200 [note, ...]
  GET  /notes/<id>     -> 200 note | 404 {"error": "note not found"}
  POST /notes          -> 201 note | 400 {"error": "..."}
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

NOTES = {
    1: {"id": 1, "patient": "A. Smith", "text": "Follow-up in two weeks."},
    2: {"id": 2, "patient": "B. Jones", "text": "Start physical therapy."},
}


def find_note(note_id):
    return NOTES.get(note_id)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        sys.stderr.write("[notes-api] " + (fmt % args) + "\n")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        if self.path == "/notes":
            return self._send(200, list(NOTES.values()))
        if self.path.startswith("/notes/"):
            raw_id = self.path[len("/notes/"):]
            if not raw_id.isdigit():
                return self._send(400, {"error": "note id must be a number"})
            note = find_note(int(raw_id))
            if note is None:
                return self._send(200, None)
            # if note is None:
            #     return self._send(404, {"error": "note not found"})
            return self._send(200, note)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/notes":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "body must be JSON"})
        if not data.get("patient") or not data.get("text"):
            return self._send(400, {"error": "patient and text are required"})
        note_id = max(NOTES) + 1
        note = {"id": note_id, "patient": data["patient"], "text": data["text"]}
        NOTES[note_id] = note
        return self._send(201, note)


def main():
    port = int(os.environ.get("API_PORT", "8080"))
    server = HTTPServer(("127.0.0.1", port), Handler)
    print("Notes API listening on http://127.0.0.1:%d" % port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
