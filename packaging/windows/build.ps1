param(
  [string]$Version = "1.0.0",
  [switch]$SkipTests,
  [switch]$Unsigned
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

if (!(Test-Path ".venv-windows")) { py -3.11 -m venv .venv-windows }
$Python = Join-Path $Root ".venv-windows\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -c packaging\windows\constraints.txt ".[dev,windows,windows-build]"
if (!$SkipTests) { & $Python -m pytest -q }

Remove-Item -Recurse -Force build,dist -ErrorAction SilentlyContinue
& $Python -m PyInstaller --clean --noconfirm packaging\windows\foundry-lite.spec

$SmokeData = Join-Path $env:TEMP ("foundry-lite-smoke-" + [guid]::NewGuid().ToString("N"))
$env:DIFOUNDRY_LITE_DATA_DIR = $SmokeData
$env:DIFOUNDRY_LITE_OPEN_BROWSER = "false"
& .\dist\FoundryLite\FoundryLite.exe --health-check
if ($LASTEXITCODE -ne 0) { throw "Packaged executable health check failed" }
Remove-Item -Recurse -Force $SmokeData -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force release\windows | Out-Null

function Sign-File([string]$Path) {
  if ($Unsigned -or !$env:WINDOWS_CERTIFICATE_BASE64) { return }
  $Pfx = Join-Path $env:TEMP "foundry-lite-signing.pfx"
  [IO.File]::WriteAllBytes($Pfx, [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_BASE64))
  $SignTool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Filter signtool.exe -Recurse | Sort-Object FullName -Descending | Select-Object -First 1
  if (!$SignTool) { throw "signtool.exe was not found" }
  & $SignTool.FullName sign /fd SHA256 /f $Pfx /p $env:WINDOWS_CERTIFICATE_PASSWORD /tr "http://timestamp.digicert.com" /td SHA256 $Path
  if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for $Path" }
  Remove-Item $Pfx -Force
}

# The executable must be signed before it is placed inside the ZIP and installer.
Sign-File ".\dist\FoundryLite\FoundryLite.exe"
Compress-Archive -Path .\dist\FoundryLite\* -DestinationPath ("release\windows\Dendritron-Foundry-Lite-$Version-Portable.zip") -Force

$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (!(Test-Path $Iscc)) { $Iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (!(Test-Path $Iscc)) { throw "Inno Setup 6 was not found" }
& $Iscc "/DMyAppVersion=$Version" "/DSourceRoot=$Root" packaging\windows\foundry-lite.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

$Installer = Get-ChildItem release\windows\Dendritron-Foundry-Lite-*-Setup.exe | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Sign-File $Installer.FullName

$CycloneDx = Join-Path $Root ".venv-windows\Scripts\cyclonedx-py.exe"
& $CycloneDx environment --output-format JSON --output-file release\windows\foundry-lite-sbom.json
if ($LASTEXITCODE -ne 0) { throw "SBOM generation failed" }
Get-ChildItem release\windows\* | Where-Object { !$_.PSIsContainer } | ForEach-Object {
  $Hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  "$Hash  $($_.Name)"
} | Set-Content release\windows\SHA256SUMS.txt -Encoding ascii
Write-Host "Windows release created in release\windows"
