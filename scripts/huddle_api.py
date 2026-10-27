"""The Huddle's comments and votes: a small JSON API with a SQLite file behind it.

Posts come from two places: files in content/huddle/ (built into the page) and posts admins
write on the Huddle tab, which are stored here with the comments and votes.
Each manager signs in with their Sleeper name and a personal 6-digit code. Codes are stored
hashed, and an address that keeps guessing wrong is locked out for a while.

    python scripts/huddle_api.py serve [--port 8081] [--static site]   # --static: local preview
    python scripts/huddle_api.py codes [NAME ...]    # issue codes (all managers without one, or NAME)
    python scripts/huddle_api.py admin NAME [--off]  # admins can remove comments and reissue codes

The server runs it as a service on 127.0.0.1:8081; Caddy sends /api/* to it. The database lives
at $HUDDLE_DB (on the server, ~/huddle/huddle.db, outside the repo).
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "content" / "huddle"
DB = Path(os.environ.get("HUDDLE_DB", ROOT / "data" / "huddle.db"))
MAX_TEXT = 1500
TAGS = ["Announcement", "Proposal", "How it works", "Rules", "Poll", "Trash talk"]
FAIL_LIMIT, FAIL_WINDOW = 10, 15 * 60   # wrong codes per address, seconds
COMMENT_GAP = 15                        # seconds between one member's comments

SCHEMA = """
create table if not exists members (name text primary key collate nocase, salt text, hash text, admin integer default 0);
create table if not exists comments (id integer primary key, post text, name text, at text, text text, hidden integer default 0);
create table if not exists votes (post text, name text, option integer, at text, primary key (post, name));
create table if not exists posts (id integer primary key, at text, author text, tag text, title text, summary text,
  body text, question text, detail text, options text, hidden integer default 0);
"""


@contextmanager
def db():
    """A connection that commits on success, rolls back on error, and always closes."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        with con:
            yield con
    finally:
        con.close()


def managers():
    """The 20 conference managers' Sleeper names, from the last site build."""
    data = json.loads((ROOT / "site" / "data.json").read_text(encoding="utf-8"))
    return sorted({t["manager"] for t in data["teams"]}, key=str.lower)


def manager(name):
    """The manager's exact Sleeper name, matched without regard to case, or None."""
    return next((m for m in managers() if m.lower() == str(name).lower()), None)


def article(row):
    """A post written on the Huddle tab, in the same shape as a content/huddle/ file (body as text)."""
    options = json.loads(row["options"] or "[]")
    return {"id": f"p{row['id']}", "date": row["at"], "author": row["author"], "tag": row["tag"],
            "title": row["title"], "summary": row["summary"], "body": row["body"],
            "proposal": {"question": row["question"], "detail": row["detail"], "options": options} if options else None}


def post(con, pid):
    """A post by id: "p<number>" for one written on the tab, otherwise a content/huddle/ file name."""
    pid = str(pid or "")
    if re.fullmatch(r"p\d{1,9}", pid):
        row = con.execute("select * from posts where id = ? and hidden = 0", (int(pid[1:]),)).fetchone()
        return article(row) if row else None
    if not re.fullmatch(r"[\w-]{1,80}", pid):
        return None
    path = POSTS / f"{pid}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def clean(body, key, limit, required=False):
    text = re.sub(r"\n{3,}", "\n\n", str(body.get(key) or "").replace("\r", "").strip())
    if len(text) > limit or (required and not text):
        raise Refuse(400, f"The {key} needs to be {'1' if required else '0'} to {limit} characters.")
    return text


def write_post(con, me, body):
    """Create a post, or edit one when body has an id. Poll options are locked once anyone has voted."""
    tag = body.get("tag") if body.get("tag") in TAGS else TAGS[0]
    title, summary = clean(body, "title", 120, True), clean(body, "summary", 500)
    text, question, detail = clean(body, "body", 10000), clean(body, "question", 200), clean(body, "detail", 300)
    options = [str(o).strip()[:80] for o in body.get("options") or [] if str(o).strip()]
    if question and not 2 <= len(options) <= 6:
        raise Refuse(400, "A vote needs 2 to 6 options.")
    if options and not question:
        raise Refuse(400, "Give the vote a question.")
    fields = (tag, title, summary, text, question, detail, json.dumps(options))
    if body.get("id") is None:
        con.execute("insert into posts (at, author, tag, title, summary, body, question, detail, options) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?)", (now(), me["name"], *fields))
        return
    old = post(con, body["id"])
    if not old or not str(body["id"]).startswith("p"):
        raise Refuse(404, "That post can't be edited here.")
    voted = con.execute("select 1 from votes where post = ? limit 1", (body["id"],)).fetchone()
    if voted and options != (old["proposal"] or {}).get("options", []):
        raise Refuse(400, "People have already voted, so the vote's options can't change.")
    con.execute("update posts set tag = ?, title = ?, summary = ?, body = ?, question = ?, detail = ?, options = ? "
                "where id = ?", (*fields, int(body["id"][1:])))


def digest(salt, code):
    return hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), 100_000).hex()


def new_code(con, name):
    code, salt = f"{secrets.randbelow(10**6):06d}", secrets.token_hex(16)
    con.execute("insert into members (name, salt, hash) values (?, ?, ?) "
                "on conflict(name) do update set salt = excluded.salt, hash = excluded.hash",
                (name, salt, digest(salt, code)))
    return code


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Refuse(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


_fails, _last_comment, _lock = {}, {}, threading.Lock()


def sign_in(con, body, ip):
    with _lock:
        recent = [t for t in _fails.get(ip, []) if t > time.time() - FAIL_WINDOW]
        _fails[ip] = recent
    if len(recent) >= FAIL_LIMIT:
        raise Refuse(429, "Too many wrong codes. Try again in 15 minutes.")
    name, code = str(body.get("name", "")), str(body.get("code", "")).strip()
    row = con.execute("select * from members where name = ?", (name,)).fetchone()
    if not row or not row["hash"] or not hmac.compare_digest(digest(row["salt"], code), row["hash"]):
        with _lock:
            _fails.setdefault(ip, []).append(time.time())
        raise Refuse(403, "That name and code don't match.")
    return row


def everything(con):
    out = {}
    for c in con.execute("select id, post, name, at, text from comments where hidden = 0 order by id"):
        out.setdefault(c["post"], {"comments": [], "votes": {}})["comments"].append(
            {"id": c["id"], "who": c["name"], "at": c["at"], "text": c["text"]})
    for v in con.execute("select post, name, option from votes"):
        out.setdefault(v["post"], {"comments": [], "votes": {}})["votes"][v["name"]] = v["option"]
    articles = [article(r) for r in con.execute("select * from posts where hidden = 0 order by id desc")]
    return {"posts": out, "articles": articles}


def handle(path, body, ip):
    """One API call. Returns a JSON-able dict or raises Refuse."""
    with db() as con:
        if path == "/api/huddle" and body is None:
            return everything(con)
        if body is None:
            raise Refuse(404, "Not found.")
        me = sign_in(con, body, ip)
        if path == "/api/huddle/check":
            return {"name": me["name"], "admin": bool(me["admin"])}
        if path == "/api/huddle/comment":
            P, text = post(con, body.get("post")), re.sub(r"\n{3,}", "\n\n", str(body.get("text", "")).strip())
            if not P:
                raise Refuse(404, "That post doesn't exist.")
            if not text or len(text) > MAX_TEXT:
                raise Refuse(400, f"Comments are 1 to {MAX_TEXT} characters.")
            with _lock:
                if time.time() - _last_comment.get(me["name"], 0) < COMMENT_GAP:
                    raise Refuse(429, "Easy, give it a few seconds between comments.")
                _last_comment[me["name"]] = time.time()
            con.execute("insert into comments (post, name, at, text) values (?, ?, ?, ?)",
                        (body["post"], me["name"], now(), text))
        elif path == "/api/huddle/vote":
            P = post(con, body.get("post"))
            options = ((P or {}).get("proposal") or {}).get("options", [])
            if not isinstance(body.get("option"), int) or not 0 <= body["option"] < len(options):
                raise Refuse(400, "That isn't one of the options.")
            con.execute("insert into votes values (?, ?, ?, ?) on conflict(post, name) do update "
                        "set option = excluded.option, at = excluded.at",
                        (body["post"], me["name"], body["option"], now()))
        elif path == "/api/huddle/remove":
            c = con.execute("select name from comments where id = ?", (body.get("id"),)).fetchone()
            if not c or (c["name"] != me["name"] and not me["admin"]):
                raise Refuse(403, "You can only remove your own comments.")
            con.execute("update comments set hidden = 1 where id = ?", (body["id"],))
        elif path in ("/api/huddle/post", "/api/huddle/unpost"):
            if not me["admin"]:
                raise Refuse(403, "Only admins can post.")
            if path.endswith("/post"):
                write_post(con, me, body)
            elif not str(body.get("id", "")).startswith("p") or not post(con, body["id"]):
                raise Refuse(404, "That post can't be removed here.")
            else:
                con.execute("update posts set hidden = 1 where id = ?", (int(body["id"][1:]),))
        elif path == "/api/huddle/reissue":
            if not me["admin"]:
                raise Refuse(403, "Only admins can issue codes.")
            member = manager(body.get("member", ""))
            if not member:
                raise Refuse(404, "No manager by that name.")
            return {"member": member, "code": new_code(con, member)}
        else:
            raise Refuse(404, "Not found.")
        return everything(con)


class Handler(SimpleHTTPRequestHandler):
    serve_static = False

    def reply(self, status, payload):
        blob = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def api(self, body):
        # Behind the Cloudflare Tunnel the visitor's address arrives in CF-Connecting-IP.
        ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        try:
            self.reply(200, handle(self.path.split("?")[0], body, ip))
        except Refuse as e:
            self.reply(e.status, {"error": e.message})

    def do_GET(self):
        if self.path.split("?")[0] == "/api/huddle":
            return self.api(None)
        if self.serve_static and not self.path.startswith("/api/"):
            return super().do_GET()
        self.reply(404, {"error": "Not found."})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        if not self.path.startswith("/api/huddle/") or size > 30000:
            return self.reply(400, {"error": "Bad request."})
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            return self.reply(400, {"error": "Bad request."})
        self.api(body)

    def log_message(self, fmt, *args):  # quiet, and keeps codes out of any log
        pass


def main(argv):
    cmd = argv[0] if argv else "serve"
    if cmd == "serve":
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8081
        static = argv[argv.index("--static") + 1] if "--static" in argv else None
        handler = partial(Handler, directory=str(ROOT / static)) if static else Handler
        Handler.serve_static = bool(static)
        with db():
            pass
        print(f"huddle: listening on 127.0.0.1:{port}", flush=True)
        ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
    elif cmd == "codes":
        names = [manager(n) or sys.exit(f"No manager named {n}") for n in argv[1:]] or managers()
        with db() as con:
            have = {r["name"].lower() for r in con.execute("select name from members where hash is not null")}
            for name in names:
                if argv[1:] or name.lower() not in have:
                    print(f"{name:<20} {new_code(con, name)}")
    elif cmd == "admin" and len(argv) > 1:
        argv[1] = manager(argv[1]) or sys.exit(f"No manager named {argv[1]}")
        with db() as con:
            con.execute("insert into members (name, admin) values (?, ?) on conflict(name) do update set admin = excluded.admin",
                        (argv[1], 0 if "--off" in argv else 1))
        print(argv[1], "is", "no longer an admin" if "--off" in argv else "an admin")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
