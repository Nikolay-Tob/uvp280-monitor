# -*- coding: utf-8 -*-
"""Проверки ядра на эмуляторе: python -m unittest discover -s tests"""
import os
import socket
import subprocess
import sys
import time
import unittest
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from uvpmon.codec import Codec, find_p32, find_p64, CONST_I32, CONST_F64  # noqa
from uvpmon.device import Device, parse_archive  # noqa


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Emu:
    def __init__(self, *args):
        self.port = free_port()
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "uvp_emulator.py"), "--port", str(self.port)] + list(args),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("эмулятор не запустился")

    def stop(self):
        self.p.terminate()
        self.p.wait(5)


class CodecTests(unittest.TestCase):
    def test_roundtrip_all_orders(self):
        for p32, p64 in (((0, 1, 2, 3), tuple(range(8))), ((2, 3, 0, 1), (6, 7, 4, 5, 2, 3, 0, 1)),
                         ((1, 0, 3, 2), (1, 0, 3, 2, 5, 4, 7, 6)), ((3, 2, 1, 0), (7, 6, 5, 4, 3, 2, 1, 0))):
            c = Codec(p32, p64)
            self.assertEqual(find_p32(c.enc_i32(CONST_I32)), p32)
            self.assertEqual(c.i32(c.enc_i32(CONST_I32)), CONST_I32)
            self.assertAlmostEqual(c.f32(c.enc_f32(123.4567)), 123.4567, places=3)
            self.assertEqual(find_p64(c.enc_f64(CONST_F64)), p64)

    def test_archive_parse(self):
        t = ("Time={Время}13/10/26 01:00:00-13/10/26 03:00:00;Pabs={Давл}0.10777МПа;Twrk={Время}6м 43сек;"
             "SvSTD={Объем}1.77м3(ст.у);NSS={НС}P<min[40м]\r\nQ1;")
        it = {i["tag"]: i for i in parse_archive(t)}
        self.assertNotIn("value", it["Time"])
        self.assertNotIn("value", it["Twrk"])
        self.assertEqual(it["Pabs"]["unit"], "МПа")
        self.assertAlmostEqual(it["SvSTD"]["value"], 1.77)
        self.assertIn("\r\n", it["NSS"]["raw"])


class DeviceTests(unittest.TestCase):
    def check(self, *args, expect_order):
        emu = Emu(*args)
        try:
            d = Device({"id": "x", "host": "127.0.0.1", "port": emu.port, "unit": 1})
            info = d.identify()
            self.assertTrue(info["order_ok"])
            self.assertEqual(info["order"], expect_order)
            self.assertTrue(d.poll({"ntp", "inputs", "level"}))
            self.assertAlmostEqual(d.state["pipes"][2]["p_abs"], 420000.0, places=0)
            now = datetime.datetime.now().replace(minute=0, second=0, microsecond=0)
            r = d.archive(1, now - datetime.timedelta(hours=2), now)
            self.assertEqual(r["status"], 2)
            self.assertTrue(r["items"])
            return d
        finally:
            emu.stop()

    def test_default_order(self):
        d = self.check(expect_order="0123 / 01234567")
        self.assertEqual(d.info["offset"], 0)

    def test_swapped_order(self):
        self.check("--swap", expect_order="2301 / 67452301")

    def test_offset_minus_one(self):
        d = self.check("--off", "-1", expect_order="0123 / 01234567")
        self.assertEqual(d.info["offset"], -1)

    def test_two_digit_year(self):
        d = self.check("--year2", expect_order="0123 / 01234567")
        self.assertFalse(d.year4)

    def test_oil_and_no_ntp(self):
        emu = Emu("--oil", "--no-ntp")
        try:
            d = Device({"id": "x", "host": "127.0.0.1", "port": emu.port, "unit": 1})
            d.identify()
            self.assertTrue(d.caps["oil"])
            self.assertFalse(d.caps["ntp"])
            self.assertTrue(d.poll({"oil", "ntp"}))
            self.assertIsNone(d.state.get("ntp"))
            self.assertTrue(d.state["oil"])
            self.assertTrue(d.read_oil_period("hour"))
        finally:
            emu.stop()


class WriteTests(unittest.TestCase):
    def test_login_env_time_oil(self):
        emu = Emu("--oil", "--drift", "250")
        try:
            d = Device({"id": "x", "host": "127.0.0.1", "port": emu.port, "unit": 1})
            d.identify()
            # параметры среды: запись и чтение обратно
            r = d.write_reg(1000, 750.5)
            self.assertAlmostEqual(r["value"], 750.5, places=2)
            env = d.read_env()
            self.assertTrue(any(sec["title"] == "Нефть" for sec in env))
            # запрещённый регистр
            with self.assertRaises(ValueError):
                d.write_reg(1806, 1)
            # управление нефтью без входа - отказ
            d.read_level()
            with self.assertRaises(PermissionError):
                d.write_reg(17100, 2)
            # вход под пользователем 1 (эмулятор: пароль 12345)
            self.assertEqual(d.login(1, 12345), 2)
            d.write_reg(17100, 2)
            self.assertEqual(d.read_oil_control()["line_mask"], 2)
            self.assertEqual(d.logout(), 1)
            # подвод часов: дрейф 250 с -> несколько шагов
            res = d.time_sync_pc()
            self.assertTrue(res["ok"], res)
            self.assertGreaterEqual(len(res["steps"]), 3)
            with self.assertRaises(ValueError):
                d.write_reg(1007, 5)
        finally:
            emu.stop()


class SettingsTests(unittest.TestCase):
    def test_settings_roundtrip(self):
        import tempfile
        from uvpmon.manager import Manager
        path = os.path.join(tempfile.mkdtemp(), "devices.json")
        m = Manager(path)
        did = m.add({"name": "t", "host": "127.0.0.1", "port": 1502, "unit": 1})
        m.set_settings({"sel_interval": 3, "bg_interval": 20, "timeout": 8, "delay_ms": 50, "attempts": 2})
        c = m.devices[did].client
        self.assertEqual((c.timeout, c.attempts), (8.0, 2))
        self.assertAlmostEqual(c.delay, 0.05)
        m.update(did, {"name": "t", "host": "127.0.0.1", "port": 1502, "unit": 1, "timeout": 20})
        self.assertEqual(m.devices[did].client.timeout, 20.0)
        with self.assertRaises(ValueError):
            m.set_settings({"timeout": 500})
        m2 = Manager(path)
        self.assertEqual(m2.settings["bg_interval"], 20.0)
        self.assertEqual(m2.devices[did].client.timeout, 20.0)


if __name__ == "__main__":
    unittest.main()
