"""API tests for the Notes API. They call the running server over HTTP."""
import json
import os
import unittest
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:%s" % os.environ.get("API_PORT", "8080")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


class HealthTests(unittest.TestCase):
    def test_health_returns_ok(self):
        status, body = call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})


class NotesReadTests(unittest.TestCase):
    def test_list_notes(self):
        status, body = call("GET", "/notes")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body), 2)

    def test_get_existing_note(self):
        status, body = call("GET", "/notes/1")
        self.assertEqual(status, 200)
        self.assertEqual(body["patient"], "A. Smith")

    def test_get_missing_note_returns_404(self):
        status, body = call("GET", "/notes/999")
        self.assertEqual(status, 404, "GET /notes/999 should be 404, got %s with body %r" % (status, body))
        self.assertEqual(body, {"error": "note not found"})

    def test_non_numeric_id_returns_400(self):
        status, _ = call("GET", "/notes/abc")
        self.assertEqual(status, 400)


class NotesWriteTests(unittest.TestCase):
    def test_create_note(self):
        status, body = call("POST", "/notes", {"patient": "C. Lee", "text": "Order labs."})
        self.assertEqual(status, 201)
        self.assertEqual(body["patient"], "C. Lee")

    def test_create_note_requires_text(self):
        status, body = call("POST", "/notes", {"patient": "C. Lee"})
        self.assertEqual(status, 400)
        self.assertIn("required", body["error"])


if __name__ == "__main__":
    unittest.main()
