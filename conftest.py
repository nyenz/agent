"""Pytest root marker: its presence puts the repo root on sys.path so tests can import fanout."""

import gc
import os

import pytest

os.environ.setdefault("FANOUT_NO_UPDATE_CHECK", "1")  # no test may reach GitHub by accident


@pytest.fixture(autouse=True)
def _free_leftovers_on_the_main_thread():
    """Tk objects left behind by one test must not be freed later on some other thread (that stalls or aborts Tk)."""
    yield
    gc.collect()
