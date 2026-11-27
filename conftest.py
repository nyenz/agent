"""Pytest root marker: its presence puts the repo root on sys.path so tests can import fanout."""

import os

os.environ.setdefault("FANOUT_NO_UPDATE_CHECK", "1")  # no test may reach GitHub by accident
