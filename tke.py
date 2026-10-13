#!/usr/bin/env python3
"""Run tke straight from a clone, no install needed:  python3 tke.py report"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_economy.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
