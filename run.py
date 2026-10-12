# -*- coding: utf-8 -*-
"""Точка входа для сборки .exe (PyInstaller)."""
import sys
from uvpmon.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
