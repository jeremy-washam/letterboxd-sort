import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"

NS = {"letterboxd": "https://letterboxd.com", "tmdb": "https://themoviedb.org"}
PORT = 8137


def safe_username(raw):
    name = re.sub(r"[^A-Za-z0-9_]", "", raw or "")
    if not name:
        raise ValueError("invalid username")
    return name.lower()


def fetch_films(username):
    url = f"https://letterboxd.com/{username}/rss/"
    req = urllib.request.Request(url, headers={"User-Agent": "letterboxd-ranker/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        xml = resp.read()
    root = ET.fromstring(xml)
    films = {}
    for item in root.iter("item"):
        title = item.findtext("letterboxd:filmTitle", namespaces=NS)
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        if not link or link in films:
            continue
        desc = item.findtext("description") or ""
        poster = None
        m = re.search(r'<img src="([^"]+)"', desc)
        if m:
            poster = m.group(1)
        films[link] = {
            "key": link,
            "title": title,
            "year": item.findtext("letterboxd:filmYear", namespaces=NS) or "",
            "rating": item.findtext("letterboxd:memberRating", namespaces=NS),
            "watched": item.findtext("letterboxd:watchedDate", namespaces=NS) or "",
            "poster": poster,
            "url": link,
        }
    return list(films.values())


def state_path(username):
    DATA.mkdir(exist_ok=True)
    return DATA / f"{username}.json"


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def query_username(self, query):
        return safe_username(parse_qs(query).get("username", [""])[0])

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route == "/":
                self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
            elif route == "/app.js":
                self.send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
            elif route == "/style.css":
                self.send_file(STATIC / "style.css", "text/css; charset=utf-8")
            elif route == "/api/films":
                username = self.query_username(parsed.query)
                self.send_json({"films": fetch_films(username)})
            elif route == "/api/state":
                username = self.query_username(parsed.query)
                path = state_path(username)
                if path.exists():
                    self.send_json(json.loads(path.read_text()))
                else:
                    self.send_json({"films": {}, "ranked": [], "skipped": []})
            else:
                self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)
        except urllib.error.HTTPError as e:
            msg = "user not found" if e.code == 404 else f"letterboxd returned {e.code}"
            self.send_json({"error": msg}, 502)
        except urllib.error.URLError as e:
            self.send_json({"error": f"could not reach letterboxd: {e.reason}"}, 502)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/state":
            self.send_json({"error": "not found"}, 404)
            return
        try:
            username = self.query_username(parsed.query)
            length = int(self.headers.get("Content-Length", 0))
            state = json.loads(self.rfile.read(length))
            if not isinstance(state, dict):
                raise ValueError("state must be an object")
            state_path(username).write_text(json.dumps(state, indent=2))
            self.send_json({"ok": True})
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json({"error": str(e)}, 400)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Letterboxd Ranker running at http://127.0.0.1:{PORT}")
    server.serve_forever()
