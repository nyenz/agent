# Build dist\FanOut (PyInstaller, with Chromium inside) and dist\FanOut-Setup-<version>.exe (+ .sha256).
# Run from the repo root on Windows:  pwsh packaging/build_windows.ps1
$ErrorActionPreference = "Stop"
$version = (Select-String -Path fanout\__init__.py -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Building FanOut $version"

$env:PLAYWRIGHT_BROWSERS_PATH = "0"   # Chromium goes inside the playwright package, so the build carries it
python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install failed" }
python -m PyInstaller --noconfirm packaging/windows.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    choco install innosetup -y --no-progress | Out-Null
}
if (-not (Test-Path $iscc)) { throw "Inno Setup is not available" }
& $iscc "/DAppVersion=$version" packaging\fanout.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = "dist\FanOut-Setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  FanOut-Setup-$version.exe" | Set-Content -NoNewline -Encoding ascii "$setup.sha256"
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
$folder = [math]::Round(((Get-ChildItem dist\FanOut -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "SIZE: installer $mb MB, installed program $folder MB"
