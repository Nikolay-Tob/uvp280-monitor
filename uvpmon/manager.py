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


class Manager:
    SEL_INTERVAL = 2.0
    BG_INTERVAL = 10.0

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
        self.load()

    # ---------------------------------------------------------------- хранение
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {"devices": []}
        for cfg in data.get("devices", []):
            self._add(cfg)

    def save(self):
        with self.lock:
            data = {"devices": [self.cfg_of(i) for i in self.order]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def clean_cfg(cfg, keep_id=None):
        try:
            port = int(cfg.get("port", 502))
            unit = int(cfg.get("unit", 1))
        except (TypeError, ValueError):
            raise ValueError("порт и Unit ID должны быть числами")
        host = str(cfg.get("host", "")).strip()
        if not host:
            raise ValueError("не указан адрес")
        if not (1 <= port <= 65535) or not (0 <= unit <= 255):
            raise ValueError("порт 1..65535, Unit ID 0..255")
        return {"id": keep_id or cfg.get("id") or uuid.uuid4().hex[:8], "name": str(cfg.get("name") or host).strip(),
                "host": host, "port": port, "unit": unit, "note": str(cfg.get("note", ""))}

    def cfg_of(self, did):
        d = self.devices[did]
        c = d.cfg
        return {k: c.get(k) for k in ("id", "name", "host", "port", "unit", "note")}

    def _add(self, cfg):
        cfg = self.clean_cfg(cfg)
        with self.lock:
            while cfg["id"] in self.devices:
                cfg["id"] = uuid.uuid4().hex[:8]
            dev = Device(cfg)
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
            interval = self.SEL_INTERVAL if self.selected == dev.id else self.BG_INTERVAL
            if not dev.state.get("online") and dev.identified is False:
                interval = max(interval, 5.0)
            while time.time() - t0 < interval and not self.stop.is_set():
                time.sleep(0.2)
                if self.selected == dev.id and time.time() - t0 > self.SEL_INTERVAL:
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
