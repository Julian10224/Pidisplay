"""Doorgeefluik voor systemd-meldingen bij de browserweergave.

De browserweergave draait als  cage -> player-daemon -> Chromium. De service gebruikt een
PAM-sessie (PAMName=, nodig voor scherm en invoer via logind); pam_systemd verplaatst de
processen daardoor naar een sessie-scope (session-N.scope) in plaats van de service-cgroup.
systemd koppelt een melding (READY=1, WATCHDOG=1) aan een unit via de cgroup van de afzender,
of via het hoofdproces. De daemon is geen hoofdproces en zit niet in de service-cgroup: zijn
READY=1 kwam nooit aan, systemd brak de start na 90 s af ("start operation timed out") en de
player herstartte steeds (Chromium: "Fatal Wayland communication error: Broken pipe").

Dit programma wordt het hoofdproces (exec vanuit signage-player-launch), start de opdracht
(cage …) als kind met een eigen NOTIFY_SOCKET, en geeft de meldingen van de daemon door aan
systemd. Omdat het doorgeven vanuit het hoofdproces gebeurt, accepteert systemd ze.
Signalen (SIGTERM bij stoppen) gaan door naar het kind; de afloop van het kind (exitcode of
signaal) wordt overgenomen, zodat signage-player-after-exit dezelfde uitkomst ziet als eerst.

Gebruik:  python3 -m signage.player.notifyrelay -- cage -s -- python3 -m signage.player.daemon …
"""

import os
import select
import signal
import socket
import subprocess
import sys
import time

# Alleen deze meldingen worden doorgegeven (geen MAINPID=, FDSTORE= e.d.).
ALLOWED = ("READY=", "WATCHDOG=", "STOPPING=", "STATUS=", "RELOADING=", "EXTEND_TIMEOUT_USEC=", "ERRNO=")
STOP_GRACE = 15


def _send(addr, message):
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.connect(addr)
        s.sendall(message.encode())
    except OSError:
        pass
    finally:
        s.close()


def _filter(data):
    lines = [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.startswith(ALLOWED)]
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        print("gebruik: notifyrelay -- opdracht [argumenten]", file=sys.stderr)
        return 2
    real = os.environ.get("NOTIFY_SOCKET")
    rundir = (os.environ.get("RUNTIME_DIRECTORY") or "").split(":")[0] or os.environ.get("XDG_RUNTIME_DIR") or ""
    if not real or not rundir or not os.path.isdir(rundir):
        os.execvp(argv[0], argv)          # niets door te geven: gewoon de opdracht zelf worden
    path = os.path.join(rundir, "notify-relay.sock")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    os.chmod(path, 0o600)

    child = subprocess.Popen(argv, env=dict(os.environ, NOTIFY_SOCKET=path))
    stop_at = [None]

    def forward(signum, _frame):
        if stop_at[0] is None and signum in (signal.SIGTERM, signal.SIGINT):
            stop_at[0] = time.monotonic() + STOP_GRACE
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, forward)

    try:
        while child.poll() is None:
            if stop_at[0] is not None and time.monotonic() > stop_at[0]:
                child.kill()
            try:
                ready, _, _ = select.select([sock], [], [], 0.5)
            except InterruptedError:
                continue
            if ready:
                try:
                    msg = _filter(sock.recv(4096))
                except OSError:
                    msg = ""
                if msg:
                    _send(real, msg)
        # Laatste meldingen (bijv. STOPPING=1) nog doorgeven.
        sock.setblocking(False)
        while True:
            try:
                msg = _filter(sock.recv(4096))
            except OSError:
                break
            if msg:
                _send(real, msg)
    finally:
        sock.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    rc = child.returncode
    if rc < 0:                              # kind stopte door een signaal: hetzelfde signaal
        signal.signal(-rc, signal.SIG_DFL)  # zodat systemd dezelfde afloop ziet
        os.kill(os.getpid(), -rc)
        time.sleep(1)
        return 128 - rc
    return rc


if __name__ == "__main__":
    sys.exit(main())
