# FanOut

FanOut opens several browser windows, one per chat account you already own. You log in to each one yourself.
Then you type a **different question for each window**, press one button, and FanOut sends them all at the
same time, waits for every answer to finish, and shows the answers **side by side**.

You can copy any answer, copy them all, and save everything as a Markdown or JSON file.

> **Status (honest):** version 0.2.1's automated tests pass on Windows, macOS and Linux (GitHub's test machines). The Windows
> installer was installed, opened, updated and uninstalled on GitHub's Windows machine. It has **not yet been tried with real
> Brave or on real chat sites.** If something
> doesn't work on your machine, please open an issue and attach `fanout.log` (see Troubleshooting).

## Install (Windows)

1. Open the **[Releases page](https://github.com/nyenz/agent/releases/latest)** and click **FanOut-Setup-x.y.z.exe** to download it.
2. Open your **Downloads** folder and **double-click FanOut-Setup**.
3. Windows may show a blue box, "Windows protected your PC". Press **More info**, then **Run anyway**.
   (It appears because the installer isn't signed with a paid certificate. It is the same file GitHub built from this code.)
4. Press **Install**. No admin password is needed and nothing else to download: the browser engine is inside.
5. FanOut opens. A **FanOut** shortcut with its icon is on your Desktop and in the Start menu.

**Updates:** when a new version is out, FanOut shows a green bar. Press **Update now**. It downloads the new installer,
checks its fingerprint, installs it by itself and opens FanOut again. You never download anything by hand.

**Uninstall:** Start menu, **Settings**, **Apps**, find **FanOut**, then **Uninstall**. Your settings, logins and results stay
in `%APPDATA%\FanOut` in case you install again; delete that folder if you want them gone.

**Mac or Linux:** on the Releases page, download **Source code (zip)**, extract it, then double-click `install.command`
(on Linux you may need to right-click it and choose "Run as a program", or run `./install.command` in a terminal).
It asks before installing anything with your system's installer, and asks for your password only for that. It makes a
Desktop shortcut, checks and repairs itself on every start, and updates itself the same way (green bar, **Update now**).

## First run, step by step

1. **Choose your accounts.** FanOut lists your Brave profiles. Tick up to 6. (No Brave? It offers its own built-in
   Chromium browser instead.)
2. Press **Continue**. FanOut opens one window per account, side by side, each with a coloured strip at the top:
   "ACCOUNT 1", "ACCOUNT 2", and so on. These windows belong to FanOut and are separate from your normal Brave.
3. **Log in yourself** in each window, on whichever chat site you like (a different one in each is fine).
   The windows open like an ordinary browser (see **Compatibility mode** below), so a site's own
   "Verify you are human" step is something you tick by hand, once, in the window.
   FanOut never asks for or sees your password. Your login is remembered for next time.
4. When a window shows **READY**, FanOut has found its chat box. If it can't, press **Map this account**, or
   **Fix by clicking** and click the chat box yourself. You never type any code.
5. Press **All ready - continue**.
6. Type a question for each account (or one shared question for everyone) and press **SEND TO ALL**
   (or **Ctrl+Enter**).
7. Watch the answers fill in. Open the **Compare** tab to see them side by side. Along the bottom: **Copy all**, **Export .md**, **Export .json**, **Retry failed**, **Clear**. **History** (top right) finds earlier questions.

| Keys | What they do |
|---|---|
| Ctrl+Enter | Send to all |
| Ctrl+Shift+C | Copy all answers |
| Esc | Cancel the run |

Every answer is saved automatically in the `results` folder (Settings, then **Open results folder**), as one `.md`
file per account plus `run.json`. Your questions are kept in a searchable history.

## Settings

Press **Settings** at the top right.

- **Wait until an answer stops changing** (default 3 seconds): FanOut treats an answer as finished when its text hasn't
  changed for this long and the site's own Stop button is gone. Raise it for slow, stop-and-start sites.
- **Give up on an account after** (default 180 seconds, up to 1800): one slow account never stops the others.
- **Gap between two sends from one account**, and **Be gentle** (doubles that gap, at least 10 seconds).
- **Compatibility mode: open windows as a normal browser** (on by default). FanOut starts each window as an ordinary browser
  with its own profile folder, you log in by hand, and FanOut connects to it afterwards. This is what lets sites that ask you to
  prove you are human let you in. If a window can't be opened this way, FanOut opens it the standard way and says so.
  It applies the next time windows open. Windows that reuse your real Brave profiles always open the standard way.
- **Check for updates**, **Repair installation**, **Open logs folder**, **Open results folder**.

## Updates

When a newer version is published, a green bar appears: **Update now**, **Later**, **Skip this version**.
An update downloads the new version, checks its fingerprint (SHA-256), saves a backup of the current code into
`.backup`, installs, restarts, and **goes back to your old version by itself** if anything fails.
It never runs while browser windows are open. Your settings, logins and results are stored outside the
program folder, so updates can't touch them. If you are offline, FanOut says nothing.

## Troubleshooting

| What you see | What to do |
|---|---|
| A window never shows READY | Make sure you are on the chat page and logged in. Press **Map this account**; if that fails, **Fix by clicking** and click the chat box. |
| "That browser profile is already open somewhere else" | Close Brave completely, or choose **App-managed windows** (recommended). |
| A run says an account hit a CAPTCHA or a usage limit | FanOut stops that account and never tries to get around it. Solve it yourself in that window, then press **Retry failed**. |
| An answer is cut short | Raise "Wait until an answer stops changing" in Settings. |
| An account timed out | The other accounts are unaffected. Press **Retry failed**, or raise the timeout in Settings. A screenshot is saved next to `fanout.log`. |
| Nothing opens | Windows: run **FanOut-Setup** again (it repairs and keeps your data). Mac/Linux: run `install.command` again. |
| Anything else | **Settings, then Repair installation.** Still stuck? See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). |

**Where things live.** Your settings, logins, results and logs are in FanOut's data folder, not in the program folder:

| System | Folder |
|---|---|
| Windows | `%APPDATA%\FanOut` (paste that into the File Explorer address bar) |
| Mac | `~/Library/Application Support/FanOut` |
| Linux | `~/.local/share/FanOut` |

The log is `logs/fanout.log` in that folder (Settings has an **Open logs folder** button).

## Privacy

- FanOut runs entirely on your computer. There is no account, no tracking and no server of ours.
- It **never asks for, reads, stores or types a password.** You log in yourself in a normal browser window.
- Logins live inside FanOut's own browser windows (a separate profile folder per account), the same way a normal browser remembers you.
  The optional "copy my existing logins" button copies only the cookie file, Preferences and Local State, and never saved passwords, history, extensions or caches.
- Your questions and answers are saved only on your computer, in the results folder. `fanout.log` records technical details and never your prompts, answers, cookies or passwords.
- The only internet request FanOut makes by itself is the update check to GitHub (turn it off in Settings).

## Limits you should know about

- **Profile locks.** Brave allows one program per profile folder. That is why FanOut uses its own windows by default.
  The advanced "reuse my real Brave profiles" mode needs Brave fully closed, and newer Chromium browsers may refuse
  automation on the default folder. FanOut then works on a private copy and tells you.
- **"Appears to be running" is a guess.** FanOut looks at Brave's lock files and the list of open programs. It can be wrong, so the table says "appears".
- **Sites change.** Chat sites redesign their pages. When that breaks a mapping, use **Map this account** again.
- **No tricks.** FanOut doesn't fake or hide anything about the browser, doesn't solve CAPTCHAs, and doesn't get around rate limits. Compatibility mode only starts the window the ordinary way; you do any "Verify you are human" step yourself. A site can still challenge or block automated use. FanOut then stops that account and leaves it to you. It slows down on purpose instead (Be gentle).
- **Not tested everywhere yet** (see Status above).

## Terms of service

FanOut drives your own logged-in accounts. Some sites' terms restrict automation or sending messages in bulk.
Check the terms of each site, keep your volume low, and prefer official APIs where you can. **You are responsible for how you use it.**

## Why each package is needed

| Package | Why | Cost |
|---|---|---|
| **Playwright** | The only reliable way to open browser windows, type into a chat box and read the answer as it streams in. | Large: it brings a browser engine (Chromium). |
| **psutil** | Best-effort guess whether Brave is running, and making sure no browser windows are left behind when FanOut closes. | Tiny. |
| **pyperclip** | Copy to the clipboard. If it ever fails, FanOut uses Tk's own clipboard. | Tiny. |
| Tkinter, `urllib` and the rest | Part of Python itself: the windows, and the update check. No extra download. | None. |

Developer-only extras (not installed for normal use): pytest, ruff, mypy, PyInstaller.

## Download size

The Windows installer is about **180 MB** and FanOut takes about **570 MB** once installed (measured on GitHub's Windows
machine, 2026-11-27). Almost all of it is the Chromium browser engine, carried inside so the app works with no
other downloads. Mac and Linux download it once during `install.command` instead.

## For developers

Run from source on Windows with `INSTALL.bat` (installs Python if needed, then the same steps as `install.command`).
Build the installer with `pwsh packaging/build_windows.ps1` (needs Inno Setup 6); CI builds it, then installs, opens,
updates and uninstalls it on every push (`packaging/test_windows_installer.ps1`).

```
python -m pip install -r requirements-dev.txt
python -m pytest          # tests; real-browser tests use Chrome/Chromium and skip if none is found
ruff check .              # lint
python app.py --smoke-test
```

Layout: `app.py` entry; `fanout/` the program (`brave.py` profiles, `sessions.py` windows and logins, `mapping.py` + `js/` finding the chat box,
`engine.py` sending and waiting, `results.py` + `runview.py` answers, `updater.py`, `settings*.py`); `bootstrap.py` installer and repair;
`packaging/` PyInstaller specs. `FANOUT_STATE.json` records build status honestly.

## Licence

MIT. See [LICENSE](LICENSE).
