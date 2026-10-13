# Construit Watchlog.exe (à la racine du projet) et crée un raccourci sur le Bureau.
#
# Usage (PowerShell, depuis la racine du projet) :
#   powershell -ExecutionPolicy Bypass -File launcher\build.ps1
#
# PyInstaller est installé dans un environnement séparé (launcher\.build-venv) :
# le .venv du projet n'est pas modifié. À relancer seulement si launcher\watchlog_launcher.py change.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$buildVenv = Join-Path $PSScriptRoot ".build-venv"
$work = Join-Path $PSScriptRoot "build"

if (-not (Test-Path (Join-Path $buildVenv "Scripts\python.exe"))) {
    Write-Host "Création de l'environnement de construction..."
    & (Join-Path $root ".venv\Scripts\python.exe") -m venv $buildVenv
    & (Join-Path $buildVenv "Scripts\python.exe") -m pip install --quiet pyinstaller
}

Write-Host "Construction de Watchlog.exe..."
& (Join-Path $buildVenv "Scripts\pyinstaller.exe") `
    --onefile --noconsole --clean --noconfirm `
    --name Watchlog `
    --icon (Join-Path $root "assets\watchlog.ico") `
    --distpath $root `
    --workpath $work `
    --specpath $work `
    (Join-Path $PSScriptRoot "watchlog_launcher.py")
if ($LASTEXITCODE -ne 0) { throw "La construction a échoué." }
Remove-Item -Recurse -Force $work

# Raccourci sur le Bureau (icône Watchlog, dossier de travail = projet)
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "Watchlog.lnk"))
$link.TargetPath = Join-Path $root "Watchlog.exe"
$link.WorkingDirectory = $root
$link.IconLocation = Join-Path $root "assets\watchlog.ico"
$link.Description = "Watchlog : suivi de séries et films"
$link.Save()

Write-Host "OK : $(Join-Path $root 'Watchlog.exe') et raccourci « Watchlog » sur le Bureau."
