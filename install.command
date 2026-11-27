#!/bin/bash
# FanOut installer for macOS and Linux. Double-click it (or run ./install.command).
# Safe to run again at any time (it also repairs).
cd "$(dirname "$0")" || exit 1
echo
echo "  FanOut installer"
echo "  ================"
echo "  This sets everything up. The first time it can take a few minutes."
echo

PY=""
pick_python() {
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys, tkinter, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      PY="$(command -v "$c")"
      return 0
    fi
  done
  return 1
}

fail() {
  echo
  echo "  $1"
  echo
  read -r -p "  Press Enter to close this window. " _
  exit 1
}

if ! pick_python; then
  echo "  A suitable Python (3.10 or newer, with Tkinter) was not found."
  case "$(uname -s)" in
    Darwin)
      if ! command -v brew >/dev/null 2>&1; then
        echo "  I need Homebrew (the standard Mac installer for tools) to add Python."
        read -r -p "  Install Homebrew now? It will ask for your Mac password. [y/N] " ans
        [[ "$ans" =~ ^[Yy] ]] || fail "OK. Install Python 3.12 from python.org, then run this again."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || fail "Homebrew did not install."
        for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
      fi
      brew install python@3.12 python-tk@3.12 || fail "Homebrew could not install Python."
      ;;
    *)
      echo "  I will install Python with your system's package manager. It will ask for your password (sudo)."
      read -r -p "  Go ahead? [y/N] " ans
      [[ "$ans" =~ ^[Yy] ]] || fail "OK. Install Python 3.10+ with Tkinter yourself, then run this again."
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-tk
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-tkinter
      elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm python tk
      else
        fail "I don't know this system's package manager. Install Python 3.10+ with Tkinter and venv, then run this again."
      fi
      ;;
  esac
  pick_python || fail "Python still wasn't usable after installing. Please send a screenshot of this window."
fi

echo "  Using Python: $PY"
echo
"$PY" bootstrap.py install
code=$?
if [ "$code" -ne 0 ]; then
  fail "The install did not finish. Read the last lines above, fix what they say, then run this again."
fi
