param(
  [switch]$Desktop,
  [switch]$StartMenu
)

$ErrorActionPreference = "Stop"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$IconPath = Join-Path $AppDir "assets\icons\windows\Locus.ico"
$RunScript = Join-Path $AppDir "run.ps1"

if (-not (Test-Path $IconPath)) {
  throw "Missing Windows icon: $IconPath. Run python scripts/generate_app_icons.py first."
}

if (-not (Test-Path $RunScript)) {
  throw "Missing launcher: $RunScript"
}

if (-not $Desktop -and -not $StartMenu) {
  $Desktop = $true
  $StartMenu = $true
}

$Shell = New-Object -ComObject WScript.Shell
$Targets = @()
if ($Desktop) {
  $Targets += [Environment]::GetFolderPath("Desktop")
}
if ($StartMenu) {
  $Targets += Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
}

foreach ($Target in $Targets) {
  New-Item -ItemType Directory -Force -Path $Target | Out-Null
  $ShortcutPath = Join-Path $Target "Locus.lnk"
  $Shortcut = $Shell.CreateShortcut($ShortcutPath)
  $Shortcut.TargetPath = "powershell.exe"
  $Shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""
  $Shortcut.WorkingDirectory = $AppDir
  $Shortcut.IconLocation = $IconPath
  $Shortcut.Description = "Locus local workspace assistant"
  $Shortcut.Save()
  Write-Host "Created $ShortcutPath"
}
