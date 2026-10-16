#!/usr/bin/env python3
"""Integratietest tegen een GEÏNSTALLEERD systeem (server + player als services).

Gebruik op een testmachine of op de Pi zelf, direct na installatie (nog geen beheerder):
    sudo python3 tests/check_installed.py --base http://127.0.0.1:80 --slides MAP

MAP moet bevatten: welkom.jpg, lunch.jpg, bedrijfsfilm.mp4, kapot.mp4.
kapot.mp4 wordt na het uploaden op de opslag beschadigd om een corrupt bestand
na te bootsen. Let op: deze test maakt een beheerder 'testbeheer' aan; gebruik hem
niet op een systeem dat al in gebruik is.
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
ap.add_argument("--slides", required=True)
ap.add_argument("--data", default="/var/lib/signage")
args = ap.parse_args()
BASE = args.base.rstrip("/")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
CSRF = {"t": None}
results = []


def call(method, path, body=None, raw=None, headers=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
        h.setdefault("Content-Type", "application/octet-stream")
    if CSRF["t"] and method != "GET":
        h["X-CSRF-Token"] = CSRF["t"]
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with opener.open(req, timeout=60) as r:
            txt = r.read()
            return r.status, (json.loads(txt) if txt[:1] in (b"{", b"[") else txt)
    except urllib.error.HTTPError as e:
        txt = e.read()
        try:
            return e.code, json.loads(txt)
        except ValueError:
            return e.code, txt


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("OK   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def wait(fn, timeout=40, interval=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


def login_state():
    _, st = call("GET", "/api/auth/state")
    CSRF["t"] = st.get("csrf")
    return st


# 1. setup ------------------------------------------------------------------
code = open(os.path.join(args.data, "setup_code")).read().strip()
s, _ = call("POST", "/api/auth/setup", {"setup_code": code, "username": "testbeheer", "password": "geheim1234"})
check("beheerder aanmaken met setup-code", s == 200, s)
login_state()

# 2. uploads ------------------------------------------------------------------
ids = {}
for f in ("welkom.jpg", "lunch.jpg", "bedrijfsfilm.mp4", "kapot.mp4"):
    raw = open(os.path.join(args.slides, f), "rb").read()
    s, r = call("POST", "/api/media/upload", raw=raw, headers={"X-Filename": urllib.parse.quote(f)})
    check(f"upload {f}", s in (200, 201), r)
    ids[f] = r["media"]["id"]
# kapot.mp4 beschadigen op de opslag (simuleert corrupte SD-kaartsector)
broken = os.path.join(args.data, "media", "files", ids["kapot.mp4"] + ".mp4")
with open(broken, "r+b") as fh:
    fh.write(os.urandom(4096))
    fh.truncate(6000)

# 3. playlist -----------------------------------------------------------------
s, r = call("POST", "/api/playlists", {"name": "Integratietest"})
pid = r["id"]
items = [{"media_id": ids["welkom.jpg"], "duration": 3}, {"media_id": ids["kapot.mp4"]},
         {"media_id": ids["bedrijfsfilm.mp4"]}, {"media_id": ids["lunch.jpg"], "duration": 3}]
s, _ = call("PUT", f"/api/playlists/{pid}", {"items": items})
check("playlist opslaan", s == 200)
s, _ = call("POST", f"/api/playlists/{pid}/activate", {"target": "default"})
check("playlist activeren", s == 200)


def local():
    _, st = call("GET", "/api/status")
    return [p for p in st["players"] if p["kind"] == "local"][0], st


seen = []
end = time.time() + 45
while time.time() < end:
    p, _ = local()
    cur = (p.get("status") or {}).get("current")
    if cur and (not seen or seen[-1] != cur["name"]):
        seen.append(cur["name"])
    if seen.count("welkom") >= 2:
        break
    time.sleep(0.5)
order = [n for n in seen if n in ("welkom", "bedrijfsfilm", "lunch", "kapot")]
check("player speelt afbeeldingen en video in volgorde", "welkom" in order and "bedrijfsfilm" in order and "lunch" in order,
      order)
p, st = local()
errs = (p.get("status") or {}).get("errors") or []
check("kapot bestand overgeslagen en gemeld", any("kapot" in e for e in errs), errs)
check("dashboard meldt de fout", any("kapot" in w["text"] for w in st["warnings"]), st["warnings"])

# 4. systeemacties via sudo + signage-priv ------------------------------------------
s, r = call("POST", "/api/system/timezone", {"timezone": "Europe/Amsterdam"})
check("tijdzone instellen via signage-priv", s == 200, r)
s, r = call("POST", "/api/system/hostname", {"hostname": "Bad Name"})
check("ongeldige hostnaam geweigerd", s == 400, r)
s, r = call("POST", "/api/system/player", {"action": "restart"})
check("player herstarten via signage-priv", s == 200, r)
time.sleep(3)
check("player na herstart weer online", wait(lambda: local()[0]["online"] and (local()[0]["status"] or {}).get("current"), 40))

# 5. back-up en herstel via de webinterface (incl. automatische serverherstart) ---------
s, r = call("POST", "/api/backups", {"include_media": False})
check("back-up maken", s == 201, r)
bk = r["name"]
call("POST", "/api/playlists", {"name": "Na de back-up"})
s, r = call("POST", f"/api/backups/restore?name={urllib.parse.quote(bk)}")
check("herstel klaarzetten + herstart aanvragen", s == 200, r)
time.sleep(4)


def relogin():
    try:
        s, _ = call("POST", "/api/auth/login", {"username": "testbeheer", "password": "geheim1234"})
        if s == 200:
            login_state()
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


check("server komt terug na herstel", wait(relogin, 40))
_, r = call("GET", "/api/playlists")
names = [x["name"] for x in r["playlists"]]
check("herstelde database actief (playlist van na de back-up is weg)", names == ["Integratietest"], names)

# 6. upload-grenzen ------------------------------------------------------------------
s, r = call("POST", "/api/media/upload", raw=b"<?php echo 1; ?>", headers={"X-Filename": "shell.php"})
check("gevaarlijk bestandstype geweigerd", s == 415, r)
s, r = call("POST", "/api/media/upload", raw=b"GIF89a....", headers={"X-Filename": "nep.jpg"})
check("ongeldige afbeelding geweigerd", s == 422, r)
check("geen restanten na mislukte upload", os.listdir(os.path.join(args.data, "media", ".incoming")) == [])

print()
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} geslaagd")
sys.exit(1 if failed else 0)
