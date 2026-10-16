#!/usr/bin/env python3
"""Controle van de live bediening (1.3) op een GEÏNSTALLEERD systeem.

Gebruik: sudo python3 tests/check_installed_v13.py --base URL --user NAAM --password WW --slides MAP
MAP: welkom.jpg, lunch.jpg en openingstijden.jpg. Maakt een playlist 'Controle 1.3' met lange
duren, activeert die en klikt er via de API doorheen zoals de knoppen in de website doen.
Meet hoe snel het scherm volgt en hoe snel de website dat ziet.
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


def local():
    return [x for x in call("GET", "/api/status")[1]["players"] if x["kind"] == "local"][0]


def wait(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.1)
    return None


call("POST", "/api/auth/login", {"username": args.user, "password": args.password})
H["X-CSRF-Token"] = call("GET", "/api/auth/state")[1]["csrf"]

files = ("welkom.jpg", "lunch.jpg", "openingstijden.jpg")
ids = []
for f in files:
    s, r = call("POST", "/api/media/upload", raw=open(os.path.join(args.slides, f), "rb").read(),
                headers={"X-Filename": urllib.parse.quote(f)})
    ids.append(r["media"]["id"])
s, r = call("POST", "/api/playlists", {"name": f"Controle 1.3 {int(time.time())}"})
pid = r["id"]
s, r = call("PUT", f"/api/playlists/{pid}", {"items": [{"media_id": m, "duration": 300} for m in ids]})
items = r["playlist"]["items"]
call("POST", f"/api/playlists/{pid}/activate", {"target": "default"})


def current():
    st = local().get("status") or {}
    return (st.get("current") or {}).get("item_id")


check("player online met live bediening", wait(lambda: local()["online"] and (local().get("status") or {}).get("control"), 40))
check("eerste item op het scherm", wait(lambda: current() == items[0]["item_id"], 40), current())
time.sleep(2)

timings = []
for action, extra, want in [("next", {}, 1), ("next", {}, 2), ("prev", {}, 1), ("goto", {"item_id": items[0]["item_id"]}, 0),
                            ("next", {}, 1)]:
    t0 = time.time()
    s, r = call("POST", "/api/players/local/control", {"action": action, **extra})
    ok = s == 200 and wait(lambda: current() == items[want]["item_id"], 6)
    timings.append(time.time() - t0)
    check(f"{action} -> item {want + 1} zichtbaar in de website", ok, (s, r, current()))
st = local()["status"]
check("laatste opdracht uitgevoerd", (st["control"]["last"] or {}).get("result") == "ok", st["control"])
print("   tijd van klik tot zichtbaar in de website: " + ", ".join(f"{t:.2f}s" for t in timings))
check("binnen 3 seconden zichtbaar", max(timings) < 3, timings)
s, r = call("POST", "/api/players/local/control", {"action": "goto", "item_id": 1})
check("onbekend item wordt geweigerd", s == 409, (s, r))
print(f"\n{sum(results)}/{len(results)} geslaagd")
sys.exit(0 if all(results) else 1)
