#!/usr/bin/env python3
"""Controle van de 1.1-functies op een GEÏNSTALLEERD systeem (na een upgrade).

Gebruik: sudo python3 tests/check_installed_v11.py --base URL --user NAAM --password WW --slides MAP
MAP: welkom.jpg, lunch.jpg, overgang.mp4 (korte H.264-video) en aanbieding.zip (HTML-project).
Maakt een playlist 'Controle 1.1' aan en activeert die als standaardplaylist.
"""

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:80")
ap.add_argument("--user", required=True)
ap.add_argument("--password", required=True)
ap.add_argument("--slides", required=True)
args = ap.parse_args()
B = args.base.rstrip("/")
o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
H = {}
results = []


def call(m, p, body=None, raw=None, headers=None):
    h = dict(H, **(headers or {}))
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        h["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(B + p, data=data, method=m, headers=h)
    try:
        with o.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


call("POST", "/api/auth/login", {"username": args.user, "password": args.password})
H["X-CSRF-Token"] = call("GET", "/api/auth/state")[1]["csrf"]

s, r = call("GET", "/api/backups")
check("back-up van vóór de migratie aanwezig", any(b.get("pre_migration") for b in r["backups"]))
s, r = call("GET", "/api/playlists")
check("bestaande playlists behouden na upgrade", len(r["playlists"]) >= 1, r)

ids = {}
for f in ("welkom.jpg", "lunch.jpg", "overgang.mp4", "aanbieding.zip"):
    s, r = call("POST", "/api/media/upload", raw=open(os.path.join(args.slides, f), "rb").read(),
                headers={"X-Filename": urllib.parse.quote(f)})
    check(f"upload {f}", s in (200, 201), r)
    ids[f] = r["media"]["id"]
s, r = call("POST", "/api/media/builtin", {"page": "clock"})
check("lokale pagina toevoegen", s == 201, r)
ids["klok"] = r["media"]["id"]

name = f"Controle 1.1 {int(time.time())}"
s, r = call("POST", "/api/playlists", {"name": name})
pid = r["id"]
s, r = call("PUT", f"/api/playlists/{pid}", {
    "transition_enabled": True, "transition_media_id": ids["overgang.mp4"],
    "items": [{"media_id": ids["welkom.jpg"], "duration": 4, "scale_mode": "fill"},
              {"media_id": ids["aanbieding.zip"], "duration": 3},
              {"media_id": ids["klok"], "duration": 3, "transition_mode": "none"},
              {"media_id": ids["lunch.jpg"], "duration": 2}]})
check("playlist met overgangen opslaan", s == 200, r)
s, r = call("POST", f"/api/playlists/{pid}/activate", {"target": "default"})
check("playlist activeren", s == 200, r)

seen, renderer, errors, hw = [], None, [], None
end = time.time() + 120
while time.time() < end:
    _, st = call("GET", "/api/status")
    p = [x for x in st["players"] if x["kind"] == "local"][0]
    stt = p.get("status") or {}
    renderer = stt.get("renderer") or renderer
    hw = stt.get("hwdec") or hw
    errors = stt.get("errors") or errors
    cur = (stt.get("current") or {}).get("name")
    if cur and (not seen or seen[-1] != cur):
        seen.append(cur)
    if "welkom" in seen and "lunch" in seen and hw is not None:
        break
    time.sleep(0.5)
print("   weergave:", renderer, "| gezien:", seen)
check("overgangsvideo wordt afgespeeld", any(n.startswith("Overgang") for n in seen), seen)
check("foto's spelen in volgorde", "welkom" in seen and "lunch" in seen, seen)
if renderer == "mpv":
    check("HTML wordt in mpv-weergave overgeslagen met melding", any("browserweergave" in e for e in errors), errors)
    check("decoder gemeld (hwdec)", hw is not None, hw)
else:
    check("HTML-pagina's worden getoond", "aanbieding" in seen or "Klok" in seen, seen)
print(f"\n{sum(results)}/{len(results)} geslaagd")
sys.exit(0 if all(results) else 1)
