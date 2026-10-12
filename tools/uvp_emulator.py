#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Эмулятор УВП-280.01 по карте регистров (для разработки и проверки без прибора).
   python uvp_emulator.py [--port 1502] [--off 0]   (off: смещение адреса, -1 = нумерация с 1)"""
import argparse, datetime, socketserver, struct, sys, threading, time

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ENC = "cp1251"
HR, IR = {}, {}          # адрес -> uint16
HRI, IRI = {}, {}        # начало параметра -> длина (допустимые границы чтения)


def put(store, items, addr, regs, whole=True):
    for i, r in enumerate(regs): store[addr + i] = r & 0xFFFF
    if whole: items[addr] = len(regs)


P32 = [0, 1, 2, 3]
P64 = [0, 1, 2, 3, 4, 5, 6, 7]
YEAR2 = False


def _perm(be, perm):
    raw = [0] * len(be)
    for i, b in enumerate(be): raw[perm[i]] = b
    return list(struct.unpack(">%dH" % (len(raw) // 2), bytes(raw)))


def f32(v): return _perm(struct.pack(">f", v), P32)
def i32(v): return _perm(struct.pack(">i", v), P32)
def u32(v): return _perm(struct.pack(">I", v), P32)
def f64(v): return _perm(struct.pack(">d", v), P64)


def strregs(s, nregs):
    b = s.encode(ENC)[:nregs * 2 - 1] + b"\x00"
    b = b.ljust(nregs * 2, b"\x00")
    return list(struct.unpack(">%dH" % nregs, b))


def setup():
    put(HR, HRI, 100, [0x0123 if P32 == [0, 1, 2, 3] else 0x2301]); put(HR, HRI, 101, [0x0123, 0x4567] if P32 == [0, 1, 2, 3] else [0x6745, 0x2301])
    put(HR, HRI, 110, i32(1234567890)); put(HR, HRI, 112, f32(123.4567)); put(HR, HRI, 114, f64(123.4567890123456))
    put(HR, HRI, 203, [1]); put(HR, HRI, 200, [0]); put(HR, HRI, 201, [0, 0])
    put(HR, HRI, 1000, f32(750.06)); put(HR, HRI, 1002, f32(10.0))
    for a, v in ((1010, 0.7), (1012, 0.5), (1014, 1.2), (1016, 0.0)): put(HR, HRI, a, f32(v))
    put(HR, HRI, 1018, [0])
    if NTP:
        put(HR, HRI, 1005, u32(0xC0A80001)); put(HR, HRI, 1007, [7]); put(HR, HRI, 1008, [2])
    put(HR, HRI, 1806, [0b11001111]); put(HR, HRI, 1807, [123, 729]); put(HR, HRI, 1809, [2011]); HRI[1807] = 1; HRI[1808] = 1
    HRI[1809] = 1; HR[1809] = 2011
    put(HR, HRI, 1810, i32(101006))
    if OIL: oil_setup()
    for tp in (1, 2, 3, 4, 7, 8):
        b = 2000 + (tp - 1) * 100
        vals = {0: 3e5, 2: 4e5 + tp * 1e4, 4: 20 + tp, 6: 8e4, 8: 1.5 + tp / 10, 10: 1e6, 12: 1e3, 14: 2e3, 16: 5e5, 18: 1e9,
                24: 1.5, 26: .001, 28: .002, 30: 9e5, 32: 900, 36: .7, 38: 1.2, 40: 2.5e4, 42: .002, 44: .001}
        for o, v in vals.items(): put(IR, IRI, b + o, f32(v))
        put(IR, IRI, b + 20, i32(8_000_000)); put(IR, IRI, b + 22, i32(3600))
        put(IR, IRI, b + 34, [1 if tp == 7 else 0]); put(IR, IRI, b + 35, [3 if tp == 7 else 0])
        for o in list(range(60, 100, 2)): put(IR, IRI, b + o, f32(o * 10.0))
    for n in range(1, NIN + 1):
        b = 4000 + (n - 1) * 4
        put(IR, IRI, b, f32(n * 3.3)); put(IR, IRI, b + 2, [4 if n == 3 else 0]); put(IR, IRI, b + 3, [0])
    for a in range(7600, 7602): put(HR, HRI, a, [0])
    for a in range(7700, 7771): put(HR, HRI, a, [0])
    for a in range(7800, 7871): put(HR, HRI, a, [0])
    for a in range(7900, 7915): put(HR, HRI, a, [0])
    for a in range(8000, 10000): put(HR, HRI, a, [0])


OIL = False
NTP = True
NIN = 8


def oil_setup():
    import os, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from uvpmon.oil_table import OIL_TABLE as tab
    for kind, addr, typ, _ in tab:
        if 17000 <= addr <= 17007 and kind == "HR": continue
        if kind == "HR" and 17250 <= addr < 17700 and addr >= 17400: pass
        n = {"int16": 1, "uint16": 1, "uint32": 2, "int32": 2, "float": 2, "double": 4}[typ]
        regs = {1: [7], 2: f32(1.5) if typ == "float" else u32(7), 4: f64(1.25)}[n]
        bases = [15000, 15500, 16000] if kind == "BASE" else [0]
        for b in bases:
            if kind == "HR": put(HR, HRI, addr, regs)
            else: put(IR, IRI, b + addr, regs)


def pipe_rows(tp):
    if OIL and tp == 1:
        return [("Тип датчика расхода / системы", "УУН"), ("Название трубопровода", "СИКН"), ("Линия 1", "Трубопровод 3")]
    if OIL and tp == 4:
        return [("Тип датчика расхода / системы", "КМХ"), ("Название трубопровода", "КМХ"), ("Контролируемая линия", "Трубопровод 3")]
    med = "нефть" if OIL else "газовая смесь MP113-03"
    return [("Тип датчика расхода / системы", "Датчик расхода" if tp % 2 else "Датчик количества"),
            ("Название трубопровода", "Тестовый ТП%d" % tp), ("Измеряемая среда", med),
            ("Вход давления (P)", "Логический вход 1"), ("Минимальный расход", "1 м3/ч(р.у)")]


def li_rows(n):
    return [("Тип выходного параметра", ["Температура", "Абсолютное давление", "Расход"][n % 3]),
            ("Единица измерения", ["°С", "МПа", "м3/ч(р.у)"][n % 3]), ("Источник", "ПИК3.01 №%d" % (1 + n % 2))]


class State:
    def __init__(self): self.st, self.rid, self.text = 0, 0, ""


class H(socketserver.BaseRequestHandler):
    def setup(self):
        self.s = State(); self.hr = dict(HR)  # у каждого соединения свои «строковые» регистры

    def recvn(self, n):
        b = b""
        while len(b) < n:
            c = self.request.recv(n - len(b))
            if not c: raise EOFError
            b += c
        return b

    def check(self, items, a, n):
        p = a
        while p < a + n:
            L = items.get(p)
            if not L or p + L > a + n: return False
            p += L
        return True

    def handle(self):
        off = self.server.off
        try:
            while True:
                tid, pid, ln, unit = struct.unpack(">HHHB", self.recvn(7))
                pdu = self.recvn(ln - 1); fc = pdu[0]
                try:
                    resp = self.do(fc, pdu, off)
                except Err as e:
                    resp = bytes([fc | 0x80, e.code])
                self.request.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
        except (EOFError, ConnectionError):
            pass

    def do(self, fc, pdu, off):
        if fc in (3, 4):
            a, n = struct.unpack(">HH", pdu[1:5]); a -= off
            st, it = (self.hr, HRI) if fc == 3 else (IR, IRI)
            if fc == 3: self.refresh()
            if n < 1 or n > 125 or not self.check(it, a, n): raise Err(2)
            data = struct.pack(">%dH" % n, *[st.get(a + i, 0) for i in range(n)])
            return bytes([fc, len(data)]) + data
        if fc in (6, 16):
            if fc == 6: a, v = struct.unpack(">HH", pdu[1:5]); vals = [v]
            else:
                a, n, bc = struct.unpack(">HHB", pdu[1:6]); vals = list(struct.unpack(">%dH" % n, pdu[6:6 + bc]))
            a -= off
            if not self.check(HRI, a, len(vals)): raise Err(2)
            for i, v in enumerate(vals):
                self.hr[a + i] = v
                self.written(a + i)
            return pdu[:5]
        raise Err(1)

    def written(self, addr):
        hr = self.hr
        if addr == 202:
            raw = struct.pack(">HH", hr[201], hr[202])
            be = bytes(raw[P32[i]] for i in range(4))
            v = struct.unpack(">I", be)[0]
            hr[203] = 2 if (v == 12345 and hr[200] == 1) else 1
        if addr in (7600, 7601):
            code = hr[addr]; num, idx = code >> 8, code & 255
            rows = pipe_rows(num) if addr == 7600 else li_rows(num)
            nm, vl = rows[idx] if idx < len(rows) else ("", "")
            if addr == 7601 and not (1 <= num <= NIN): nm = vl = ""
            for i, r in enumerate(strregs(nm, 71)): hr[7700 + i] = r
            for i, r in enumerate(strregs(vl, 71)): hr[7800 + i] = r
        if addr == 7914:
            if self.s.st == 1: raise Err(6)
            self.s.rid = hr[7900]; self.s.st = 1
            req = [hr[7900 + i] for i in range(14)]
            threading.Timer(0.8, self.arch_done, (req,)).start()

    def arch_done(self, req):
        tp = req[1]
        txt = ("Time={Время  }%02d/%02d/%02d %02d:%02d:%02d-%02d/%02d/%02d %02d:%02d:%02d;" % (
            req[2], req[3], req[4] % 100, req[5], req[6], req[7], req[8], req[9], req[10] % 100, req[11], req[12], req[13])
               + "Pabs={Абсол. давление *}0.10777МПа;T={Температура *}40.275°С;SrawV={Объем в р.у. с датчика}1.940536м3(р.у);"
               + "SvSTD={Объем газа в ст.у. }1.7758912м3(ст.у);Twrk={Время штатной работы}6м 43сек;Tnss={Время нештатных ситуаций}1ч53м17с;"
               + "NSS={Нештатные ситуации(время)  Сообщения[время]}P<min[40м 20сек] | Q1:нижняя уставка[1ч53м11с]\r\nрасход<Qmin(1ч50м56с);")
        b = (txt.encode(ENC) + b"\x00")
        if len(b) % 2: b += b"\x00"
        regs = struct.unpack(">%dH" % (len(b) // 2), b)
        for i, r in enumerate(regs): self.hr[8003 + i] = r
        self.hr[8002] = len(txt.encode(ENC)) + 1; self.hr[8001] = self.s.rid
        bad = YEAR2 and (req[4] >= 100 or req[10] >= 100)
        self.s.st = 4 if bad else (2 if 1 <= tp <= 10 else 6)

    def refresh(self):
        n = datetime.datetime.now()
        for i, v in enumerate((n.day, n.month, n.year % 100 if YEAR2 else n.year, n.hour, n.minute, n.second)):
            self.hr[1800 + i] = v
        if not hasattr(self.hr, "x"): pass
        self.hr[8000] = self.s.st
        for a in (1800, 1801, 1802, 1803, 1804, 1805): HRI[a] = 1


class Err(Exception):
    def __init__(self, c): self.code = c


class Srv(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=1502); ap.add_argument("--off", type=int, default=0)
    ap.add_argument("--swap", action="store_true"); ap.add_argument("--year2", action="store_true")
    ap.add_argument("--oil", action="store_true"); ap.add_argument("--inputs", type=int, default=8); ap.add_argument("--no-ntp", action="store_true")
    a = ap.parse_args()
    if a.swap: P32[:] = [2, 3, 0, 1]; P64[:] = [6, 7, 4, 5, 2, 3, 0, 1]
    NIN = a.inputs; YEAR2 = a.year2; OIL = a.oil; NTP = not a.no_ntp
    setup()
    for x in range(1800, 1806): HRI[x] = 1; HR[x] = 0
    s = Srv(("127.0.0.1", a.port), H); s.off = a.off
    print("эмулятор УВП-280 на 127.0.0.1:%d, смещение адреса %+d" % (a.port, a.off)); s.serve_forever()
