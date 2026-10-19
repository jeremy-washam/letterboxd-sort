import csv
import html
import io
import json
import re
import time
import zipfile
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


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "letterboxd-ranker/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


GRID_ITEM_RE = re.compile(r'<li class="griditem">(.*?)</li>', re.S)
PAGE_LINK_RE = re.compile(r"/films/page/(\d+)/")


def parse_grid_item(block, username):
    slug_m = re.search(r'data-item-link="/film/([^/"]+)/"', block)
    name_m = re.search(r'data-item-name="([^"]*)"', block)
    id_m = re.search(r"film:(\d+)", block)
    if not (slug_m and name_m and id_m):
        return None
    slug = slug_m.group(1)
    name = html.unescape(name_m.group(1))
    year = ""
    title = name
    ym = re.match(r"^(.*) \((\d{4})\)$", name)
    if ym:
        title, year = ym.group(1), ym.group(2)
    film_id = id_m.group(1)
    digits = "/".join(film_id)
    rating = None
    rm = re.search(r"rated-(\d+)", block)
    if rm:
        rating = f"{int(rm.group(1)) / 2:.1f}"
    return {
        "key": f"https://letterboxd.com/{username}/film/{slug}/",
        "title": title,
        "year": year,
        "rating": rating,
        "watched": "",
        "poster": f"https://a.ltrbxd.com/resized/film-poster/{digits}/{film_id}-{slug}-0-600-0-900-crop.jpg",
        "url": f"https://letterboxd.com/{username}/film/{slug}/",
    }


def scrape_profile_films(username):
    films = {}
    page = 1
    last_page = 1
    while page <= last_page:
        url = (
            f"https://letterboxd.com/{username}/films/"
            if page == 1
            else f"https://letterboxd.com/{username}/films/page/{page}/"
        )
        try:
            body = http_get(url).decode()
        except urllib.error.HTTPError:
            # Cloudflare blocks /films/page/N/ for non-browser clients; keep what we have
            if page == 1:
                raise
            break
        if page == 1:
            pages = [int(p) for p in PAGE_LINK_RE.findall(body)]
            last_page = max(pages) if pages else 1
        for block in GRID_ITEM_RE.findall(body):
            film = parse_grid_item(block, username)
            if film and film["key"] not in films:
                films[film["key"]] = film
        page += 1
        if page <= last_page:
            time.sleep(0.3)
    return films


def fetch_rss_films(username):
    xml = http_get(f"https://letterboxd.com/{username}/rss/")
    root = ET.fromstring(xml)
    films = {}
    for item in root.iter("item"):
        title = item.findtext("letterboxd:filmTitle", namespaces=NS)
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        link = re.sub(r"(/film/[^/]+)/\d+/?$", r"\1/", link)
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


def fetch_films(username):
    films = scrape_profile_films(username)
    for film in fetch_rss_films(username):
        existing = films.get(film["key"])
        if existing:
            existing["watched"] = film["watched"]
            if film.get("rating"):
                existing["rating"] = film["rating"]
            if film.get("poster"):
                existing["poster"] = film["poster"]
        else:
            films[film["key"]] = film
    return list(films.values())


def find_export_zip(username):
    candidates = []
    for folder in [Path.home() / "Downloads", DATA / "import", ROOT]:
        if folder.is_dir():
            candidates += folder.glob(f"letterboxd-{username}-*.zip")
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def import_export_zip(username):
    path = find_export_zip(username)
    if not path:
        raise FileNotFoundError(
            f"No export zip found. Download yours from letterboxd.com/settings/data/ "
            f"into ~/Downloads (it will be named letterboxd-{username}-....zip) and retry."
        )

    def read_csv(zf, name):
        try:
            with zf.open(name) as f:
                return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))
        except KeyError:
            return []

    with zipfile.ZipFile(path) as zf:
        watched = read_csv(zf, "watched.csv")
        ratings = read_csv(zf, "ratings.csv")

    rating_by_uri = {r["Letterboxd URI"]: r.get("Rating") for r in ratings if r.get("Letterboxd URI")}
    films = []
    for row in watched:
        uri = (row.get("Letterboxd URI") or "").strip()
        if not uri:
            continue
        films.append({
            "key": uri,
            "title": row.get("Name") or "",
            "year": row.get("Year") or "",
            "rating": rating_by_uri.get(uri),
            "watched": row.get("Date") or "",
            "poster": None,
            "url": uri,
        })
    return {"films": films, "source": path.name}


POSTER_CACHE = DATA / "posters.json"


def resolve_poster(uri):
    DATA.mkdir(exist_ok=True)
    cache = json.loads(POSTER_CACHE.read_text()) if POSTER_CACHE.exists() else {}
    if uri in cache:
        return cache[uri]
    if not re.match(r"^https://(boxd\.it|letterboxd\.com)/", uri):
        raise ValueError("not a letterboxd url")
    req = urllib.request.Request(uri, headers={"User-Agent": "letterboxd-ranker/0.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode()
        film_url = resp.url
    poster = None
    m = re.search(r'<script type="application/ld\+json">\s*(?:/\*.*?\*/)?\s*(\{.*?\})\s*(?:/\*.*?\*/)?\s*</script>', body, re.S)
    if m:
        try:
            poster = json.loads(m.group(1)).get("image")
        except json.JSONDecodeError:
            pass
    result = {"poster": poster, "filmUrl": film_url}
    cache[uri] = result
    POSTER_CACHE.write_text(json.dumps(cache, indent=2))
    return result


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
            elif route == "/api/import":
                username = self.query_username(parsed.query)
                self.send_json(import_export_zip(username))
            elif route == "/api/poster":
                uri = parse_qs(parsed.query).get("uri", [""])[0]
                self.send_json(resolve_poster(uri))
            elif route == "/api/state":
                username = self.query_username(parsed.query)
                path = state_path(username)
                if path.exists():
                    self.send_json(json.loads(path.read_text()))
                else:
                    self.send_json({"films": {}, "ranked": [], "skipped": []})
            else:
                self.send_json({"error": "not found"}, 404)
        except (ValueError, FileNotFoundError) as e:
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
