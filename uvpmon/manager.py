# -*- coding: utf-8 -*-
"""Список приборов, хранение настроек и фоновый опрос."""
import json
import os
import sys
import threading
import time
import uuid

from .device import Device


def data_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "UVP280Monitor")
    else:
        d = os.path.join(os.path.expanduser("~"), ".uvp280monitor")
    os.makedirs(d, exist_ok=True)
    return d


DEFAULT_SETTINGS = {"sel_interval": 2.0, "bg_interval": 10.0, "timeout": 5.0, "delay_ms": 10, "attempts": 3}
SETTINGS_LIMITS = {"sel_interval": (0.5, 60.0), "bg_interval": (2.0, 600.0), "timeout": (1.0, 60.0),
                   "delay_ms": (0, 1000), "attempts": (1, 6)}


class Manager:

    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "devices.json")
        self.lock = threading.RLock()
        self.devices = {}          # id -> Device
        self.order = []
        self.selected = None
        self.view = {"tab": "main", "tp": None}
        self.stop = threading.Event()
        self.threads = {}
        self.enrich_jobs = {}
        self.settings = dict(DEFAULT_SETTINGS)
        self.load()

    # ---------------------------------------------------------------- хранение
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {"devices": []}
        try:
            self.settings = self.clean_settings(data.get("settings") or {})
        except ValueError:
            self.settings = dict(DEFAULT_SETTINGS)
        for cfg in data.get("devices", []):
            self._add(cfg)

    def save(self):
        with self.lock:
            data = {"devices": [self.cfg_of(i) for i in self.order], "settings": self.settings}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def clean_settings(src):
        out = dict(DEFAULT_SETTINGS)
        for k, (lo, hi) in SETTINGS_LIMITS.items():
            if k in src and src[k] not in (None, ""):
                try:
                    v = float(src[k])
                except (TypeError, ValueError):
                    raise ValueError("неверное значение: %s" % k)
                if not lo <= v <= hi:
                    raise ValueError("%s: допустимо от %g до %g" % (k, lo, hi))
                out[k] = int(v) if k in ("delay_ms", "attempts") else v
        return out

    def set_settings(self, src):
        self.settings = self.clean_settings(src)
        with self.lock:
            for dev in self.devices.values():
                self.apply(dev)
        self.save()

    def apply(self, dev):
        c = dev.client
        t = dev.cfg.get("timeout")
        c.timeout = float(t) if t else float(self.settings["timeout"])
        c.delay = self.settings["delay_ms"] / 1000.0
        c.attempts = int(self.settings["attempts"])
        if c.sock is not None:
            try:
                c.sock.settimeout(c.timeout)
            except OSError:
                pass

    @staticmethod
    def clean_cfg(cfg, keep_id=None):
        try:
            port = int(cfg.get("port", 502))
            unit = int(cfg.get("unit", 1))
        except (TypeError, ValueError):
            raise ValueError("порт и Unit ID должны быть числами")
        host = str(cfg.get("host", "")).strip()
        try:
            to = cfg.get("timeout")
            to = float(to) if to not in (None, "", 0) else None
        except (TypeError, ValueError):
            raise ValueError("таймаут должен быть числом")
        if to is not None and not 1 <= to <= 60:
            raise ValueError("таймаут от 1 до 60 с")
        if not host:
            raise ValueError("не указан адрес")
        if not (1 <= port <= 65535) or not (0 <= unit <= 255):
            raise ValueError("порт 1..65535, Unit ID 0..255")
        return {"id": keep_id or cfg.get("id") or uuid.uuid4().hex[:8], "name": str(cfg.get("name") or host).strip(),
                "host": host, "port": port, "unit": unit, "note": str(cfg.get("note", "")),
                "write": bool(cfg.get("write", False)), "timeout": to}

    def cfg_of(self, did):
        d = self.devices[did]
        c = d.cfg
        return {k: c.get(k) for k in ("id", "name", "host", "port", "unit", "note", "write", "timeout")}

    def _add(self, cfg):
        cfg = self.clean_cfg(cfg)
        with self.lock:
            while cfg["id"] in self.devices:
                cfg["id"] = uuid.uuid4().hex[:8]
            dev = Device(cfg)
            self.apply(dev)
            self.devices[cfg["id"]] = dev
            self.order.append(cfg["id"])
        if self.threads is not None and not self.stop.is_set() and getattr(self, "started", False):
            self._start_thread(dev)
        return cfg["id"]

    def add(self, cfg):
        did = self._add(cfg)
        self.save()
        return did

    def update(self, did, cfg):
        with self.lock:
            old = self.devices[did]
            new = self.clean_cfg(cfg, keep_id=did)
            changed = any(old.cfg.get(k) != new[k] for k in ("host", "port", "unit"))
            old.cfg.update(new)
            self.apply(old)
            if changed:
                old.client.host, old.client.port, old.client.unit = new["host"], new["port"], new["unit"]
                old.identified = False
                old.param_cache.clear()
                old.pipe_names.clear()
                old.client.close()
        self.save()

    def remove(self, did):
        with self.lock:
            dev = self.devices.pop(did, None)
            if did in self.order:
                self.order.remove(did)
            if self.selected == did:
                self.selected = None
        if dev:
            dev.paused = True
            dev.close()
        self.save()

    def export(self):
        with self.lock:
            return {"format": "uvp280-monitor", "devices": [self.cfg_of(i) for i in self.order]}

    def import_(self, data, replace=False):
        items = data.get("devices", []) if isinstance(data, dict) else data
        if replace:
            for did in list(self.order):
                self.remove(did)
        n = 0
        for cfg in items:
            try:
                self._add(dict(cfg, id=None))
                n += 1
            except ValueError:
                pass
        self.save()
        return n

    # ---------------------------------------------------------------- опрос
    def start(self):
        self.started = True
        for did in list(self.order):
            self._start_thread(self.devices[did])

    def _start_thread(self, dev):
        t = threading.Thread(target=self._loop, args=(dev,), daemon=True, name="poll-" + dev.id)
        self.threads[dev.id] = t
        t.start()

    def parts_for(self, dev):
        if self.selected != dev.id:
            return (), None
        tab = self.view.get("tab")
        if tab == "main":
            return ("ntp", "level"), None
        if tab == "sensors":
            return ("inputs", "level"), None
        if tab == "oil":
            return ("oil", "level"), None
        if tab == "pipes":
            return ("level",), self.view.get("tp")
        return ("level",), None

    def _loop(self, dev):
        while not self.stop.is_set() and dev.id in self.devices:
            t0 = time.time()
            if not dev.paused:
                parts, hist = self.parts_for(dev)
                ok = dev.poll(parts, hist)
                if ok and self.selected == dev.id and self.view.get("tab") in ("sensors", "pipes"):
                    self._maybe_enrich(dev)
            sel, bg = self.settings["sel_interval"], self.settings["bg_interval"]
            interval = sel if self.selected == dev.id else bg
            if not dev.state.get("online") and dev.identified is False:
                interval = max(interval, 5.0)
            while time.time() - t0 < interval and not self.stop.is_set():
                time.sleep(0.2)
                if self.selected == dev.id and time.time() - t0 > self.settings["sel_interval"]:
                    break

    # ---------------------------------------------------------------- карта параметров в фоне
    def _maybe_enrich(self, dev):
        if self.enrich_jobs.get(dev.id) and self.enrich_jobs[dev.id].is_alive():
            return
        todo = self.enrich_todo(dev)
        if not todo:
            return
        th = threading.Thread(target=self._enrich, args=(dev,), daemon=True)
        self.enrich_jobs[dev.id] = th
        th.start()

    def enrich_todo(self, dev):
        todo = []
        for tp in dev.info.get("pipes", []):
            if ("pipe", tp) not in dev.param_cache:
                todo.append(("pipe", tp))
        for inp in dev.state.get("inputs") or []:
            if inp["described"] and ("input", inp["n"]) not in dev.param_cache:
                todo.append(("input", inp["n"]))
        return todo

    def _enrich(self, dev):
        for kind, num in self.enrich_todo(dev):
            if self.stop.is_set() or dev.id not in self.devices:
                return
            try:
                dev.param_rows(kind, num)
            except Exception:
                return
            time.sleep(0.05)
