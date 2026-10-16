"""API: software bijwerken vanaf GitHub met een knop in de beheerwebsite.

- Controleren gebeurt door de server zelf (als gebruiker 'signage'): VERSION en
  CHANGELOG.md van de ingestelde repository/branch ophalen.
- Bijwerken start het root-programma signage-update via signage-priv (systemd-run), zodat
  de update doorloopt terwijl de server herstart. De bron staat in /etc/signage/signage.ini
  ([update]); de website kan die niet wijzigen.
- Voortgang staat in <data_dir>/update/status.json en update.log.
"""

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

from flask import jsonify

from . import __version__
from .app import ApiError, state

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
CHECK_TTL = 300
_cache = {"key": None, "at": 0.0, "result": None}


def parse_version(v):
    parts = re.findall(r"\d+", str(v or ""))[:3]
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts)) if parts else None


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return bool(latest) and latest != current
    return a > b


def release_notes(changelog, current, limit=6000):
    """De secties '## X.Y.Z — …' uit CHANGELOG.md die nieuwer zijn dan de huidige versie."""
    out, keep = [], False
    for line in changelog.splitlines():
        m = re.match(r"^##\s+(\d+\.\d+\.\d+)", line)
        if m:
            keep = is_newer(m.group(1), current)
        elif line.startswith("# "):
            keep = False
            continue
        if keep:
            out.append(line)
    text = "\n".join(out).strip()
    return text[:limit] + ("\n…" if len(text) > limit else "")


def update_source(cfg):
    p = cfg.parser
    enabled = p.get("update", "enabled", fallback="true").strip().lower() in ("1", "true", "yes", "on")
    repo = p.get("update", "repo", fallback="Julian10224/Pidisplay").strip()
    branch = p.get("update", "branch", fallback="main").strip()
    if not REPO_RE.match(repo) or not BRANCH_RE.match(branch):
        enabled = False
    return enabled, repo, branch


def _fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": f"signage/{__version__}", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(512 * 1024).decode("utf-8", "replace")


def check(cfg, force=False):
    enabled, repo, branch = update_source(cfg)
    key = (repo, branch)
    now = time.time()
    if not force and _cache["key"] == key and now - _cache["at"] < CHECK_TTL and _cache["result"]:
        return _cache["result"]
    base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    try:
        latest = _fetch(base + "VERSION").strip()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise ApiError(502, f"Controleren mislukt: GitHub is niet bereikbaar ({reason}). Heeft de Raspberry Pi internet?")
    if not parse_version(latest):
        raise ApiError(502, "Controleren mislukt: onverwacht antwoord van GitHub.")
    notes = ""
    newer = is_newer(latest, __version__)
    if newer:
        try:
            notes = release_notes(_fetch(base + "CHANGELOG.md"), __version__)
        except (urllib.error.URLError, OSError, ValueError):
            notes = ""
    result = {"latest": latest, "current": __version__, "newer": newer, "notes": notes, "checked": now}
    _cache.update(key=key, at=now, result=result)
    return result


def _service_active():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", "signage-update.service"],
                              timeout=5, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def read_status(cfg):
    d = os.path.join(cfg.data_dir, "update")
    status = None
    try:
        with open(os.path.join(d, "status.json"), encoding="utf-8") as fh:
            status = json.load(fh)
    except (OSError, ValueError):
        pass
    log = ""
    try:
        with open(os.path.join(d, "update.log"), encoding="utf-8", errors="replace") as fh:
            log = fh.read()[-20000:]
    except OSError:
        pass
    if isinstance(status, dict) and status.get("state") == "running" and not _service_active():
        # Geen lopende update meer, maar ook geen eindstatus: onderbroken (bijv. stroom weg).
        if time.time() - float(status.get("updated") or 0) > 120:
            status["state"] = "interrupted"
            status["message"] = "De update is onderbroken. Probeer het opnieuw; de actieve versie is " + __version__ + "."
    return status, log


def register(app):

    @app.get("/api/system/update")
    def update_info():
        cfg = state().cfg
        enabled, repo, branch = update_source(cfg)
        status, log = read_status(cfg)
        cached = _cache["result"] if _cache["key"] == (repo, branch) else None
        return jsonify(current=__version__, enabled=enabled, repo=repo, branch=branch,
                       priv_available=bool(cfg.priv_helper and os.path.exists(cfg.priv_helper)),
                       status=status, log=log, check=cached)

    @app.post("/api/system/update/check")
    def update_check():
        cfg = state().cfg
        enabled, _repo, _branch = update_source(cfg)
        if not enabled:
            raise ApiError(400, "Bijwerken via de website staat uit ([update] in signage.ini).")
        return jsonify(check=check(cfg, force=True))

    @app.post("/api/system/update")
    def update_start():
        cfg = state().cfg
        enabled, _repo, _branch = update_source(cfg)
        if not enabled:
            raise ApiError(400, "Bijwerken via de website staat uit ([update] in signage.ini).")
        state().priv("update", timeout=60)
        return jsonify(ok=True)
