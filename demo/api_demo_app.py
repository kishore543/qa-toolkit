"""Practice API for /qa-toolkit:api - a tiny "Tasks" API with 4 bugs planted on purpose.

    python3 demo/api_demo_app.py            # http://localhost:8800  (spec: /openapi.json)
    token for the practice:  API_TOKEN=practice-token

Planted bugs (the API test should catch every one):
  1. POST /tasks without "title" crashes with 500 instead of answering 400.
  2. GET /tasks/{id} for an id that does not exist answers 200 with null instead of 404.
  3. GET /users/{id} works WITHOUT a token - anyone can read user details (security bug).
  4. "done" comes back as the text "false" instead of true/false (does not match the spec).
Data lives in memory only; restart the app to reset it.
"""
import json
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = "practice-token"
TASKS = {}
USERS = {"u1": {"id": "u1", "name": "Asha QA", "email": "asha@example.com", "role": "tester"}}

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Practice Tasks API", "version": "1.0.0"},
    "servers": [{"url": "http://localhost:8800"}],
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "schemas": {
            "NewTask": {"type": "object", "required": ["title"], "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 100, "example": "Write test plan"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"], "example": "high"},
                "estimate_hours": {"type": "integer", "minimum": 0, "example": 3}}},
            "Task": {"type": "object", "required": ["id", "title", "done", "priority"], "properties": {
                "id": {"type": "string"}, "title": {"type": "string"}, "done": {"type": "boolean"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]}, "estimate_hours": {"type": "integer"}}},
            "TaskUpdate": {"type": "object", "properties": {"title": {"type": "string", "example": "Write test plan v2"},
                                                            "done": {"type": "boolean", "example": True}}},
            "User": {"type": "object", "required": ["id", "name", "email"], "properties": {
                "id": {"type": "string"}, "name": {"type": "string"}, "email": {"type": "string", "format": "email"},
                "role": {"type": "string"}}},
            "Error": {"type": "object", "required": ["error"], "properties": {"error": {"type": "string"}}},
        },
    },
    "security": [{"bearer": []}],
    "paths": {
        "/tasks": {
            "get": {"tags": ["Tasks"], "summary": "List tasks", "responses": {
                "200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Task"}}}}},
                "401": {"description": "no token"}}},
            "post": {"tags": ["Tasks"], "summary": "Create a task",
                     "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/NewTask"}}}},
                     "responses": {"201": {"description": "created", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Task"}}}},
                                   "400": {"description": "bad input"}, "401": {"description": "no token"}}}},
        "/tasks/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": {"tags": ["Tasks"], "summary": "Get one task", "responses": {
                "200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Task"}}}},
                "401": {"description": "no token"}, "404": {"description": "not found"}}},
            "patch": {"tags": ["Tasks"], "summary": "Update a task",
                      "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TaskUpdate"}}}},
                      "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Task"}}}},
                                    "400": {"description": "bad input"}, "401": {"description": "no token"}, "404": {"description": "not found"}}},
            "delete": {"tags": ["Tasks"], "summary": "Delete a task", "responses": {
                "204": {"description": "deleted"}, "401": {"description": "no token"}, "404": {"description": "not found"}}}},
        "/users/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "example": "u1"}}],
            "get": {"tags": ["Users"], "summary": "Get a user", "responses": {
                "200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}}},
                "401": {"description": "no token"}, "404": {"description": "not found"}}}},
    },
}


def show(task):
    out = dict(task)
    out["done"] = "true" if task["done"] else "false"   # BUG 4: text instead of true/false
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body=None):
        data = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        return self.headers.get("Authorization", "") == "Bearer " + TOKEN

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None

    def route(self, method):
        path = self.path.split("?")[0]
        if path == "/openapi.json":
            return self.reply(200, SPEC)
        m_user = re.fullmatch(r"/users/([^/]+)", path)
        if m_user and method == "GET":   # BUG 3: no token check here
            u = USERS.get(m_user.group(1))
            return self.reply(200, u) if u else self.reply(404, {"error": "user not found"})
        if not self.authed():
            return self.reply(401, {"error": "login required"})
        if path == "/tasks" and method == "GET":
            return self.reply(200, [show(t) for t in TASKS.values()])
        if path == "/tasks" and method == "POST":
            b = self.body()
            if b is None:
                return self.reply(400, {"error": "body is not JSON"})
            title = b["title"]   # BUG 1: KeyError -> 500 when title is missing
            if not isinstance(title, str) or not title.strip() or len(title) > 100:
                return self.reply(400, {"error": "title must be 1-100 characters"})
            pr = b.get("priority", "medium")
            if pr not in ("low", "medium", "high"):
                return self.reply(400, {"error": "priority must be low, medium or high"})
            est = b.get("estimate_hours", 0)
            if not isinstance(est, int) or isinstance(est, bool) or est < 0:
                return self.reply(400, {"error": "estimate_hours must be a whole number >= 0"})
            t = {"id": uuid.uuid4().hex[:8], "title": title, "done": False, "priority": pr, "estimate_hours": est}
            TASKS[t["id"]] = t
            return self.reply(201, show(t))
        m = re.fullmatch(r"/tasks/([^/]+)", path)
        if m:
            t = TASKS.get(m.group(1))
            if method == "GET":
                return self.reply(200, show(t) if t else None)   # BUG 2: 200 + null instead of 404
            if not t:
                return self.reply(404, {"error": "task not found"})
            if method == "PATCH":
                b = self.body()
                if b is None or ("title" in b and (not isinstance(b["title"], str) or not b["title"].strip())) \
                        or ("done" in b and not isinstance(b["done"], bool)):
                    return self.reply(400, {"error": "bad input"})
                t.update({k: v for k, v in b.items() if k in ("title", "done")})
                return self.reply(200, show(t))
            if method == "DELETE":
                del TASKS[t["id"]]
                return self.reply(204)
        return self.reply(404, {"error": "no such address"})

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        try:
            self.route("POST")
        except Exception:
            self.reply(500, {"error": "internal server error"})

    def do_PATCH(self):
        self.route("PATCH")

    def do_DELETE(self):
        self.route("DELETE")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    print("Practice Tasks API on http://localhost:%d  (spec: /openapi.json, token: %s)" % (port, TOKEN))
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
