# -*- coding: utf-8 -*-
"""Разрешённые для записи регистры (белый список) и их ограничения."""
from .env_table import ENV_SECTIONS
from . import regs as R

# класс доступа: 'env' - параметры среды (базовый уровень), 'time' - часы/NTP, 'oil' - управление узлом (нужен «Наладчик»)
WRITABLE = {}          # addr -> (type, class, label)


def _add(addr, typ, cls, label):
    WRITABLE[addr] = (typ, cls, label)


for _title, _rows in ENV_SECTIONS:
    for _a, _t, _l, _u in _rows:
        if "Количество ошибок" in _l:
            continue
        _add(_a, _t, "env", "%s: %s" % (_title, _l))

_add(1005, "uint32", "time", "IP-адрес NTP-сервера")
_add(1007, "int16", "time", "Команда синхронизации времени")
_add(1009, "int16", "time", "Поправка часов")

_add(17100, "uint16", "oil", "Маска отключения линий СИКН")
for _k, _a, _t, _l in R.HR_ROWS:
    if 17200 <= _a <= 17626:
        _add(_a, _t, "oil", R.DENS_LABELS.get(_a, _l))
_add(18000, "uint16", "oil", "КМХ: эталонная линия")
_add(18001, "uint16", "oil", "КМХ: контролируемая линия")
_add(18002, "uint32", "oil", "КМХ: количество импульсов / время, мин")
_add(18004, "uint32", "oil", "КМХ: счётчик выполненных КМХ")
_add(18006, "uint32", "oil", "КМХ: команда запуска/прерывания")
for _base in (18500, 18600):
    _add(_base + 1, "int16", "oil", "Пробоотборник: длительность импульса, мс")
    _add(_base + 2, "int32", "oil", "Пробоотборник: количество проб")
    _add(_base + 4, "float", "oil", "Пробоотборник: период / масса на импульс")

TIME_COMMANDS = (24680, 13579)
KMH_COMMANDS = (R.KMH_CMD_START, R.KMH_CMD_ABORT)


def describe(addr):
    return WRITABLE.get(addr)
