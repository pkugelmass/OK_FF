# Creates a "Fantasy Football HQ" shortcut on the Desktop that runs update.bat.
# Called by start.bat on first run; safe to run again any time.
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Fantasy Football HQ.lnk"

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = Join-Path $root "update.bat"
$s.WorkingDirectory = $root
$s.IconLocation = (Join-Path $root "assets\football.ico") + ",0"
$s.Description = "Fantasy Football HQ"
$s.Save()
Write-Output "Shortcut created: $lnk"
