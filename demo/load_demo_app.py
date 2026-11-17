"""qa-toolkit: a small practice website for LOAD TESTS - it gets slower as more users arrive.

Use it to learn and demo load testing safely, on your own computer only:
    python3 load_demo_app.py                  # runs on http://localhost:8800
    python3 load_demo_app.py --comfortable 30 # handles more before slowing down

How it behaves (like a real server under pressure):
  - up to --comfortable requests a second: fast (about 50 ms)
  - above that: every extra request a second adds about 60 ms to each answer
  - far above that (double): some requests are refused with 503 "Server busy"

Pages it has:
    /                      home page (HTML)
    /api/courses?search=x  course search (JSON)
    /api/health            "ok"
    POST /api/login        {"email": "...", "otp": "123456"} -> {"data": {"access_token": "..."}}
    /api/my-courses        needs the header  Authorization: Bearer <access_token>
Stop it with Ctrl+C.
"""
import argparse
import json
import random
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

COURSES = ["Fire safety", "Data privacy basics", "Code of conduct", "Workplace safety",
           "Anti-bribery", "Information security", "First aid at work", "Safe lifting"]

DEMO_OTP = "123456"   # the demo's test OTP - like a QA environment's fixed OTP
TOKENS = {}           # token -> email of the logged-in user
arrivals = deque()   # times of requests in the last second
lock = threading.Lock()


def requests_last_second():
    now = time.time()
    with lock:
        arrivals.append(now)
        while arrivals and arrivals[0] < now - 1:
            arrivals.popleft()
        return len(arrivals)


class Handler(BaseHTTPRequestHandler):
    comfortable = 20

    def do_GET(self):
        load = requests_last_second()
        extra = max(0, load - self.comfortable)
        if load > 2 * self.comfortable and random.random() < 0.3:
            return self.reply(503, "text/plain", "Server busy - try again later")
        time.sleep(0.05 + extra * 0.06 + random.uniform(0, 0.02))   # the busier, the slower

        url = urlparse(self.path)
        if url.path == "/":
            self.reply(200, "text/html", "<h1>Demo Academy</h1><p>Courses for everyone.</p>")
        elif url.path == "/api/courses":
            word = parse_qs(url.query).get("search", [""])[0].lower()
            self.reply(200, "application/json", json.dumps([c for c in COURSES if word in c.lower()]))
        elif url.path == "/api/health":
            self.reply(200, "text/plain", "ok")
        elif url.path == "/api/my-courses":          # needs the token from /api/login
            auth = self.headers.get("Authorization", "")
            if auth.replace("Bearer ", "") not in TOKENS:
                return self.reply(401, "application/json", '{"error": "Please log in"}')
            self.reply(200, "application/json", json.dumps({"learner": TOKENS[auth[7:]], "courses": COURSES[:3]}))
        else:
            self.reply(404, "text/plain", "Not found")

    def do_POST(self):
        """POST /api/login  {"email": "...", "otp": "123456"}  ->  {"data": {"access_token": "..."}}"""
        load = requests_last_second()
        time.sleep(0.1 + max(0, load - self.comfortable) * 0.06)   # logging in is a bit slower
        if urlparse(self.path).path != "/api/login":
            return self.reply(404, "text/plain", "Not found")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            body = {}
        if not body.get("email") or body.get("otp") != DEMO_OTP:
            return self.reply(401, "application/json", '{"error": "Wrong email or OTP"}')
        token = uuid.uuid4().hex
        TOKENS[token] = body["email"]
        self.reply(200, "application/json", json.dumps({"data": {"access_token": token}}))

    def reply(self, code, kind, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):   # keep the terminal quiet
        pass


def main():
    parser = argparse.ArgumentParser(description="Practice website that slows down under load")
    parser.add_argument("--port", type=int, default=8800)
    parser.add_argument("--comfortable", type=int, default=20,
                        help="requests a second it handles before slowing down")
    args = parser.parse_args()
    Handler.comfortable = args.comfortable
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)   # your computer only
    print("Demo app running on http://localhost:%d  (Ctrl+C to stop)" % args.port)
    print("Fast up to %d requests a second, slower above that, some 503 errors above %d."
          % (args.comfortable, 2 * args.comfortable))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
