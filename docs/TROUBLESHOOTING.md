# Troubleshooting

Start with **Settings, then Repair installation**. It rebuilds anything missing and keeps your settings, logins and results.

## Where is `fanout.log`?

Settings, then **Open logs folder**. Or open it by hand:

| System | File |
|---|---|
| Windows | `%APPDATA%\FanOut\logs\fanout.log` |
| Mac | `~/Library/Application Support/FanOut/logs/fanout.log` |
| Linux | `~/.local/share/FanOut/logs/fanout.log` |

It never contains passwords, cookies, prompts or answers. Attach it to a bug report.

## Installing

| Problem | Fix |
|---|---|
| Windows says "Windows protected your PC" | Press **More info**, then **Run anyway**. It appears because the installer isn't signed with a paid certificate. |
| FanOut-Setup says Nyenz Agent is still running | Close Nyenz Agent (and its browser windows), then press **Retry**. |
| You want to repair the Windows app | Run **FanOut-Setup** again. It reinstalls the program and keeps your settings, logins and results. |
| (Mac/Linux or running from source) The installer says it couldn't install Python | Install Python 3.12 from python.org. On the first screen tick **Add python.exe to PATH**. Then double-click `INSTALL.bat` again. |
| "Tkinter is missing" | Windows: reinstall Python from python.org and keep **tcl/tk** ticked. Mac: `brew install python-tk@3.12`. Linux: `sudo apt install python3-tk` (Fedora `python3-tkinter`, Arch `tk`). Then run the installer again. |
| "I couldn't install Nyenz Agent's packages" | Check your internet, then run the installer again. Behind a work proxy, ask your IT team to allow `pypi.org`. |
| "I couldn't download the built-in Chromium" | Check your internet and run the installer again. Some work networks block it. |
| Mac: the file "can't be opened" | Right-click `install.command`, press **Open**, then **Open** again. |
| Linux: double-click does nothing | Open a terminal in the folder and run `./install.command`. |

## Browser windows

| Problem | Fix |
|---|---|
| "That browser profile is already open somewhere else" | Close Brave completely (also check the system tray), or use **App-managed windows**. |
| Nyenz Agent says it couldn't start the browser | If you chose the built-in Chromium: Settings, **Repair installation**. If you use Brave: check it is installed. |
| A window is left open after Nyenz Agent closes | Close it by hand. If it keeps happening, send `fanout.log`. |
| A window never shows READY | Be on the chat page, logged in, then **Map this account** or **Fix by clicking**. |
| A site keeps showing "Verify you are human" again and again | Check Settings: **Compatibility mode** should be ticked. Close the windows (**Close windows and go back**), open them again, tick the box in the window yourself, then log in. If you chose "Reuse my real Brave profiles", switch back to **App-managed windows**, because compatibility mode can't be used on a real profile. Some sites may still refuse; Nyenz Agent doesn't try to get around that. |
| A note says the window "didn't start in compatibility mode" | Nyenz Agent opened it the standard way instead, so it still works. Close any stuck browser windows and try again; if it repeats, send `fanout.log`. |
| Windows overlap or are off-screen | Close Nyenz Agent and start it again; it re-tiles windows to your screen. |

## Sending and answers

| Problem | Fix |
|---|---|
| The prompt is typed but not sent | Use **Fix by clicking** and click the site's Send button. Nyenz Agent also tries Enter, Ctrl+Enter and Cmd+Enter by itself. |
| The answer is cut short | Raise **Wait until an answer stops changing** (Settings). Some sites pause while "thinking". |
| An account timed out | Raise **Give up on an account after** (Settings), then **Retry failed**. A screenshot of that moment is saved next to `fanout.log`. |
| A CAPTCHA or "too many requests" message | Nyenz Agent stops that account and never solves or avoids it. Handle it in that window, wait a while, then **Retry failed**. Turn on **Be gentle** to slow down. |
| The answer has extra buttons or text such as "Copy" | Tell us which site, and attach `fanout.log`. The reply area may need a new mapping (**Map this account**). |

## Updates

| Problem | Fix |
|---|---|
| "Please finish or cancel the current run" | Updates never run while browser windows are open. Close the windows, then press **Update now**. |
| "The download didn't match its checksum" | Nothing was changed. Try again later; if it repeats, download the newest zip from the Releases page instead. |
| "The update didn't finish, so I went back to your old version" | That is the automatic rollback working. Your old version still works. Send `fanout.log`. |
| Old copies of the program in `.backup` | Safe to delete. Nyenz Agent keeps the newest three. |

## Starting over

To forget all logins and settings, close Nyenz Agent and delete Nyenz Agent's **data folder** (see the table above).
The program folder can stay. This cannot be undone.
