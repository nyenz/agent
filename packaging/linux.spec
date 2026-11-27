# PyInstaller spec for linux. Build from the repo root:  python -m PyInstaller --noconfirm packaging/linux.spec
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(Path(SPECPATH)))
from _common import pieces  # noqa: E402

datas, binaries, hidden = pieces(ROOT)
a = Analysis([str(ROOT / "app.py")], pathex=[str(ROOT)], datas=datas, binaries=binaries, hiddenimports=hidden,
             excludes=["tests", "pytest", "mypy", "ruff"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="FanOut", console=False,
          icon=str(ROOT / "packaging" / "icons" / "fanout.png"))
coll = COLLECT(exe, a.binaries, a.datas, name="FanOut")
