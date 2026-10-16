"""Systeeminformatie (alleen lezen, geen root nodig)."""

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time

_cache = {}
_cache_lock = threading.Lock()


def _cached(key, ttl, fn):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _read(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return default


def _run(cmd, timeout=3):
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        if res.returncode == 0:
            return res.stdout.decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def system_uptime():
    raw = _read("/proc/uptime")
    try:
        return float(raw.split()[0])
    except (AttributeError, ValueError, IndexError):
        return None


def pi_model():
    raw = _read("/proc/device-tree/model")
    return raw.strip("\x00\n ") if raw else platform.machine()


def os_name():
    raw = _read("/etc/os-release", "")
    for line in raw.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return platform.system()


def memory():
    raw = _read("/proc/meminfo", "")
    vals = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                vals[parts[0].rstrip(":")] = int(parts[1]) * 1024
            except ValueError:
                pass
    total = vals.get("MemTotal")
    avail = vals.get("MemAvailable")
    return {"total": total, "available": avail}


def cpu_temperature():
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw.strip()) / 1000.0, 1)
    except (AttributeError, ValueError):
        return None


def load_average():
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def disk(path):
    try:
        du = shutil.disk_usage(path)
        return {"total": du.total, "used": du.used, "free": du.free}
    except OSError:
        return None


def throttled():
    """Raspberry Pi onderspanning/throttling via vcgencmd (optioneel)."""
    def fetch():
        out = _run(["vcgencmd", "get_throttled"], timeout=2)
        if not out or "=" not in out:
            return None
        try:
            value = int(out.split("=", 1)[1], 16)
        except ValueError:
            return None
        return {
            "raw": hex(value),
            "under_voltage_now": bool(value & 0x1),
            "throttled_now": bool(value & 0x4),
            "under_voltage_occurred": bool(value & 0x10000),
            "throttled_occurred": bool(value & 0x40000),
        }
    return _cached("throttled", 30, fetch)


def ip_addresses():
    def fetch():
        out = _run(["ip", "-j", "-4", "addr", "show", "scope", "global"])
        addrs = []
        if out:
            try:
                for iface in json.loads(out):
                    for a in iface.get("addr_info", []):
                        if a.get("family") == "inet" and a.get("local"):
                            addrs.append({"interface": iface.get("ifname"), "address": a["local"]})
            except ValueError:
                pass
        if not addrs:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("192.0.2.1", 9))
                addrs.append({"interface": "?", "address": s.getsockname()[0]})
                s.close()
            except OSError:
                pass
        return addrs
    return _cached("ips", 10, fetch)


def hostname():
    return socket.gethostname()


def time_status():
    def fetch():
        out = _run(["timedatectl", "show", "-p", "Timezone", "-p", "NTP", "-p", "NTPSynchronized"])
        res = {"timezone": None, "ntp": None, "synchronized": None}
        if out:
            for line in out.splitlines():
                k, _, v = line.partition("=")
                if k == "Timezone":
                    res["timezone"] = v
                elif k == "NTP":
                    res["ntp"] = v == "yes"
                elif k == "NTPSynchronized":
                    res["synchronized"] = v == "yes"
        if not res["timezone"]:
            try:
                res["timezone"] = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
            except IndexError:
                res["timezone"] = time.strftime("%Z")
        return res
    return _cached("time", 5, fetch)


def timezones():
    out = _run(["timedatectl", "list-timezones"], timeout=5)
    if out:
        return out.splitlines()
    zones = []
    base = "/usr/share/zoneinfo"
    for root, _dirs, files in os.walk(base):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), base)
            if "/" in rel and rel[0].isupper() and not rel.startswith(("posix", "right", "Etc/")):
                zones.append(rel)
    return sorted(zones) or ["UTC"]


def _systemctl_query(verb, unit):
    # is-active / is-enabled geven een exitcode != 0 bij 'inactive'/'disabled'.
    try:
        res = subprocess.run(["systemctl", verb, unit], capture_output=True, timeout=3, check=False)
        out = res.stdout.decode("utf-8", "replace").strip()
        return out or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def service_active(unit):
    out = _systemctl_query("is-active", unit)
    return out == "active" if out is not None else None


def service_enabled(unit):
    out = _systemctl_query("is-enabled", unit)
    return out == "enabled" if out is not None else None


def invalidate():
    with _cache_lock:
        _cache.clear()
