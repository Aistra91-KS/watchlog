# Crée les raccourcis « Watchlog » (Bureau + menu Démarrer) pour lancer l'outil sans VS Code.
#
# Usage (PowerShell, depuis la racine du projet) :
#   powershell -ExecutionPolicy Bypass -File launcher\install.ps1
#
# Le raccourci lance launcher\watchlog_launcher.py avec le pythonw.exe du .venv (Python
# officiel, sans fenêtre de console) : pas d'exe maison, que les antivirus bloquent souvent
# par erreur. À relancer si le dossier du projet est déplacé.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Environnement Python introuvable : $pythonw" }

$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($place in $places) {
    $link = $shell.CreateShortcut((Join-Path $place "Watchlog.lnk"))
    $link.TargetPath = $pythonw
    $link.Arguments = "`"$(Join-Path $PSScriptRoot 'watchlog_launcher.py')`""
    $link.WorkingDirectory = $root
    $link.IconLocation = Join-Path $root "assets\watchlog.ico"
    $link.Description = "Watchlog : suivi de séries et films"
    $link.Save()
    Write-Host "Raccourci créé : $(Join-Path $place 'Watchlog.lnk')"
}
