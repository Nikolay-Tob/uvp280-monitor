# -*- coding: utf-8 -*-
"""Сеанс работы с одним прибором УВП-280: определение особенностей, чтение, архив."""
import datetime
import re
import threading
import time

from . import regs as R
from .codec import Codec, IDENT32, IDENT64, clean, find_p32, find_p64, hint_from_hr100, regs_to_text, s16, u16
from .modbus import Client, LinkError, ModbusError

OIL_TYPE_LEN = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2, "float": 2, "double": 4}


def _ts(v):
    return v if v is not None else None


class Device:
    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.id = cfg["id"]
        self.client = Client(cfg["host"], cfg.get("port", 502), cfg.get("unit", 1),
                             timeout=cfg.get("timeout", 5.0), delay=cfg.get("delay", 0.01))
        self.client.on_reconnect = self._reconnected
        self.codec = Codec()
        self.state_lock = threading.RLock()
        self.info = {}                 # статические сведения (после identify)
        self.caps = {}
        self.state = {"online": False, "error": None}
        self.param_cache = {}          # (kind, num) -> строки карты параметров
        self.pipe_names = {}
        self.identified = False
        self.paused = False
        self.polls = 0
        self.last_ok = None
        self.errors_total = 0
        self.year4 = True

    # ------------------------------------------------------------------ служебное
    def _reconnected(self):
        # у прибора часть состояния привязана к соединению (карта параметров, запрос архива)
        pass

    def close(self):
        self.client.close()

    def _try(self, kind, addr, n):
        try:
            return self.client.read(kind, addr, n)
        except ModbusError:
            return None

    def _hr(self, addr, n=1):
        return self.client.hr(addr, n)

    def _ir(self, addr, n=1):
        return self.client.ir(addr, n)

    # ------------------------------------------------------------------ определение прибора
    def identify(self):
        c = self.client
        c.connect()
        info = {"offset": 0, "order_ok": False, "p32": IDENT32, "p64": IDENT64}
        found = False
        for off in (0, -1, 1):
            c.off = off
            r = self._try(3, R.HR_CONST_I32, 2)
            if r is None:
                continue
            p = find_p32(r)
            if p is not None:
                info["offset"], info["p32"], found = off, p, True
                break
        if not found:
            c.off = 0
        else:
            r = self._try(3, R.HR_CONST_F64, 4)
            p64 = find_p64(r) if r else None
            if p64:
                info["p64"] = p64
                info["order_ok"] = True
        self.codec = Codec(info["p32"], info["p64"])
        hint = self._try(3, R.HR_ORDER_ID, 1), self._try(3, R.HR_ORDER_RAW, 2)
        if hint[0] and hint[1]:
            info["order_hint"] = hint_from_hr100(hint[0][0], hint[1])
        info["order"] = "%s / %s" % ("".join(str(i) for i in info["p32"]), "".join(str(i) for i in info["p64"]))
        # идентификация
        r = self._try(3, R.HR_MASK, 1)
        mask = r[0] if r else 0
        info["mask"] = mask
        info["pipes"] = [i + 1 for i in range(R.MAX_PIPES) if mask >> i & 1]
        r = self._try(3, R.HR_FW, 3)
        if r:
            info["fw"] = "%d" % r[0]
            try:
                info["fw_date"] = "%02d.%02d.%d" % (r[1] % 100, r[1] // 100, r[2])
            except Exception:
                pass
            info["fw_raw"] = r[0]
        r = self._try(3, R.HR_SERIAL, 2)
        if r:
            info["serial"] = self.codec.i32(r)
        r = self._try(3, R.HR_CLOCK, 6)
        if r:
            self.year4 = r[2] >= 100
        # возможности
        caps = {}
        caps["ntp"] = self._try(3, R.HR_NTP_IP, 2) is not None and self._try(3, R.HR_NTP_STATUS, 1) is not None
        caps["oil"] = self._try(4, R.OIL_PRESENT_IR, 4) is not None
        caps["kmh"] = self._try(3, 18000, 1) is not None and caps["oil"] or self._try(3, 18010, 2) is not None
        caps["sampler"] = caps["oil"] and self._try(3, 18500, 1) is not None
        caps["inputs"] = self._count_inputs()
        self.caps = caps
        self.info = info
        self.identified = True
        return info

    def _count_inputs(self):
        n = 0
        while n < R.MAX_INPUTS:
            step = 4
            r = self._try(4, R.input_base(n + 1), step * 4)
            if r is None:
                # возможно, доступны не все 4 входа блока
                for k in range(step):
                    if self._try(4, R.input_base(n + k + 1), 4) is None:
                        return n + k
                return n
            n += step
        return n

    # ------------------------------------------------------------------ динамика
    def read_clock(self):
        r = self._hr(R.HR_CLOCK, 6)
        d, mo, y, h, mi, s = r
        if y < 100:
            y += 2000
        self.year4 = r[2] >= 100
        try:
            t = datetime.datetime(y, mo, d, h, mi, s)
        except ValueError:
            return {"raw": r, "iso": None}
        return {"iso": t.isoformat(), "raw": r}

    def read_ntp(self):
        if not self.caps.get("ntp"):
            return None
        ip = self.codec.u32(self._hr(R.HR_NTP_IP, 2))
        st = self._hr(R.HR_NTP_STATUS, 1)[0]
        diff = s16(self._hr(R.HR_NTP_DIFF, 1))
        return {
            "ip": "%d.%d.%d.%d" % (ip >> 24, ip >> 16 & 255, ip >> 8 & 255, ip & 255) if ip else "",
            "link": bool(st & 1), "fresh": bool(st >> 1 & 1), "auto": bool(st >> 2 & 1),
            "diff": diff, "raw": st,
        }

    def read_pipe(self, tp, hist=False):
        base = R.pipe_base(tp)
        blk = self._ir(base + R.PIPE_BLOCK_A[0], R.PIPE_BLOCK_A[1])
        out = {"tp": tp}
        for off, typ, key, label, qty in R.PIPE_FIELDS:
            out[key] = clean(self.codec.by_type(typ, blk, off))
        if hist:
            b2 = self._ir(base + R.PIPE_BLOCK_B[0], R.PIPE_BLOCK_B[1])
            for off, typ, key, label, qty in R.PIPE_HIST:
                out[key] = clean(self.codec.by_type(typ, b2, off - R.PIPE_BLOCK_B[0]))
        return out

    def read_inputs(self):
        n = self.caps.get("inputs", 0)
        res = []
        i = 1
        while i <= n:
            k = min(16, n - i + 1)
            blk = self._try(4, R.input_base(i), k * 4)
            if blk is None:
                # читаем по одному
                for j in range(i, i + k):
                    b = self._try(4, R.input_base(j), 4)
                    if b:
                        res.append(self._input_from(j, b))
            else:
                for j in range(k):
                    res.append(self._input_from(i + j, blk[j * 4:j * 4 + 4]))
            i += k
        return res

    def _input_from(self, n, b):
        st = u16(b, 2)
        undescribed = bool(st >> R.INPUT_STATUS_UNDESCRIBED & 1)
        flags = [t for bit, t in R.INPUT_STATUS_BITS if st >> bit & 1]
        level = "ok"
        if any(st >> b_ & 1 for b_ in R.BAD_BITS):
            level = "bad"
        elif any(st >> b_ & 1 for b_ in R.WARN_BITS):
            level = "warn"
        return {"n": n, "value": clean(self.codec.f32(b, 0)), "status": st, "flags": flags,
                "level": level, "described": not undescribed}

    # ------------------------------------------------------------------ универсальное чтение таблиц
    def read_rows(self, rows, base=0):
        """rows: [(kind, addr, type, label)] -> список {addr,label,type,value}. Читает смежные куски."""
        out = []
        rows = sorted(rows, key=lambda r: r[1])
        spans = []
        for kind, addr, typ, label in rows:
            ln = OIL_TYPE_LEN[typ.lower()]
            if spans and spans[-1]["kind"] == kind and addr == spans[-1]["end"] and spans[-1]["end"] - spans[-1]["start"] + ln <= 120:
                spans[-1]["rows"].append((addr, typ, label, ln))
                spans[-1]["end"] = addr + ln
            else:
                spans.append({"kind": kind, "start": addr, "end": addr + ln, "rows": [(addr, typ, label, ln)]})
        for sp in spans:
            fc = 4 if sp["kind"] in ("IR", "BASE") else 3
            data = self._retry_busy(fc, base + sp["start"], sp["end"] - sp["start"])
            if data is None:
                for addr, typ, label, ln in sp["rows"]:
                    d = self._retry_busy(fc, base + addr, ln)
                    out.append(self._row(addr, typ, label, d, 0))
            else:
                for addr, typ, label, ln in sp["rows"]:
                    out.append(self._row(addr, typ, label, data, addr - sp["start"]))
        return out

    def _row(self, addr, typ, label, data, off):
        v = None
        if data is not None:
            v = clean(self.codec.by_type(typ, data, off))
        return {"addr": addr, "label": label, "type": typ.lower(), "value": v}

    def _retry_busy(self, fc, addr, n):
        for i in range(4):
            try:
                return self.client.read(fc, addr, n)
            except ModbusError as e:
                if e.code == 6:
                    time.sleep(0.8)
                    continue
                return None
        return None

    # ------------------------------------------------------------------ нефть
    def read_oil(self):
        if not self.caps.get("oil"):
            return None
        groups = []
        for gid, title, rows in R.OIL_GROUPS_IR:
            res = self.read_rows(rows)
            if gid.startswith("line") or gid == "bik":
                if all(x["value"] is None for x in res):
                    continue          # линия/БИК не сконфигурированы
            groups.append({"id": gid, "title": title, "rows": res})
        return groups

    def read_oil_period(self, key):
        base = {k: b for k, _, b in R.PERIODS}[key]
        scopes = []
        for sid, title, lo, hi in R.PERIOD_SCOPES:
            rows = [(k, a, t, l) for (k, a, t, l) in R.BASE_ROWS if lo <= a < hi and not (sid == "sikn" and a >= 100)]
            res = self.read_rows(rows, base=base)
            if all(x["value"] is None for x in res):
                continue
            scopes.append({"id": sid, "title": title, "rows": res})
        return scopes

    def read_kmh(self):
        if not self.caps.get("kmh"):
            return None
        ctl = self._try(3, 18000, 2)
        cnt = self._try(3, 18004, 2)
        rows = [("HR", a, t, R.KMH_LABELS.get(a, l)) for (a, t, l) in R.KMH_ROWS]
        out = {"ref_pipe": ctl[0] if ctl else None, "ctl_pipe": ctl[1] if ctl else None,
               "count": self.codec.u32(cnt) if cnt else None, "rows": self.read_rows(rows)}
        r = self._try(3, 18002, 2)
        out["param"] = self.codec.u32(r) if r else None
        return out

    def read_sampler(self):
        if not self.caps.get("sampler"):
            return None
        out = []
        for name, base in R.SAMPLERS:
            r = self._try(3, base, 2)
            if r is None:
                continue
            cnt = self._try(3, base + 2, 2)
            per = self._try(3, base + 4, 2)
            mode = s16(r, 0)
            out.append({"name": name, "base": base, "mode": mode,
                        "mode_text": R.SAMPLER_MODES.get(mode, "отключён"),
                        "pulse_ms": s16(r, 1),
                        "count": self.codec.i32(cnt) if cnt else None,
                        "period": clean(self.codec.f32(per)) if per else None})
        return out

    def read_oil_control(self):
        if not self.caps.get("oil"):
            return None
        r = self._try(3, 17100, 1)
        dens = self.read_rows([("HR", a, "float", R.DENS_LABELS.get(a, l)) for (a, l) in R.DENS_COEFS])
        kf = []
        for name, base in R.KFACTOR_BASES:
            rows = [(k, a, t, l) for (k, a, t, l) in R.HR_ROWS if base <= a < base + 50]
            res = self.read_rows(rows)
            if all(x["value"] is None for x in res):
                continue
            kf.append({"title": name, "base": base, "rows": res})
        return {"line_mask": r[0] if r else None, "dens": dens, "kfactors": kf}

    # ------------------------------------------------------------------ карта параметров
    def param_rows(self, kind, num, force=False):
        """kind: 'pipe' | 'input'. Возвращает [{idx,name,value}] (кэш на сеанс)."""
        key = (kind, num)
        if not force and key in self.param_cache:
            return self.param_cache[key]
        reg = 7600 if kind == "pipe" else 7601
        rows = []
        for idx in range(120):
            row = self._param_row(reg, num, idx)
            if row is None:
                break
            if not row[0] and not row[1]:
                break
            rows.append({"idx": idx, "name": row[0], "value": row[1]})
        self.param_cache[key] = rows
        return rows

    def _param_row(self, reg, num, idx):
        c = self.client
        for _ in range(3):
            with c.lock:
                before = c.reconnects
                try:
                    c.write(reg, [(num << 8) | idx])
                    nm = regs_to_text(c.hr(7700, 71))
                    vl = regs_to_text(c.hr(7800, 71))
                except ModbusError:
                    return None
                if c.reconnects == before:
                    return nm, vl
        return None

    def pipe_info(self, tp):
        if tp in self.pipe_names:
            return self.pipe_names[tp]
        info = {"type": "", "name": "ТП%d" % tp, "medium": ""}
        rows = []
        for idx in range(3):
            row = self._param_row(7600, tp, idx)
            rows.append(row)
        if rows[0]:
            info["type"] = rows[0][1]
        if rows[1] and rows[1][1]:
            info["name"] = rows[1][1].strip()
        if rows[2]:
            info["extra_name"], info["extra"] = rows[2]
        self.pipe_names[tp] = info
        return info

    # ------------------------------------------------------------------ архив
    def archive_ex(self, tp, start, end, opts=7, timeout=25.0):
        res = self.archive(tp, start, end, opts, timeout)
        res["tp"], res["start"] = tp, start.isoformat()
        res["end"] = end.isoformat() if end else None
        return res

    def archive(self, tp, start, end, opts=7, timeout=25.0):
        """start/end: datetime (end=None - срез: нулевое конечное время). Возвращает {'status','text','items'}."""
        c = self.client
        last = None
        for four in ((self.year4, not self.year4)):
            y = lambda d: d.year if four else d.year % 100
            rid = int(time.time()) & 0x7FFF
            req = [rid, tp, start.day, start.month, y(start), start.hour, start.minute, start.second]
            req += [0] * 6 if end is None else [end.day, end.month, y(end), end.hour, end.minute, end.second]
            for _ in range(10):
                try:
                    c.write(7900, req)
                    c.write(7914, [opts])
                    break
                except ModbusError as e:
                    if e.code == 6:
                        time.sleep(1.0)
                        continue
                    raise
            t0 = time.time()
            st = 1
            while time.time() - t0 < timeout:
                st = self._hr(8000, 1)[0]
                if st != 1:
                    break
                time.sleep(0.4)
            last = st
            if st == 4 and four == self.year4:
                continue            # формат года не подошёл - пробуем другой
            break
        res = {"status": last, "status_text": ARCHIVE_STATUS.get(last, "?"), "text": "", "items": []}
        if last == 2:
            _, length = self._hr(8001, 2)
            need = (length + 1) // 2
            regs, addr = [], 8003
            while len(regs) < need:
                n = min(120, need - len(regs))
                regs += self._hr(addr, n)
                addr += n
            from .codec import raw_bytes, ENC
            txt = raw_bytes(regs)[:length].split(b"\x00")[0].decode(ENC, errors="replace")
            res["text"] = txt
            res["items"] = parse_archive(txt)
        return res

    # ------------------------------------------------------------------ сводные снимки
    def poll(self, parts=(), hist_tp=None):
        """Один цикл опроса. parts: подмножество {'ntp','inputs','oil','level'} - что читать сверх часов и трубопроводов."""
        parts = set(parts)
        try:
            if not self.identified:
                self.identify()
            for tp in self.info.get("pipes", []):
                if tp not in self.pipe_names:
                    self.pipe_info(tp)
            st = {"online": True, "error": None}
            st["clock"] = self.read_clock()
            st["pc_time"] = datetime.datetime.now().isoformat()
            pipes = {}
            for tp in self.info.get("pipes", []):
                pipes[tp] = self.read_pipe(tp, hist=(tp == hist_tp))
            st["pipes"] = pipes
            if "ntp" in parts:
                st["ntp"] = self.read_ntp()
            if "level" in parts:
                r = self._try(3, R.HR_LEVEL, 1)
                st["level"] = r[0] if r else None
            if "inputs" in parts:
                st["inputs"] = self.read_inputs()
            if "oil" in parts and self.caps.get("oil"):
                st["oil"] = self.read_oil()
                st["kmh"] = self.read_kmh()
                st["sampler"] = self.read_sampler()
            st["ms"] = self.client.last_ms
            st["reconnects"] = self.client.reconnects
            st["updated"] = time.time()
            with self.state_lock:
                merged = dict(self.state)
                merged.update(st)
                self.state = merged
            self.last_ok = time.time()
            self.polls += 1
            return True
        except (LinkError, OSError) as e:
            self.errors_total += 1
            self.identified = False
            with self.state_lock:
                self.state = dict(self.state, online=False, error=str(e))
            self.client.close()
            return False
        except ModbusError as e:
            self.errors_total += 1
            with self.state_lock:
                self.state = dict(self.state, error="ошибка Modbus %d" % e.code)
            return False


ARCHIVE_STATUS = {
    0: "результат уничтожен по таймауту или запрос не принят", 1: "идёт сбор данных", 2: "готово",
    3: "за выбранный промежуток в архиве нет записей", 4: "ошибка начального времени",
    5: "ошибка конечного времени", 6: "ошибка номера трубопровода",
    7: "слишком большой период (сбор дольше 15 с)",
}

_TAG = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=\{([^}]*)\}")
_NUM = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*(.*?)\s*$", re.S)


def _not_number(unit):
    """Время, длительности и т.п. - не число с единицей."""
    return bool(re.search(r"(^|\s)\d|^[/:]", unit)) or len(re.findall(r"\d+", unit)) >= 2


def parse_archive(txt):
    """Разбор строки вида Tag={шапка}значение+единица; ... Устойчив к склеенным именам и мусору."""
    items = []
    ms = list(_TAG.finditer(txt))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(txt)
        raw = txt[m.end():end].rstrip()
        if raw.endswith(";"):
            raw = raw[:-1]
        item = {"tag": m.group(1), "title": m.group(2).strip(), "raw": raw.strip()}
        n = _NUM.match(raw)
        if n and "\n" not in raw and not _not_number(n.group(2)):
            try:
                item["value"] = float(n.group(1).replace(",", "."))
                item["unit"] = n.group(2)
            except ValueError:
                pass
        items.append(item)
    return items
