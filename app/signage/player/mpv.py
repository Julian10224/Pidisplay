"""Aansturing van mpv via de JSON-IPC-socket (--input-ipc-server)."""

import json
import logging
import os
import shlex
import socket
import subprocess
import time

log = logging.getLogger("signage.player.mpv")


class MpvError(Exception):
    pass


class Mpv:
    def __init__(self, binary, socket_path, hwdec="v4l2m2m-copy", vo="gpu", gpu_context="drm",
                 audio_device="auto", extra_args="", volume=80, rotation=0):
        self.binary = binary
        self.socket_path = socket_path
        self.hwdec = hwdec
        self.vo = vo
        self.gpu_context = gpu_context
        self.audio_device = audio_device
        self.extra_args = extra_args
        self.initial_volume = volume
        self.initial_rotation = rotation
        self.proc = None
        self.sock = None
        self.buf = b""
        self.events = []
        self.req = 0

    def build_args(self):
        args = [
            self.binary,
            "--no-config",
            "--idle=yes",
            "--force-window=yes",
            "--fullscreen",
            "--keep-open=yes",                 # laatste frame blijft staan => geen zwart beeld
            "--image-display-duration=inf",    # de daemon bepaalt hoe lang een afbeelding blijft
            "--input-ipc-server=" + self.socket_path,
            "--input-terminal=no",
            "--input-default-bindings=no",
            "--input-vo-keyboard=no",
            "--osc=no",
            "--osd-level=0",
            "--cursor-autohide=always",
            "--audio-display=no",
            "--sub-auto=no",
            "--load-scripts=no",
            "--ytdl=no",
            "--msg-level=all=warn",
            "--hwdec=" + self.hwdec,
            "--vo=" + self.vo,
            f"--volume={int(self.initial_volume)}",
            f"--video-rotate={int(self.initial_rotation)}",
        ]
        if self.vo == "gpu" and self.gpu_context:
            args.append("--gpu-context=" + self.gpu_context)
        if self.audio_device and self.audio_device != "auto":
            args.append("--audio-device=" + self.audio_device)
        if self.extra_args:
            args += shlex.split(self.extra_args)
        return args

    # -- levenscyclus ----------------------------------------------------------
    def start(self, timeout=15):
        self.stop()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        args = self.build_args()
        log.info("mpv starten: %s", " ".join(shlex.quote(a) for a in args))
        try:
            self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise MpvError(f"mpv niet gevonden ({self.binary}). Is het pakket 'mpv' geïnstalleerd?")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise MpvError(f"mpv stopte direct na het starten (exitcode {self.proc.returncode}). "
                               "Controleer of er een scherm is aangesloten en of er geen desktop actief is.")
            if os.path.exists(self.socket_path):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self.socket_path)
                    s.setblocking(False)
                    self.sock = s
                    self.buf = b""
                    self.events = []
                    return
                except OSError:
                    pass
            time.sleep(0.1)
        self.stop()
        raise MpvError("mpv reageerde niet op tijd (IPC-socket ontbreekt).")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None and self.sock is not None

    def stop(self):
        if self.sock is not None:
            try:
                self._send({"command": ["quit"]})
            except OSError:
                pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=3)
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.proc = None

    # -- communicatie ---------------------------------------------------------
    def _send(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        self.sock.setblocking(True)
        self.sock.settimeout(3)
        try:
            self.sock.sendall(data)
        finally:
            self.sock.setblocking(False)

    def _read_lines(self, wait):
        import select
        r, _, _ = select.select([self.sock], [], [], wait)
        if not r:
            return []
        try:
            chunk = self.sock.recv(65536)
        except BlockingIOError:
            return []
        if not chunk:
            raise MpvError("IPC-verbinding met mpv verbroken.")
        self.buf += chunk
        lines = []
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if line.strip():
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    log.debug("Onleesbare IPC-regel: %r", line[:200])
        return lines

    def command(self, *args, timeout=5.0):
        if not self.alive():
            raise MpvError("mpv draait niet.")
        self.req += 1
        rid = self.req
        try:
            self._send({"command": list(args), "request_id": rid})
        except OSError as exc:
            raise MpvError(f"Schrijven naar mpv mislukt: {exc}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            reply = None
            # Verwerk altijd de hele batch: events die in hetzelfde pakket als het
            # antwoord binnenkomen mogen niet verloren gaan.
            for msg in self._read_lines(max(0.0, deadline - time.time())):
                if "event" in msg:
                    self.events.append(msg)
                elif msg.get("request_id") == rid:
                    reply = msg
            if reply is not None:
                if reply.get("error") not in (None, "success"):
                    raise MpvError(f"mpv: {args[0]}: {reply.get('error')}")
                return reply.get("data")
        raise MpvError(f"mpv reageert niet (commando {args[0]}).")

    def get(self, prop, default=None):
        try:
            return self.command("get_property", prop)
        except MpvError as exc:
            if "property unavailable" in str(exc) or "property not found" in str(exc):
                return default
            raise

    def set(self, prop, value):
        return self.command("set_property", prop, value)

    def drain_events(self, wait=0.0):
        if not self.alive():
            raise MpvError("mpv draait niet.")
        try:
            for msg in self._read_lines(wait):
                if "event" in msg:
                    self.events.append(msg)
        except MpvError:
            raise
        out, self.events = self.events, []
        return out
