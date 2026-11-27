# Changelog

All notable changes. Newest first. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] - 2026-11-27

First published version.

### Added
- Brave profile scan: finds Brave and lists profiles with their real names (a guess, never certainty, for "appears to be running").
- App-managed browser windows, one per account, tiled side by side with a numbered coloured banner; logins are remembered; optional, limited login import (cookies, Preferences, Local State only).
- Chat-box mapping: auto-detect, a visual "click it" picker, a test that types a harmless word and never presses send; mappings saved per account and site.
- Send to all: sends a different prompt to each account at the same time, waits until each answer has finished streaming (text steady and no Stop button), keeps going if one account fails or times out, and stops cleanly on a CAPTCHA or usage limit without trying to get around it.
- Results: side-by-side Compare view, Copy All, export to Markdown and JSON, automatic saving of every run, searchable prompt history, Retry failed, Cancel.
- One-file Windows installer **FanOut-Setup.exe**: installs for your user with no admin password, adds Desktop and Start-menu shortcuts with FanOut's icon, carries its own Chromium, has a normal uninstaller. The installed app updates itself (downloads the new installer, checks its fingerprint, installs quietly, reopens).
- FanOut's own icon on the window, program, installer and shortcuts.
- `--self-test` checks that the built-in browser starts.
- Run-from-source installers for Windows (`INSTALL.bat`, `run.bat`) and Mac/Linux (`install.command`, `run.command`) that install Python if needed, build a private environment, add the browser engine, make a Desktop shortcut, and repair themselves on every launch.
- Updater: checks GitHub Releases, verifies the SHA-256, backs up, installs, restarts, and rolls back automatically on failure. Never runs while browser windows are open.
- Settings screen: timing numbers, Be gentle, update check, repair, open logs and results folders.
- Tests, lint (ruff), CI on Windows/macOS/Linux, PyInstaller specs and a release workflow.

### Fixed (found in a code review before release)
- A site that empties its chat box slowly could get the same prompt twice. FanOut now also counts a new message or the site's Stop button as "sent".
- A problem during a run, a mapping or a pick no longer throws you back to the Scan screen with windows still open. Only a failed window opening goes back, after closing what opened.
- If the browser helper can't start, FanOut says so at once instead of freezing for 10 seconds.
- Saving the question history can no longer stop a run from being sent.
- "Close Brave for me" on Windows now closes only your own Brave, never FanOut's windows.
- Temporary profile copies are deleted after leftover browsers stop, so Windows doesn't leave them behind.
- Updates only download from https addresses.

### Known limits
- Automated tests (including window tests and a start-up check) pass on Windows, macOS and Linux CI machines. The waiting-for-answers engine was run in a real Chrome against local pretend chat pages.
- Not yet tried with real Brave or real chat sites.
- The Windows installer was installed, opened, updated and uninstalled on GitHub's Windows test machine, not yet on a home PC. The Mac and Linux packaged builds are not tested.
