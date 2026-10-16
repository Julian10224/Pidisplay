"""Back-up en herstel.

Een back-up is een .tar.gz met:
  manifest.json          versie, tijdstip, of media zijn meegenomen
  signage.db             consistente kopie via de SQLite backup-API
  config/signage.ini     ter referentie (wordt bij herstel NIET automatisch teruggezet)
  media/files/...        alleen bij een back-up 'met media'
  media/renditions/...
  media/thumbs/...

Herstel gebeurt in twee stappen zodat er nooit een half teruggezette database actief is:
  1. het archief wordt gecontroleerd en klaargezet als restore-pending.tar.gz;
  2. bij de volgende start van de server (vóór de database wordt geopend) wordt eerst een
     veiligheidsback-up gemaakt en daarna de database atomair vervangen.
"""

import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time

from . import __version__

NAME_RE = re.compile(r"^signage-backup-(auto-|pre-restore-)?\d{8}-\d{6}(-media)?\.tar\.gz$")
MEDIA_MEMBER_RE = re.compile(r"^media/(files|renditions|thumbs)/[0-9a-f]{32}(\.[a-z0-9]{2,5})$")


class BackupError(Exception):
    pass


def _dir_size(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _snapshot_db(db_path, dst):
    src = sqlite3.connect(db_path, timeout=15)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def create_backup(cfg, include_media=False, prefix=""):
    os.makedirs(cfg.backups_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"signage-backup-{prefix}{stamp}{'-media' if include_media else ''}.tar.gz"
    final = os.path.join(cfg.backups_dir, name)
    part = final + ".part"

    needed = 50 * 1024 * 1024
    if include_media:
        needed += _dir_size(cfg.media_dir)
    free = shutil.disk_usage(cfg.backups_dir).free
    if free - needed < cfg.min_free_bytes // 2:
        raise BackupError("Onvoldoende vrije opslagruimte voor deze back-up.")

    with tempfile.TemporaryDirectory(dir=cfg.tmp_dir) as tmp:
        db_copy = os.path.join(tmp, "signage.db")
        _snapshot_db(cfg.db_path, db_copy)
        manifest = {"app": "signage", "version": __version__, "created": time.time(),
                    "include_media": bool(include_media)}
        try:
            with tarfile.open(part, "w:gz", compresslevel=6) as tar:
                data = json.dumps(manifest, indent=2).encode()
                ti = tarfile.TarInfo("manifest.json")
                ti.size = len(data)
                ti.mtime = int(time.time())
                tar.addfile(ti, io.BytesIO(data))
                tar.add(db_copy, arcname="signage.db")
                if os.path.exists(cfg.path):
                    try:
                        tar.add(cfg.path, arcname="config/signage.ini")
                    except OSError:
                        pass
                if include_media:
                    for sub in ("files", "renditions", "thumbs"):
                        d = os.path.join(cfg.media_dir, sub)
                        if os.path.isdir(d):
                            for f in sorted(os.listdir(d)):
                                p = os.path.join(d, f)
                                if os.path.isfile(p) and not f.endswith((".tmp", ".part")):
                                    tar.add(p, arcname=f"media/{sub}/{f}")
            with open(part, "rb") as fh:
                os.fsync(fh.fileno())
            os.replace(part, final)
        except Exception:
            try:
                os.unlink(part)
            except OSError:
                pass
            raise
    return name


def list_backups(cfg):
    out = []
    if not os.path.isdir(cfg.backups_dir):
        return out
    for f in os.listdir(cfg.backups_dir):
        if NAME_RE.match(f):
            p = os.path.join(cfg.backups_dir, f)
            st = os.stat(p)
            out.append({"name": f, "size": st.st_size, "created": st.st_mtime,
                        "include_media": f.endswith("-media.tar.gz"),
                        "automatic": "-auto-" in f, "pre_restore": "-pre-restore-" in f})
    out.sort(key=lambda b: b["created"], reverse=True)
    return out


def backup_path(cfg, name):
    if not NAME_RE.match(name or ""):
        raise BackupError("Ongeldige back-upnaam.")
    p = os.path.join(cfg.backups_dir, name)
    if not os.path.isfile(p):
        raise BackupError("Back-up niet gevonden.")
    return p


def prune(cfg, prefix, keep):
    items = [b for b in list_backups(cfg) if b["name"].startswith(f"signage-backup-{prefix}")]
    for b in items[keep:]:
        try:
            os.unlink(os.path.join(cfg.backups_dir, b["name"]))
        except OSError:
            pass


def validate_archive(path):
    """Controleer structuur en inhoud; retourneert het manifest."""
    try:
        tar = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise BackupError("Bestand is geen geldig back-uparchief (.tar.gz).")
    with tar:
        names = set()
        try:
            members = tar.getmembers()
        except (tarfile.TarError, EOFError, OSError):
            raise BackupError("Back-uparchief is beschadigd of onvolledig.")
        for m in members:
            n = m.name
            if m.isdir():
                continue
            if not m.isfile():
                raise BackupError(f"Archief bevat een niet-toegestaan item: {n}")
            if n.startswith("/") or ".." in n.split("/"):
                raise BackupError(f"Archief bevat een onveilig pad: {n}")
            if n not in ("manifest.json", "signage.db", "config/signage.ini") and not MEDIA_MEMBER_RE.match(n):
                raise BackupError(f"Archief bevat een onbekend bestand: {n}")
            names.add(n)
        if "manifest.json" not in names or "signage.db" not in names:
            raise BackupError("Archief mist manifest.json of signage.db.")
        try:
            manifest = json.load(tar.extractfile("manifest.json"))
        except (ValueError, KeyError, AttributeError):
            raise BackupError("manifest.json is ongeldig.")
        if manifest.get("app") != "signage":
            raise BackupError("Dit archief is geen back-up van dit signage-systeem.")
        with tempfile.TemporaryDirectory() as tmp:
            dbf = os.path.join(tmp, "check.db")
            with open(dbf, "wb") as out:
                shutil.copyfileobj(tar.extractfile("signage.db"), out)
            try:
                conn = sqlite3.connect(dbf)
                ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                conn.close()
            except sqlite3.DatabaseError:
                raise BackupError("Database in de back-up is beschadigd.")
            if ok != "ok" or not {"media", "playlists", "players", "settings"} <= tables:
                raise BackupError("Database in de back-up is beschadigd of onvolledig.")
    return manifest


def stage_restore(cfg, src_path):
    manifest = validate_archive(src_path)
    tmp = cfg.restore_pending_path + ".part"
    shutil.copyfile(src_path, tmp)
    os.replace(tmp, cfg.restore_pending_path)
    return manifest


def apply_pending_restore(cfg, log):
    """Wordt bij het starten van de server aangeroepen, vóór de database open gaat."""
    pending = cfg.restore_pending_path
    if not os.path.exists(pending):
        return False
    try:
        validate_archive(pending)
    except BackupError as exc:
        log.error("Klaargezette herstelactie is ongeldig en wordt overgeslagen: %s", exc)
        os.replace(pending, pending + ".rejected")
        return False
    if os.path.exists(cfg.db_path):
        try:
            name = create_backup(cfg, include_media=False, prefix="pre-restore-")
            log.info("Veiligheidsback-up vóór herstel gemaakt: %s", name)
        except Exception as exc:  # noqa: BLE001
            log.error("Veiligheidsback-up mislukt, herstel afgebroken: %s", exc)
            return False
    with tarfile.open(pending, "r:gz") as tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            if MEDIA_MEMBER_RE.match(m.name):
                sub, fname = m.name.split("/")[1:3]
                dst = os.path.join(cfg.media_dir, sub, fname)
                with open(dst + ".tmp", "wb") as out:
                    shutil.copyfileobj(tar.extractfile(m), out)
                os.replace(dst + ".tmp", dst)
        new_db = cfg.db_path + ".restore"
        with open(new_db, "wb") as out:
            shutil.copyfileobj(tar.extractfile("signage.db"), out)
            out.flush()
            os.fsync(out.fileno())
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(cfg.db_path + suffix)
        except FileNotFoundError:
            pass
    os.replace(new_db, cfg.db_path)
    os.replace(pending, pending + ".applied")
    log.info("Back-up teruggezet.")
    return True
