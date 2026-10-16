#!/usr/bin/env python3
"""Nagebootste mpv voor tests: implementeert het gebruikte deel van de JSON-IPC.

Gedrag:
- loadfile <pad> replace: bestaat het bestand niet, of staat 'corrupt' in de naam,
  dan wordt mpv idle en volgt een end-file event met reason=error.
- Video's (.mp4 e.d.) bereiken eof na FAKE_MPV_VIDEO_SECONDS (standaard 1.5 s).
- Bestand met 'hang' in de naam: mpv reageert niet meer op IPC.
- Elk geladen bestand wordt gelogd naar FAKE_MPV_LOG (één regel per load).
"""

import json
import os
import socket
import sys
import threading
import time

VIDEO_EXTS = (".mp4", ".m4v", ".mov", ".mkv", ".webm")

opts = {}
for a in sys.argv[1:]:
    if a.startswith("--") and "=" in a:
        k, v = a[2:].split("=", 1)
        opts[k] = v
    elif a.startswith("--"):
        opts[a[2:]] = "yes"

sock_path = opts["input-ipc-server"]
log_path = os.environ.get("FAKE_MPV_LOG")
video_seconds = float(os.environ.get("FAKE_MPV_VIDEO_SECONDS", "1.5"))

state = {"path": None, "idle": True, "loaded_at": 0, "kind": None, "hang": False,
         "props": {"volume": float(opts.get("volume", 100)), "video-rotate": int(opts.get("video-rotate", 0)),
                   "mute": False, "pause": False}}


def video_eof():
    """Zoals echte mpv met --keep-open: aan het einde pauzeren; pauze blijft bij het volgende bestand."""
    if state["kind"] != "video" or state["props"]["pause"]:
        return state["kind"] == "video" and state.get("eof", False)
    if time.time() - state["loaded_at"] >= video_seconds:
        state["eof"] = True
        state["props"]["pause"] = True
    return state.get("eof", False)
lock = threading.Lock()
clients = []

if os.path.exists(sock_path):
    os.unlink(sock_path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sock_path)
srv.listen(4)


def emit(conn, obj):
    try:
        conn.sendall((json.dumps(obj) + "\n").encode())
    except OSError:
        pass


def handle(conn):
    buf = b""
    while True:
        try:
            data = conn.recv(65536)
        except OSError:
            return
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            msg = json.loads(line)
            if state["hang"]:
                continue
            cmd = msg.get("command", [])
            rid = msg.get("request_id")
            resp = {"error": "success", "data": None, "request_id": rid}
            name = cmd[0] if cmd else ""
            with lock:
                if name == "quit":
                    emit(conn, resp)
                    os._exit(0)
                elif name == "loadfile":
                    path = cmd[1]
                    if state["path"]:
                        emit(conn, {"event": "end-file", "reason": "stop"})
                    if log_path:
                        with open(log_path, "a") as fh:
                            fh.write(os.path.basename(path) + "\n")
                    state["path"] = path
                    state["loaded_at"] = time.time()
                    state["eof"] = False
                    state["kind"] = "video" if path.lower().endswith(VIDEO_EXTS) else "image"
                    if "hang" in os.path.basename(path):
                        state["hang"] = True
                        emit(conn, resp)
                        continue
                    if not os.path.exists(path) or "corrupt" in os.path.basename(path):
                        state["idle"] = True
                        emit(conn, resp)
                        emit(conn, {"event": "end-file", "reason": "error", "file_error": "unrecognized file format"})
                        continue
                    state["idle"] = False
                elif name == "get_property":
                    prop = cmd[1]
                    if prop == "idle-active":
                        resp["data"] = state["idle"]
                    elif prop == "eof-reached":
                        if state["path"] is None or state["idle"]:
                            resp = {"error": "property unavailable", "request_id": rid}
                        else:
                            resp["data"] = video_eof()
                    elif prop == "path":
                        resp["data"] = state["path"]
                    elif prop in state["props"]:
                        resp["data"] = state["props"][prop]
                    else:
                        resp = {"error": "property unavailable", "request_id": rid}
                elif name in ("overlay-add", "overlay-remove"):
                    if log_path:
                        with open(log_path, "a") as fh:
                            fh.write(f"set {name} " + (os.path.basename(str(cmd[4])) + f" {cmd[2]},{cmd[3]} {cmd[7]}x{cmd[8]}"
                                                         if name == "overlay-add" else "") + "\n")
                elif name == "set_property":
                    if cmd[1] == "pause" and state["props"]["pause"] and not cmd[2]:
                        state["loaded_at"] = time.time()   # afspelen begint nu pas
                    state["props"][cmd[1]] = cmd[2]
                    if log_path:
                        with open(log_path, "a") as fh:
                            fh.write(f"set {cmd[1]}={cmd[2]}\n")
            emit(conn, resp)


while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
