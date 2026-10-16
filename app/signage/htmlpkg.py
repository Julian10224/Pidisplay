"""HTML-content: veilige verwerking van geüploade HTML-bestanden/ZIP-projecten en de
whitelist van ingebouwde lokale pagina's.

Elk HTML-project krijgt een eigen map (media/html/<id>/). Bij een ZIP:
- geen absolute paden, geen '..', geen backslashes, geen symlinks, geen versleuteling;
- alleen toegestane bestandstypen (webcontent en media);
- limieten op aantal bestanden, totale grootte en compressieverhouding (ZIP-bom);
- uitpakken gebeurt in een tijdelijke map; pas als alles klopt wordt die op zijn plek gezet;
- er moet een duidelijk startbestand zijn (index.html, of de enige .html in de hoofdmap).

Deze module heeft geen Flask nodig; de player gebruikt hem ook om projecten uit te pakken
die hij van de server downloadt.
"""

import hashlib
import os
import posixpath
import re
import shutil
import stat
import tempfile
import zipfile

BUILTIN_PAGES = {
    "clock": {"name": "Klok", "file": "clock.html",
              "description": "Grote digitale klok met datum (lokale tijd van de Pi)."},
    "welcome": {"name": "Welkom", "file": "welcome.html",
                "description": "Systeemnaam met datum en tijd."},
}

ALLOWED_EXTS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".xml", ".csv", ".map", ".webmanifest",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".m4v", ".mp3", ".ogg", ".oga", ".wav", ".m4a", ".vtt",
}
IGNORED = ("__MACOSX/",)
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
MAX_FILES = 2000
MAX_TOTAL = 300 * 1024 * 1024
MAX_SINGLE_HTML = 20 * 1024 * 1024
MAX_RATIO = 200

TEXT_EXTS = {".html", ".htm", ".css", ".js", ".mjs"}
EXTERNAL_RE = re.compile(
    r"""(?:src|href|action|poster)\s*=\s*["']\s*(?:https?:)?//([^"'/\s>]+)"""
    r"""|url\(\s*["']?\s*(?:https?:)?//([^"')\s/]+)"""
    r"""|@import\s+["'](?:https?:)?//([^"'/\s]+)"""
    r"""|fetch\(\s*["'](?:https?:)?//([^"'/\s]+)""", re.I)
ROOT_PATH_RE = re.compile(r"""(?:src|href)\s*=\s*["']/(?!/)[^"']*["']""", re.I)


class HtmlError(Exception):
    """Ongeldig HTML-project (melding is voor de gebruiker bedoeld)."""


def _safe_member(name):
    """Genormaliseerd, veilig relatief pad, of None om over te slaan; gooit HtmlError."""
    if name.endswith("/"):
        return None
    if any(name.startswith(p) for p in IGNORED) or posixpath.basename(name) in IGNORED_NAMES:
        return None
    if "\\" in name or name.startswith("/") or "\x00" in name:
        raise HtmlError(f"Ongeldig pad in ZIP: {name}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts) or re.match(r"^[A-Za-z]:", parts[0]):
        raise HtmlError(f"Onveilig pad in ZIP: {name}")
    norm = posixpath.normpath(name)
    if norm != name or norm.startswith(".."):
        raise HtmlError(f"Onveilig pad in ZIP: {name}")
    return norm


def _scan_text(path, rel, hosts, rootpaths):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(2 * 1024 * 1024)
    except OSError:
        return
    for m in EXTERNAL_RE.finditer(text):
        host = next(g for g in m.groups() if g)
        hosts.add(host.lower())
    if rel.lower().endswith((".html", ".htm")) and ROOT_PATH_RE.search(text):
        rootpaths.add(rel)


def _warnings(hosts, rootpaths):
    out = []
    if hosts:
        listed = ", ".join(sorted(hosts)[:5]) + (" …" if len(hosts) > 5 else "")
        out.append(f"De pagina laadt externe bronnen ({listed}). Zonder internet ontbreken die; "
                   "de rest van de playlist speelt gewoon door.")
    if rootpaths:
        out.append("De pagina gebruikt absolute paden (beginnend met '/'). Gebruik relatieve paden "
                   "(bijv. 'css/stijl.css'), anders worden die bestanden niet gevonden.")
    return out


def extract_project(src_path, original_name, dest_dir):
    """Pak een .html/.htm of .zip uit naar dest_dir. Retourneert info-dict."""
    ext = os.path.splitext(original_name or "")[1].lower()
    parent = os.path.dirname(dest_dir)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".tmp-", dir=parent)
    hosts, rootpaths = set(), set()
    try:
        if ext in (".html", ".htm"):
            size = os.path.getsize(src_path)
            if size > MAX_SINGLE_HTML:
                raise HtmlError("HTML-bestand is te groot (maximaal 20 MB).")
            with open(src_path, "rb") as fh:
                head = fh.read(65536).decode("utf-8", "replace").lower()
            if "<" not in head:
                raise HtmlError("Bestand bevat geen HTML.")
            entry = "index.html"
            shutil.copyfile(src_path, os.path.join(tmp, entry))
            _scan_text(os.path.join(tmp, entry), entry, hosts, rootpaths)
            files, total = 1, size
        elif ext == ".zip":
            try:
                zf = zipfile.ZipFile(src_path)
            except (zipfile.BadZipFile, OSError):
                raise HtmlError("Bestand is geen geldig ZIP-archief.")
            with zf:
                members, total, bad_types = [], 0, []
                for info in zf.infolist():
                    rel = _safe_member(info.filename)
                    if rel is None:
                        continue
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise HtmlError(f"ZIP bevat een snelkoppeling (symlink): {rel}")
                    if info.flag_bits & 0x1:
                        raise HtmlError("Versleutelde ZIP-bestanden worden niet ondersteund.")
                    if os.path.splitext(rel)[1].lower() not in ALLOWED_EXTS:
                        bad_types.append(rel)
                        continue
                    total += info.file_size
                    if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
                        raise HtmlError(f"Verdachte compressieverhouding in {rel} (mogelijke ZIP-bom).")
                    members.append((info, rel))
                if bad_types:
                    shown = ", ".join(bad_types[:5]) + (" …" if len(bad_types) > 5 else "")
                    raise HtmlError(f"ZIP bevat niet-toegestane bestandstypen: {shown}")
                if not members:
                    raise HtmlError("ZIP bevat geen bestanden.")
                if len(members) > MAX_FILES:
                    raise HtmlError(f"ZIP bevat te veel bestanden (maximaal {MAX_FILES}).")
                if total > MAX_TOTAL:
                    raise HtmlError(f"Uitgepakt project is te groot (maximaal {MAX_TOTAL // (1024 * 1024)} MB).")

                # Eén gemeenschappelijke hoofdmap zonder index.html in de root? Dan die map als root.
                names = [rel for _i, rel in members]
                prefix = ""
                tops = {n.split("/", 1)[0] for n in names}
                if len(tops) == 1 and all("/" in n for n in names):
                    prefix = next(iter(tops)) + "/"
                rels = [n[len(prefix):] for n in names]
                root_html = [r for r in rels if "/" not in r and r.lower().endswith((".html", ".htm"))]
                if "index.html" in rels:
                    entry = "index.html"
                elif "index.htm" in rels:
                    entry = "index.htm"
                elif len(root_html) == 1:
                    entry = root_html[0]
                else:
                    raise HtmlError("Geen startbestand gevonden: zet een index.html in de hoofdmap van de ZIP.")

                real_tmp = os.path.realpath(tmp)
                written = 0
                for info, rel in members:
                    target_rel = rel[len(prefix):]
                    target = os.path.realpath(os.path.join(tmp, *target_rel.split("/")))
                    if os.path.commonpath([real_tmp, target]) != real_tmp:
                        raise HtmlError(f"Onveilig pad in ZIP: {rel}")
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        remaining = info.file_size
                        while True:
                            block = src.read(1024 * 1024)
                            if not block:
                                break
                            remaining -= len(block)
                            written += len(block)
                            if remaining < 0 or written > MAX_TOTAL:
                                raise HtmlError("ZIP-inhoud wijkt af van de opgegeven grootte.")
                            out.write(block)
                    if os.path.splitext(target_rel)[1].lower() in TEXT_EXTS:
                        _scan_text(target, target_rel, hosts, rootpaths)
                files = len(members)
        else:
            raise HtmlError("Gebruik een .html-bestand of een .zip met een HTML-project.")

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir)
        os.replace(tmp, dest_dir)
        tmp = None
        return {"entry": entry, "files": files, "size": total, "warnings": _warnings(hosts, rootpaths)}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, RuntimeError) as exc:
        raise HtmlError(f"ZIP kon niet worden gelezen: {exc}")
    finally:
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def process_html_upload(cfg, part_path, original_filename, media_id):
    """Verwerk een HTML/ZIP-upload naar kolommen voor de media-tabel."""
    ext = os.path.splitext(original_filename)[1].lower()
    dest = os.path.join(cfg.html_dir, media_id)
    info = extract_project(part_path, original_filename, dest)
    created = [dest]
    try:
        h = hashlib.sha256()
        with open(part_path, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        size = os.path.getsize(part_path)
        final_name = media_id + ext
        final = os.path.join(cfg.files_dir, final_name)
        os.replace(part_path, final)
        created.append(final)
        return {
            "kind": "html", "format": "zip" if ext == ".zip" else "html", "ext": ext,
            "width": None, "height": None, "duration": None, "codec": None,
            "status": "warning" if info["warnings"] else "ok",
            "warning": " ".join(info["warnings"]) or None,
            "file_name": final_name, "playback_name": media_id, "playback_dir": "html",
            "playback_sha256": h.hexdigest(), "playback_size": size, "thumb_name": None,
            "html_entry": info["entry"], "_created": created,
        }
    except Exception:
        for p in created:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.unlink(p)
        raise


def safe_join(root, rel):
    """Pad binnen root, of None als rel buiten root zou komen."""
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or "\x00" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("..",) for p in parts):
        return None
    full = os.path.realpath(os.path.join(root, *[p for p in parts if p not in ("", ".")]))
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, full]) != real_root:
        return None
    return full
