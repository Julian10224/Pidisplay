#!/usr/bin/env python3
"""Controle van de 1.2-functies op een GEÏNSTALLEERD systeem.

Gebruik: sudo python3 tests/check_installed_v12.py --base URL --user NAAM --password WW --slides MAP
MAP: welkom.jpg, lunch.jpg, openingstijden.jpg en logo-bedrijf.png.
Maakt een playlist 'Controle 1.2' met willekeurige volgorde en een logo, en activeert die.
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

ids = {}
for f in ("welkom.jpg", "lunch.jpg", "openingstijden.jpg", "logo-bedrijf.png"):
    s, r = call("POST", "/api/media/upload", raw=open(os.path.join(args.slides, f), "rb").read(),
                headers={"X-Filename": urllib.parse.quote(f)})
    check(f"upload {f}", s in (200, 201), r)
    ids[f] = r["media"]["id"]
s, r = call("POST", "/api/playlists", {"name": f"Controle 1.2 {int(time.time())}"})
pid = r["id"]
names = ["welkom", "lunch", "openingstijden"]
s, r = call("PUT", f"/api/playlists/{pid}", {"shuffle": True, "items": [
    {"media_id": ids["welkom.jpg"], "duration": 2, "logo_media_id": ids["logo-bedrijf.png"]},
    {"media_id": ids["lunch.jpg"], "duration": 2},
    {"media_id": ids["openingstijden.jpg"], "duration": 2, "logo_media_id": ids["logo-bedrijf.png"]}]})
check("playlist met willekeurige volgorde en logo opslaan", s == 200 and r["playlist"]["shuffle"], r)
s, r = call("POST", f"/api/playlists/{pid}/activate", {"target": "default"})
check("activeren", s == 200, r)

# Volgorde volgen via de heartbeats (elke paar seconden)
seen = []
end = time.time() + 75
while time.time() < end and len(seen) < 9:
    _, st = call("GET", "/api/status")
    p = [x for x in st["players"] if x["kind"] == "local"][0]
    cur = ((p.get("status") or {}).get("current") or {}).get("name")
    if cur in names and (not seen or seen[-1] != cur):
        seen.append(cur)
    time.sleep(0.4)
print("   gezien:", seen)
check("alle items komen langs", set(seen) == set(names), seen)
check("geen item twee keer achter elkaar", all(a != b for a, b in zip(seen, seen[1:])), seen)
_, r = call("GET", "/api/media")
logo = [m for m in r["media"] if m["id"] == ids["logo-bedrijf.png"]][0]
check("media-overzicht toont gebruik als logo", any("logo" in pl["roles"] for pl in logo["playlists"]), logo["playlists"])
check("opslagoverzicht aanwezig", r["summary"]["media_bytes"] > 0 and r["summary"]["disk_free"] > 0)
print(f"\n{sum(results)}/{len(results)} geslaagd")
sys.exit(0 if all(results) else 1)
