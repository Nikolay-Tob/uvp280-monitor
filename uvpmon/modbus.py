# -*- coding: utf-8 -*-
"""Минимальный клиент Modbus TCP (только стандартная библиотека)."""
import socket
import struct
import threading
import time


class ModbusError(Exception):
    """Прибор вернул исключение Modbus (02 - нет такого адреса/неполное чтение, 06 - занят...)."""

    def __init__(self, code, text=""):
        super().__init__(text or "Modbus exception %d" % code)
        self.code = code


class LinkError(Exception):
    """Нет связи с прибором (сокет, таймаут)."""


class Client:
    MAX_READ = 125
    MAX_WRITE = 123

    def __init__(self, host, port=502, unit=1, timeout=5.0, delay=0.01, attempts=3):
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.timeout, self.delay, self.attempts = float(timeout), float(delay), attempts
        self.off = 0                  # адрес_в_запросе = адрес_в_карте + off
        self.sock = None
        self.tid = 0
        self.lock = threading.RLock()
        self.reconnects = 0
        self.errors = 0
        self.last_ms = None
        self.on_reconnect = None      # вызывается после повторного соединения

    # ---- соединение ----
    def connect(self):
        self.close()
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise LinkError("не удалось подключиться: %s" % e)
        s.settimeout(self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except OSError:
                pass

    @property
    def connected(self):
        return self.sock is not None

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            c = self.sock.recv(n - len(buf))
            if not c:
                raise ConnectionError("соединение закрыто прибором")
            buf += c
        return buf

    def _once(self, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        tid = self.tid
        self.sock.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, self.unit) + pdu)
        while True:
            h = self._recv(7)
            rtid, _, ln, _ = struct.unpack(">HHHB", h)
            body = self._recv(ln - 1)
            if rtid == tid:
                return body
            # устаревший ответ на прежний запрос - пропускаем

    def txn(self, pdu):
        with self.lock:
            last = None
            for attempt in range(self.attempts):
                try:
                    if self.sock is None:
                        self.connect()
                        if attempt > 0 or self.reconnects:
                            pass
                    t0 = time.time()
                    body = self._once(pdu)
                    self.last_ms = int((time.time() - t0) * 1000)
                    if self.delay:
                        time.sleep(self.delay)
                    if body[0] & 0x80:
                        raise ModbusError(body[1])
                    return body
                except ModbusError:
                    raise
                except (OSError, ConnectionError, struct.error) as e:
                    last = e
                    self.errors += 1
                    self.close()
                    if attempt + 1 < self.attempts:
                        time.sleep(0.15 * (attempt + 1))
                        self.reconnects += 1
                        try:
                            self.connect()
                        except LinkError as e2:
                            last = e2
                            continue
                        if self.on_reconnect:
                            self.on_reconnect()
            raise LinkError("сбой связи: %s" % last)

    # ---- чтение / запись ----
    def read(self, fc, addr, count):
        """fc=3 (Holding) или 4 (Input). Возвращает список uint16. Читает кусками по 125."""
        out = []
        while count > 0:
            n = min(count, self.MAX_READ)
            a = addr + self.off
            if a < 0:
                raise ModbusError(2, "адрес < 0")
            b = self.txn(struct.pack(">BHH", fc, a, n))
            ln = b[1]
            out += list(struct.unpack(">%dH" % (ln // 2), b[2:2 + ln]))
            addr += n
            count -= n
        return out

    def hr(self, addr, count=1):
        return self.read(3, addr, count)

    def ir(self, addr, count=1):
        return self.read(4, addr, count)

    def write(self, addr, values):
        values = [int(v) & 0xFFFF for v in values]
        if len(values) > self.MAX_WRITE:
            raise ValueError("слишком длинная запись")
        data = struct.pack(">%dH" % len(values), *values)
        self.txn(struct.pack(">BHHB", 16, addr + self.off, len(values), len(data)) + data)
