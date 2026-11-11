# /// script
# dependencies = ["requests"]
# ///
"""Local Wellpass web app: browse studios on a map, see a studio's classes, book and auto-book.

Run:  uv run app.py --open     ->  http://127.0.0.1:8765  (log in in the browser)
Auth: Netpulse login -> SSO -> Firebase ID token (Bearer for EGYM's mwa-api).
      The login form saves email+password to .credentials.json (chmod 600, git-ignored).
"""
import argparse
import webbrowser
import datetime as dt
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / ".token.json"
CRED_FILE = HERE / ".credentials.json"
LEGACY_LOGIN = HERE / "login.txt"  # old two-line format, migrated on first run
OVERVIEW_FILE = HERE / "cache" / "overview.json"
WATCH_FILE = HERE / "watches.json"
PORT = 8765
WATCH_INTERVAL = 10  # seconds between auto-book checks

NETPULSE = "https://qualitrain.netpulse.com"
MWA = "https://mwa-api.int.api.egym.com/mwa/api"
GYMFINDER = "https://gymfinder.int.api.egym.com"
NP_HEADERS = {
    "User-Agent": "QualitrainContainer/6.6.1 (com.qualitrain.fitness; build:3080; iOS 26.5.2) Alamofire/5.9.1",
    "X-NP-API-Version": "1.5",
    "X-NP-APP-Version": "6.6.1",
    "Accept": "application/json",
}
APP_UA = "Wellpass/3080 CFNetwork/3860.600.12 Darwin/25.5.0"

# one shared HTTP session: keeps TCP/TLS connections to each host open and reuses them
HTTP = requests.Session()
HTTP.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))

# ---------------------------------------------------------------- auth

class AuthRequired(Exception):
    """No usable login yet — the browser shows the login screen."""


class LoginFailed(Exception):
    pass


def private_write(path, text):
    path.write_text(text)
    os.chmod(path, 0o600)


def load_creds():
    if CRED_FILE.exists():
        return json.loads(CRED_FILE.read_text())
    if LEGACY_LOGIN.exists():
        lines = [l.strip() for l in LEGACY_LOGIN.read_text().splitlines()]
        if len(lines) >= 2 and lines[0] and lines[1]:
            creds = {"email": lines[0], "password": lines[1]}
            private_write(CRED_FILE, json.dumps(creds))
            return creds
    return None


_lock = threading.Lock()
_tok = {"id": None, "exp": 0.0}
_fbkey = {}


def firebase_key():
    if "v" not in _fbkey:
        js = HTTP.get("https://locations.egym.com/resources/js/env.js", timeout=30).text
        _fbkey["v"] = re.search(r'firebaseOptions:\s*\{[^}]*?apiKey:\s*"([^"]+)"', js, re.S).group(1)
    return _fbkey["v"]


def save_token(refresh, id_token, expires_in):
    _tok.update(id=id_token, exp=time.time() + int(expires_in))
    private_write(TOKEN_FILE, json.dumps({"refresh_token": refresh}))


def full_login(email=None, password=None):
    """Log in with the given credentials (from the browser) or the saved ones. Returns the Netpulse profile."""
    if email is None:
        creds = load_creds()
        if not creds:
            raise AuthRequired
        email, password = creds["email"], creds["password"]
    s = requests.Session()
    r = s.post(f"{NETPULSE}/np/exerciser/login", headers=NP_HEADERS, timeout=30,
               data={"username": email, "password": password, "relogin": "false"})
    if r.status_code in (400, 401, 403):
        raise LoginFailed("Wellpass didn't accept that email and password.")
    r.raise_for_status()
    me = r.json()
    uuid = me["uuid"]
    sso = s.get(f"{NETPULSE}/np/exerciser/{uuid}/externalAccount/egym/qltLocations/sso", headers=NP_HEADERS, timeout=30)
    sso.raise_for_status()
    code = urllib.parse.unquote(re.search(r"[?&]code=([^&]+)", sso.json()["url"]).group(1))
    fb = HTTP.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={firebase_key()}",
                       json={"token": code, "returnSecureToken": True}, timeout=30)
    fb.raise_for_status()
    fb = fb.json()
    save_token(fb["refreshToken"], fb["idToken"], fb["expiresIn"])
    return me


def refresh_login():
    refresh = json.loads(TOKEN_FILE.read_text())["refresh_token"]
    r = HTTP.post(f"https://securetoken.googleapis.com/v1/token?key={firebase_key()}", timeout=30,
                      data={"grant_type": "refresh_token", "refresh_token": refresh})
    r.raise_for_status()
    r = r.json()
    save_token(r["refresh_token"], r["id_token"], r["expires_in"])


def id_token(force=False):
    with _lock:
        if not force and _tok["id"] and time.time() < _tok["exp"] - 120:
            return _tok["id"]
        try:
            if force or not TOKEN_FILE.exists():
                raise FileNotFoundError
            refresh_login()
        except Exception:
            try:
                full_login()
            except LoginFailed:  # saved password no longer valid → ask again in the browser
                raise AuthRequired
        return _tok["id"]


def logged_in():
    return CRED_FILE.exists() or LEGACY_LOGIN.exists() or TOKEN_FILE.exists()


def mwa(method, path, **kw):
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {id_token(force=attempt > 0)}", "Accept": "application/json",
                   "User-Agent": APP_UA, "Accept-Language": "de-DE"}
        r = HTTP.request(method, MWA + path, headers=headers, timeout=30, **kw)
        if r.status_code != 401:
            return r
    return r


# ---------------------------------------------------------------- studios (public gym finder)

_gyms = []


def load_gyms():
    if not OVERVIEW_FILE.exists() or time.time() - OVERVIEW_FILE.stat().st_mtime > 86400:
        OVERVIEW_FILE.parent.mkdir(exist_ok=True)
        r = HTTP.get(f"{GYMFINDER}/v1/gym/overview", params={"searchFilter": "wellpass", "limit": 20000}, timeout=120)
        r.raise_for_status()
        OVERVIEW_FILE.write_bytes(r.content)
    _gyms[:] = [
        {"gymId": g["g"], "slug": g["slug"], "name": g["a"], "lat": g["lat"], "lng": g["lng"],
         "type": g.get("st"), "activities": [a["a"] for a in g.get("ac", [])],
         "inApp": bool((g.get("cbc") or {}).get("e")),
         "image": re.sub(r"_t$", "", g["bimg"]) if g.get("bimg") else None,  # append _t / _s / _m for size
         "hours": g.get("ov2"), "plusOne": "PLUS_1" in (g.get("mt") or [])}
        for g in json.loads(OVERVIEW_FILE.read_text())["gyms"]
    ]
    _by_id.clear()
    _by_id.update({g["gymId"]: g for g in _gyms})


_by_id = {}
_activities = {}  # ENUM -> English label, from the gym finder's filter vocabulary


def load_activities():
    r = HTTP.get(f"{GYMFINDER}/v1/search-filter/wellpass", timeout=30)
    r.raise_for_status()
    for grp in r.json():
        for f in grp.get("filters") or []:
            if f["slug"].startswith("activity-"):
                _activities[f["slug"][9:].upper().replace("-", "_")] = f["text"].get("en") or f["slug"]


def studio_detail(slug):
    r = HTTP.get(f"{GYMFINDER}/v1/gym", params={"slug": slug}, timeout=30)
    r.raise_for_status()
    d = r.json()[0]
    return {
        "gymId": d.get("gymUUID"), "slug": d.get("slug"), "address": d.get("address"), "description": d.get("description"), "website": d.get("website"),
        "phone": d.get("phone"), "offer": d.get("qualitrainOfferDescription"), "hours": d.get("openingHoursV2"),
        "images": [i["imageURL"] for i in d.get("gymImages") or []],
    }


def km(lat1, lng1, lat2, lng2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lng2 - lng1) * p)) / 2
    return 12742 * math.asin(math.sqrt(a))


def nearby(lat, lng, radius, q="", in_app=True, limit=3000):
    q = q.lower()
    out = []
    for g in _gyms:
        if in_app and not g["inApp"]:
            continue
        if q and q not in g["name"].lower():
            continue
        d = km(lat, lng, g["lat"], g["lng"])
        if d <= radius:
            out.append({**g, "km": round(d, 2)})
    out.sort(key=lambda g: g["km"])
    return out[:limit]


_scache = {}  # (gym, from, to) -> (time, sessions); short cache so day/tab switches are instant
SESSION_TTL = 120


def sessions(gym_id, frm, to):
    key = (gym_id, frm, to)
    hit = _scache.get(key)
    if hit and time.time() - hit[0] < SESSION_TTL:
        return hit[1]
    r = mwa("GET", "/class-booking/v1/class-session", params={"gymId": gym_id, "from": frm, "to": to, "status": "ACTIVE"})
    data = [] if r.status_code == 404 else (r.raise_for_status() or r.json())
    _scache[key] = (time.time(), data)
    return data


def slim_session(s):
    g = s.get("gym") or {}
    cp = s.get("cancellationPolicy") or {}
    return {
        "id": s["id"], "name": s.get("name"), "description": s.get("description"),
        "start": s.get("startDateTime"), "end": s.get("endDateTime"),
        "bookingWindowStart": s.get("bookingWindowStart"), "bookingWindowEnd": s.get("bookingWindowEnd"),
        "capacity": s.get("capacity"), "booked": s.get("booked"),
        "waitlistCapacity": s.get("waitlistCapacity"), "waitlistBooked": s.get("waitlistBooked"),
        "instructor": (s.get("instructor") or {}).get("fullName"), "room": (s.get("room") or {}).get("name"),
        "gymId": g.get("serverGymsId"), "gymName": g.get("name"), "gymSlug": g.get("slug"),
        "bookingFlowType": g.get("bookingFlowType"),
        "cancellationWindowMs": cp.get("cancellationWindow"), "lateCancelFee": cp.get("lateCancelFee"),
        "noShowFee": cp.get("noShowFee"), "currency": cp.get("currencyCode"),
    }


def with_gym(c):
    g = _by_id.get(c.get("gymId")) or {}
    return {**c, "gymImage": g.get("image"), "lat": g.get("lat"), "lng": g.get("lng")}


def bookings(frm, to):
    r = mwa("GET", "/class-booking/v1/booking", params={"from": frm, "to": to})
    r.raise_for_status()
    return [{"id": b["id"], "status": b.get("status"), "session": with_gym(slim_session(b["classSession"])),
             "cancellationPolicy": b.get("cancellationPolicy")} for b in r.json()]


# ---------------------------------------------------------------- auto-book watcher

_wlock = threading.Lock()


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_watches():
    return json.loads(WATCH_FILE.read_text()) if WATCH_FILE.exists() else []


def save_watches(ws):
    WATCH_FILE.write_text(json.dumps(ws, indent=1))


def notify(msg):
    print("  notify:", msg)
    subprocess.run(["osascript", "-e", "on run argv\ndisplay notification (item 1 of argv) with title \"Wellpass\" sound name \"Glass\"\nend run", msg],
                   check=False, capture_output=True)


def check_watch(w):
    w["lastChecked"] = now_iso()
    r = mwa("GET", f"/class-booking/v1/class-session/{w['id']}")
    if r.status_code == 404:
        w.update(status="gone", note="Class no longer exists")
        return
    r.raise_for_status()
    s = r.json()
    w.update(capacity=s.get("capacity"), booked=s.get("booked"))
    now = dt.datetime.now(dt.timezone.utc)
    if parse_iso(s["startDateTime"]) <= now:
        w.update(status="expired", note="Class started before a spot opened")
        return
    if s.get("status") not in (None, "ACTIVE"):
        w.update(status="gone", note=f"Class is {s.get('status')}")
        notify(f"{w['name']} was {str(s.get('status')).lower()} — auto-book stopped")
        return
    if s.get("bookingWindowStart") and parse_iso(s["bookingWindowStart"]) > now:
        w["note"] = "Waiting for booking to open"
        return
    cap, booked = s.get("capacity"), s.get("booked") or 0
    if cap is not None and booked >= cap:
        w["note"] = "Full — waiting for a spot"
        return
    b = mwa("POST", "/class-booking/v1/booking", json={"classSessionId": w["id"]})
    print("  auto-book", w["name"], "->", b.status_code, b.text[:200])
    if b.ok:
        w.update(status="booked", bookingId=b.json().get("id"), bookedAt=now_iso(), note="Booked automatically")
        start = parse_iso(w["start"]).astimezone().strftime("%a %d.%m %H:%M")
        notify(f"Booked {w['name']} — {start} at {w['gymName']}")
        return
    w["failures"] = w.get("failures", 0) + 1
    w["note"] = f"Booking attempt failed ({b.status_code}): {b.text[:160]}"
    if w["failures"] >= 5:
        w["status"] = "failed"
        notify(f"Auto-book for {w['name']} failed 5 times — stopped")


def watcher():
    while True:
        if not logged_in():
            time.sleep(WATCH_INTERVAL)
            continue
        with _wlock:
            todo = [w for w in load_watches() if w.get("status") == "watching"]
        for w in todo:
            try:
                check_watch(w)
            except Exception as e:
                w.update(lastChecked=now_iso(), note=f"Check failed: {e!r}"[:200])
            with _wlock:  # merge back; the user may have removed it meanwhile
                ws = load_watches()
                for i, cur in enumerate(ws):
                    if cur["id"] == w["id"] and cur.get("status") == "watching":
                        ws[i] = w
                save_watches(ws)
        time.sleep(WATCH_INTERVAL)


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s %s" % (self.command, self.path))

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/status":  # handy for agents: curl it to see if the user still has to log in
                creds = load_creds() if logged_in() else None
                return self.send(200, {"loggedIn": logged_in(), "email": (creds or {}).get("email"),
                                       "name": (creds or {}).get("name"), "studios": len(_gyms),
                                       "autoBooking": sum(w.get("status") == "watching" for w in load_watches())})
            if u.path == "/api/studios":
                return self.send(200, nearby(float(q["lat"]), float(q["lng"]), float(q.get("radius", 5)),
                                             q.get("q", ""), q.get("inApp", "1") == "1"))
            if u.path == "/api/classes":  # by gymId, or by slug so the page can load it in parallel with /api/studio
                gym = _by_id.get(q.get("gymId")) or next((g for g in _gyms if g["slug"] == q.get("slug")), None)
                gym_id = q.get("gymId") or (gym or {}).get("gymId")
                if not gym_id or (gym and not gym["inApp"]):
                    return self.send(200, [])  # studio doesn't take in-app bookings → no Wellpass request
                return self.send(200, [with_gym(slim_session(s)) for s in sessions(gym_id, q["from"], q["to"])])
            if u.path == "/api/studio":
                return self.send(200, {**(next((g for g in _gyms if g["slug"] == q["slug"]), {})), **studio_detail(q["slug"])})
            if u.path == "/api/activities":
                return self.send(200, _activities)
            if u.path == "/api/watches":
                with _wlock:
                    return self.send(200, load_watches())
            if u.path == "/api/bookings":
                return self.send(200, bookings(q["from"], q["to"]))
            if u.path == "/api/geocode":
                r = HTTP.get("https://nominatim.openstreetmap.org/search", timeout=20,
                                 params={"q": q["q"], "format": "json", "limit": 1},
                                 headers={"User-Agent": "wellpass-local-browser/0.1"})
                hit = (r.json() or [None])[0]
                return self.send(200, {"lat": float(hit["lat"]), "lng": float(hit["lon"]), "label": hit["display_name"]} if hit else None)
            self.send(404, {"error": "not found"})
        except AuthRequired:
            self.send(401, {"error": "login_required"})
        except requests.HTTPError as e:
            self.send(502, {"error": f"upstream {e.response.status_code}", "detail": e.response.text[:500]})
        except Exception as e:
            self.send(500, {"error": repr(e)})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            if self.path == "/api/login":
                email, password = (body.get("email") or "").strip(), body.get("password") or ""
                if not email or not password:
                    return self.send(400, {"error": "Enter your Wellpass email and password."})
                with _lock:
                    try:
                        me = full_login(email, password)
                    except LoginFailed as e:
                        return self.send(401, {"error": str(e)})
                name = " ".join(filter(None, [me.get("firstName"), me.get("lastName")])) or None
                private_write(CRED_FILE, json.dumps({"email": email, "password": password, "name": name}))
                LEGACY_LOGIN.unlink(missing_ok=True)
                print(f"  logged in as {email}")
                return self.send(200, {"loggedIn": True, "email": email, "name": name})
            if self.path == "/api/logout":
                with _lock:
                    for f in (CRED_FILE, TOKEN_FILE, LEGACY_LOGIN):
                        f.unlink(missing_ok=True)
                    _tok.update(id=None, exp=0.0)
                _scache.clear()
                print("  logged out")
                return self.send(200, {"loggedIn": False})
            if self.path in ("/api/watch", "/api/unwatch"):
                with _wlock:
                    ws = [w for w in load_watches() if w["id"] != body["id"]]
                    if self.path == "/api/watch":
                        c = body
                        ws.append({"id": c["id"], "name": c.get("name"), "gymName": c.get("gymName"), "gymSlug": c.get("gymSlug"),
                                   "gymId": c.get("gymId"), "gymImage": c.get("gymImage"), "start": c.get("start"), "end": c.get("end"),
                                   "status": "watching", "created": now_iso(), "note": "Starting…"})
                    save_watches(ws)
                return self.send(200, ws)
            _scache.clear()  # booking counts changed
            if self.path == "/api/book":
                r = mwa("POST", "/class-booking/v1/booking", json={"classSessionId": body["classSessionId"]})
            elif self.path == "/api/cancel":
                r = mwa("POST", f"/class-booking/v1/booking/{body['bookingId']}/cancel")
            else:
                return self.send(404, {"error": "not found"})
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text[:500]}
            print("  ->", r.status_code, json.dumps(data)[:300])
            self.send(200 if r.ok else 502, data if r.ok else {"error": f"upstream {r.status_code}", "detail": data})
        except AuthRequired:
            self.send(401, {"error": "login_required"})
        except Exception as e:
            self.send(500, {"error": repr(e)})


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description="Local Wellpass web app")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--open", action="store_true", help="open the app in the default browser once it's ready")
    args = ap.parse_args()
    print("loading studios…")
    load_gyms()
    load_activities()
    print(f"{len(_gyms)} studios, {sum(g['inApp'] for g in _gyms)} with in-app booking")
    if logged_in():
        try:
            id_token()
            print("logged in with saved credentials")
        except Exception as e:
            print(f"saved login didn't work ({e!r}) — log in again in the browser")
    threading.Thread(target=watcher, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        sys.exit(f"port {args.port} is busy — is the app already running? Try --port {args.port + 1}")
    print(f"ready: {url}" + ("" if logged_in() else "  ← open it and log in with your Wellpass account"))
    if args.open:
        webbrowser.open(url)
    server.serve_forever()
