# -*- coding: utf-8 -*-
"""Локальный HTTP-сервер: JSON API для интерфейса + статические файлы."""
import datetime
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import regs as R
from .modbus import LinkError, ModbusError


def web_root():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "web")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def _diff_sec(st):
    try:
        a = datetime.datetime.fromisoformat(st["clock"]["iso"])
        b = datetime.datetime.fromisoformat(st["pc_time"])
        return round((a - b).total_seconds())
    except Exception:
        return None


def device_brief(dev):
    """Краткая карточка для сводки."""
    with dev.state_lock:
        st = dict(dev.state)
    pipes = []
    errors = 0
    crit = 0
    for tp, p in (st.get("pipes") or {}).items():
        info = dev.pipe_names.get(tp) or {}
        errors += p.get("n_err") or 0
        crit = max(crit, p.get("crit") or 0)
        pipes.append({"tp": tp, "name": info.get("name") or "ТП%d" % tp, "type": info.get("type", ""),
                      "p_abs": p.get("p_abs"), "t": p.get("t"), "qv_std": p.get("qv_std"), "qv_wc": p.get("qv_wc"),
                      "q_m": p.get("q_m"), "n_err": p.get("n_err"), "crit": p.get("crit")})
    out = {
        "id": dev.id, "name": dev.cfg["name"], "write": bool(dev.cfg.get("write")), "host": dev.cfg["host"], "port": dev.cfg["port"], "unit": dev.cfg["unit"],
        "online": bool(st.get("online")), "error": st.get("error"), "paused": dev.paused,
        "ms": st.get("ms"), "updated": st.get("updated"), "clock": (st.get("clock") or {}).get("iso"),
        "diff": _diff_sec(st), "errors": errors, "crit": crit, "pipes": pipes,
        "serial": dev.info.get("serial"), "fw": dev.info.get("fw"), "oil": bool(dev.caps.get("oil")),
        "ntp": (st.get("ntp") or {}),
    }
    return out


def device_full(dev):
    out = device_brief(dev)
    with dev.state_lock:
        st = dict(dev.state)
    out["info"] = dev.info
    out["caps"] = dev.caps
    out["pipe_info"] = {str(k): v for k, v in dev.pipe_names.items()}
    out["pipes_full"] = {str(k): v for k, v in (st.get("pipes") or {}).items()}
    out["inputs"] = st.get("inputs")
    out["oil_groups"] = st.get("oil")
    out["kmh"] = st.get("kmh")
    out["sampler"] = st.get("sampler")
    out["level"] = st.get("level")
    out["level_text"] = R.LEVELS.get(st.get("level"), "?") if st.get("level") is not None else None
    out["reconnects"] = st.get("reconnects")
    out["errors_total"] = dev.errors_total
    out["pc_time"] = st.get("pc_time")
    out["year4"] = dev.year4
    return out


class App:
    def __init__(self, manager):
        self.m = manager
        self.last_ping = None
        self.started = time.time()
        self.shutdown = threading.Event()


class Handler(BaseHTTPRequestHandler):
    server_version = "UVPMonitor/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- утилиты ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _dev(self, did):
        d = self.server.app.m.devices.get(did)
        if d is None:
            raise KeyError(did)
        return d

    # ---- маршрутизация ----
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path.startswith("/api/"):
            return self._api("GET", path[5:].strip("/").split("/"), parse_qs(u.query))
        return self._static(path)

    def do_POST(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._err("нет такого пути", 404)
        return self._api("POST", u.path[5:].strip("/").split("/"), parse_qs(u.query))

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        root = web_root()
        full = os.path.normpath(os.path.join(root, path.lstrip("/")))
        if not full.startswith(os.path.normpath(root)) or not os.path.isfile(full):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method, parts, q):
        app = self.server.app
        m = app.m
        try:
            if parts == ["ping"]:
                app.last_ping = time.time()
                return self._json({"ok": True, "version": __version__})
            if parts == ["devices"] and method == "GET":
                with m.lock:
                    ids = list(m.order)
                return self._json({"devices": [device_brief(m.devices[i]) for i in ids], "selected": m.selected,
                                   "version": __version__})
            if parts == ["devices"] and method == "POST":
                b = self._body()
                try:
                    did = m.add(b)
                except ValueError as e:
                    return self._err(str(e))
                return self._json({"id": did})
            if parts == ["devices", "export"]:
                return self._json(m.export())
            if parts == ["devices", "import"] and method == "POST":
                b = self._body()
                n = m.import_(b.get("data", b), replace=bool(b.get("replace")))
                return self._json({"imported": n})
            if parts == ["select"] and method == "POST":
                b = self._body()
                did = b.get("id")
                m.selected = did if did in m.devices else None
                m.view = {"tab": b.get("tab") or "main", "tp": b.get("tp")}
                return self._json({"ok": True})
            if parts == ["test"] and method == "POST":
                return self._json(self._test(self._body()))
            if parts == ["quit"] and method == "POST":
                self._json({"ok": True})
                app.shutdown.set()
                return
            if parts[0] == "device" and len(parts) >= 2:
                dev = self._dev(parts[1])
                rest = parts[2:]
                if not rest:
                    if method == "GET":
                        return self._json(device_full(dev))
                    if method == "POST":
                        try:
                            m.update(dev.id, self._body())
                        except ValueError as e:
                            return self._err(str(e))
                        return self._json({"ok": True})
                if rest == ["delete"] and method == "POST":
                    m.remove(dev.id)
                    return self._json({"ok": True})
                if rest == ["pause"] and method == "POST":
                    dev.paused = bool(self._body().get("paused"))
                    return self._json({"paused": dev.paused})
                if rest == ["refresh"] and method == "POST":
                    dev.identified = False
                    dev.param_cache.clear()
                    dev.pipe_names.clear()
                    return self._json({"ok": True})
                if rest == ["params"]:
                    return self._json({
                        "pipes": {str(k[1]): v for k, v in dev.param_cache.items() if k[0] == "pipe"},
                        "inputs": {str(k[1]): v for k, v in dev.param_cache.items() if k[0] == "input"},
                        "pending": len(m.enrich_todo(dev)) if dev.identified else 0})
                if rest[:1] == ["oil"]:
                    if rest == ["oil", "control"]:
                        return self._json(dev.read_oil_control() or {})
                    if len(rest) == 3 and rest[1] == "period":
                        return self._json({"scopes": dev.read_oil_period(rest[2])})
                if rest == ["write-mode"] and method == "POST":
                    dev.cfg["write"] = bool(self._body().get("enabled"))
                    m.save()
                    return self._json({"write": dev.cfg["write"]})
                if rest == ["env"] and method == "GET":
                    return self._json({"sections": dev.read_env()})
                if rest[:1] in (["write"], ["login"], ["logout"], ["time"], ["ntp"]) and method == "POST":
                    if not dev.cfg.get("write"):
                        return self._err("запись выключена для этого прибора (включите «Разрешить запись» на вкладке «Настройки»)", 403)
                    b = self._body()
                    try:
                        if rest == ["login"]:
                            lvl = dev.login(b.get("user"), b.get("password"))
                            return self._json({"level": lvl, "level_text": R.LEVELS.get(lvl, "?") if lvl is not None else None})
                        if rest == ["logout"]:
                            lvl = dev.logout()
                            return self._json({"level": lvl})
                        if rest == ["write"]:
                            done = []
                            for it in b.get("items", []):
                                done.append(dev.write_reg(int(it["addr"]), it["value"]))
                            return self._json({"written": done})
                        if rest == ["time", "correct"]:
                            dev.time_correct(b.get("seconds"))
                            return self._json({"ok": True})
                        if rest == ["time", "sync-pc"]:
                            return self._json(dev.time_sync_pc())
                        if rest == ["ntp", "ip"]:
                            ip = str(b.get("ip", "")).strip()
                            parts = ip.split(".")
                            if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                                return self._err("неверный IP-адрес")
                            v = (int(parts[0]) << 24) | (int(parts[1]) << 16) | (int(parts[2]) << 8) | int(parts[3])
                            return self._json(dev.write_reg(1005, v))
                        if rest == ["ntp", "force"]:
                            return self._json(dev.write_reg(1007, 24680))
                        if rest == ["ntp", "auto"]:
                            return self._json(dev.write_reg(1007, 13579))
                    except PermissionError as e:
                        return self._err(str(e), 403)
                if rest == ["archive"] and method == "POST":
                    b = self._body()
                    start = datetime.datetime.fromisoformat(b["start"])
                    end = datetime.datetime.fromisoformat(b["end"]) if b.get("end") else datetime.datetime(2000, 1, 1)
                    zero_end = not b.get("end")
                    res = dev.archive_ex(int(b["tp"]), start, None if zero_end else end, int(b.get("opts", 7)))
                    return self._json(res)
            return self._err("нет такого пути", 404)
        except KeyError:
            return self._err("прибор не найден", 404)
        except LinkError as e:
            return self._err("нет связи: %s" % e, 502)
        except ModbusError as e:
            return self._err("прибор вернул ошибку Modbus %d" % e.code, 502)
        except (ValueError, TypeError) as e:
            return self._err("неверный запрос: %s" % e)
        except Exception as e:      # не роняем сервер
            return self._err("внутренняя ошибка: %r" % (e,), 500)

    def _test(self, b):
        from .device import Device
        cfg = self.server.app.m.clean_cfg(b)
        d = Device(cfg)
        d.client.attempts = 1
        d.client.timeout = 3.0
        try:
            info = d.identify()
            clk = d.read_clock()
            return {"ok": True, "info": info, "caps": d.caps, "clock": clk.get("iso")}
        except (LinkError, ModbusError) as e:
            return {"ok": False, "error": str(e)}
        finally:
            d.close()


def make_server(manager, host="127.0.0.1", port=0):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.app = App(manager)
    return srv
