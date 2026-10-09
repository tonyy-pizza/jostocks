<#
.SYNOPSIS
    Stop jostocks starting when you log on.

.DESCRIPTION
    Removes the Startup-folder shortcut that install_autostart.ps1 created.
    Nothing else is touched: the project folder, your settings, your notes and
    the pipeline's data are all left exactly as they are.

    Safe to run when nothing is installed - it says so and changes nothing.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall_autostart.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Write-Step { param($m) Write-Host "  $m" }
function Write-Ok   { param($m) Write-Host "  [ok] $m" -ForegroundColor Green }

$root = if ($PSScriptRoot) { $PSScriptRoot }
        else { Split-Path -Parent $MyInvocation.MyCommand.Definition }

$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'jostocks.lnk'

Write-Host ''
Write-Host '  jostocks - remove autostart' -ForegroundColor Cyan
Write-Host '  ----------------------------------------------------------------'
Write-Host ''

$removed = $false

if (Test-Path -LiteralPath $startup) {
    Remove-Item -LiteralPath $startup -Force
    Write-Ok "removed $startup"
    $removed = $true
} else {
    Write-Step 'no Startup shortcut found'
}

# Earlier builds of this project may have registered scheduled tasks. Clear
# them too, so an upgrade does not leave a task behind that still fires.
foreach ($task in @('jostocks logon', 'jostocks wake')) {
    $existing = schtasks /query /tn "$task" 2>$null
    if ($LASTEXITCODE -eq 0) {
        schtasks /delete /tn "$task" /f | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "removed scheduled task '$task'"
            $removed = $true
        }
    }
}

Write-Host ''
if ($removed) {
    Write-Host '  jostocks will no longer start on its own.' -ForegroundColor Cyan
    Write-Host '  Your settings, notes and history are untouched, and you can'
    Write-Host '  still start it from launch.bat at any time.'
} else {
    Write-Host '  Nothing was installed, so nothing was changed.'
}
Write-Host ''
