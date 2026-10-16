"""Draait de echte lokale playerserver (browser.LocalServer + PageState) met een scenario
uit een JSON-bestand. Wijzigt het bestand, dan krijgt de pagina een nieuwe revisie,
zoals bij een playlistwijziging. De laatste status van de pagina staat in <scenario.json>.status.
Elke regel JSON op stdin is een opdracht van de live bediening (zoals de Syncer die doorgeeft).

Gebruik: python3 harness.py <poort> <scenario.json>
"""

import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "app"))

from signage.player.browser import LocalServer, PageState  # noqa: E402

port, scenario = int(sys.argv[1]), sys.argv[2]
ps = PageState(port)
last = {"mtime": 0, "status": None}


def load():
    with open(scenario) as fh:
        cfg = json.load(fh)
    ps.update(cfg)


def on_status(data):
    last["status"] = data
    with open(scenario + ".status.tmp", "w") as fh:   # voor tests: laatste status van de pagina
        json.dump(data, fh)
    os.replace(scenario + ".status.tmp", scenario + ".status")


load()
last["mtime"] = os.path.getmtime(scenario)
srv = LocalServer(port, ps, on_status)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def read_commands():
    for line in sys.stdin:
        if line.strip():
            srv.commands.put(json.loads(line))


threading.Thread(target=read_commands, daemon=True).start()
print("READY", flush=True)
while True:
    time.sleep(0.2)
    m = os.path.getmtime(scenario)
    if m != last["mtime"]:
        last["mtime"] = m
        load()
        print("RELOADED", flush=True)
