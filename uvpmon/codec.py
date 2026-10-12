# -*- coding: utf-8 -*-
"""Кодирование/декодирование многорегистровых значений с учётом порядка байт прибора.

Порядок задаётся перестановкой: байт номер i «нормального» (big-endian) значения лежит
в сыром потоке байт регистров на позиции perm[i]. По умолчанию 0123h / 01234567h
(тождественная перестановка); у части приборов - 2301h / 67452301h.
"""
import itertools
import math
import struct

ENC = "cp1251"

IDENT32 = (0, 1, 2, 3)
IDENT64 = (0, 1, 2, 3, 4, 5, 6, 7)

CONST_I32 = 1234567890
CONST_F32 = 123.4567
CONST_F64 = 123.45678901234


def raw_bytes(regs):
    return struct.pack(">%dH" % len(regs), *[r & 0xFFFF for r in regs])


def _arrange(regs, perm):
    raw = raw_bytes(regs)
    return bytes(raw[p] for p in perm)


def _unarrange(be, perm):
    raw = bytearray(len(be))
    for i, p in enumerate(perm):
        raw[p] = be[i]
    return list(struct.unpack(">%dH" % (len(raw) // 2), bytes(raw)))


def u16(r, i=0):
    return r[i] & 0xFFFF


def s16(r, i=0):
    v = r[i] & 0xFFFF
    return v - 65536 if v >= 32768 else v


class Codec:
    def __init__(self, p32=IDENT32, p64=IDENT64):
        self.p32 = tuple(p32)
        self.p64 = tuple(p64)

    # ---- чтение ----
    def u32(self, r, i=0):
        return struct.unpack(">I", _arrange(r[i:i + 2], self.p32))[0]

    def i32(self, r, i=0):
        return struct.unpack(">i", _arrange(r[i:i + 2], self.p32))[0]

    def f32(self, r, i=0):
        return struct.unpack(">f", _arrange(r[i:i + 2], self.p32))[0]

    def f64(self, r, i=0):
        return struct.unpack(">d", _arrange(r[i:i + 4], self.p64))[0]

    # ---- запись ----
    def enc_u32(self, v):
        return _unarrange(struct.pack(">I", v & 0xFFFFFFFF), self.p32)

    def enc_i32(self, v):
        return _unarrange(struct.pack(">i", v), self.p32)

    def enc_f32(self, v):
        return _unarrange(struct.pack(">f", v), self.p32)

    def enc_f64(self, v):
        return _unarrange(struct.pack(">d", v), self.p64)

    def by_type(self, typ, r, i=0):
        t = typ.lower()
        if t == "int16":
            return s16(r, i)
        if t == "uint16":
            return u16(r, i)
        if t == "int32":
            return self.i32(r, i)
        if t == "uint32":
            return self.u32(r, i)
        if t == "float":
            return self.f32(r, i)
        if t == "double":
            return self.f64(r, i)
        raise ValueError(typ)


TYPE_LEN = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2, "float": 2, "double": 4}


def type_len(typ):
    return TYPE_LEN[typ.lower()]


def find_p32(regs):
    """regs: два регистра HR110, int32 = 1234567890. Возвращает перестановку или None."""
    raw = raw_bytes(regs[:2])
    for perm in itertools.permutations(range(4)):
        if bytes(raw[p] for p in perm) == struct.pack(">i", CONST_I32):
            # предпочитаем «естественные» варианты: словная и байтовая перестановки
            return perm
    return None


def _natural_perms64():
    """Кандидаты для 64 бит: перестановка слов (24) x обмен байт внутри слов (2)."""
    out = []
    for wp in itertools.permutations(range(4)):
        for sw in (False, True):
            perm = []
            for w in wp:
                a, b = 2 * w, 2 * w + 1
                perm += [b, a] if sw else [a, b]
            out.append(tuple(perm))
    return out


def find_p64(regs):
    raw = raw_bytes(regs[:4])
    want = struct.pack(">d", CONST_F64)
    for perm in _natural_perms64():
        got = bytes(raw[p] for p in perm)
        if got == want:
            return perm
        v = struct.unpack(">d", got)[0]
        if abs(v - CONST_F64) < 1e-9:
            return perm
    return None


def hint_from_hr100(hr100, hr101):
    """Порядок, который сообщает сам прибор (HR100/HR101): для диагностики."""
    return "%04Xh / %s" % (hr100 & 0xFFFF, "".join("%04X" % (x & 0xFFFF) for x in hr101))


def clean(v):
    """JSON не умеет NaN/Inf - заменяем на None."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def regs_to_text(regs):
    b = raw_bytes(regs).split(b"\x00", 1)[0]
    return b.decode(ENC, errors="replace")
