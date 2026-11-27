# Install FanOut-Setup silently on this Windows machine and check it the way a person would use it.
# Used by CI after build_windows.ps1. Exits non-zero on the first problem.
$ErrorActionPreference = "Stop"
function Check($ok, $what) { if (-not $ok) { throw "FAILED: $what" } ; Write-Host "OK: $what" }
function RunApp($exe, $arg) { (Start-Process $exe -ArgumentList $arg -Wait -PassThru).ExitCode }

$setup = (Get-ChildItem dist\FanOut-Setup-*.exe | Select-Object -First 1).FullName
$env:FANOUT_HOME = Join-Path $env:RUNNER_TEMP "fanout-data"
$env:FANOUT_NO_UPDATE_CHECK = "1"
$app = "$env:LOCALAPPDATA\Programs\FanOut\FanOut.exe"

$code = (Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru).ExitCode
Check ($code -eq 0) "installer finished without questions (exit $code)"
Check (Test-Path $app) "FanOut.exe installed for this user, no admin needed"
Check (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\FanOut.lnk") "Start menu shortcut"
Check (Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "FanOut.lnk")) "Desktop shortcut"
Check ((Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{D2FA2C88-CEE7-4B59-8246-7428311F35C0}_is1").DisplayName -eq "FanOut") "listed in Apps & features"

Check ((RunApp $app "--self-test") -eq 0) "installed app starts its built-in Chromium"
Check ((Get-Content "$env:FANOUT_HOME\logs\fanout.log" -Raw) -match "Self-test passed") "self-test written to fanout.log"
Check ((RunApp $app "--smoke-test") -eq 0) "installed app opens its window and shows the first screen"

"keep me" | Set-Content "$env:FANOUT_HOME\marker.txt"
$code = (Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS" -Wait -PassThru).ExitCode
Check ($code -eq 0) "installing again over the top (an update) works"
Check ((RunApp $app "--self-test") -eq 0) "app still works after the update"
Check (Test-Path "$env:FANOUT_HOME\marker.txt") "settings and results kept through the update"

& "$env:LOCALAPPDATA\Programs\FanOut\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
for ($i = 0; $i -lt 60 -and (Test-Path $app); $i++) { Start-Sleep 1 }
Check (-not (Test-Path $app)) "uninstall removes the program"
Check (-not (Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "FanOut.lnk"))) "uninstall removes the Desktop shortcut"
Check (Test-Path "$env:FANOUT_HOME\marker.txt") "uninstall keeps the person's own data"
Write-Host "ALL INSTALLER CHECKS PASSED"
