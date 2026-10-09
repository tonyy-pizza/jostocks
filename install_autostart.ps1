<#
.SYNOPSIS
    Start jostocks automatically when you log on.

.DESCRIPTION
    Puts one shortcut in your Startup folder. At logon it runs
    jostocks_autorun.py with pythonw.exe, which refreshes the pipeline data if
    it is stale and then opens the window.

    This matches how Dionysus already starts - a Startup-folder shortcut
    targeting pythonw.exe - rather than adding a scheduled task, so the two
    behave the same way on this machine.

    There is deliberately NO wake trigger. jostocks notices that the machine
    slept from inside the app, by watching the wall clock jump between timer
    ticks, and refreshes then. The consequence: with the window CLOSED, a wake
    does not refresh. The next logon does.

    Nothing is installed, no admin rights are needed, and nothing outside your
    own Startup folder is touched. Re-running this is safe - it overwrites its
    own shortcut. uninstall_autostart.ps1 takes it away again.

    The shortcut's target is pythonw.exe called explicitly with the script
    path as an argument, not the .py file on its own. A file association only
    decides what happens when Windows is asked to "open" a file; naming the
    interpreter outright skips that decision, which is why this still works on
    a machine where double-clicking a .py opens an editor.

.PARAMETER Remove
    Delete the shortcut instead of creating it. Same as running
    uninstall_autostart.ps1.

.PARAMETER Python
    Use this interpreter instead of the one found automatically. Give the path
    to pythonw.exe - python.exe works but flashes a console window at logon.

.PARAMETER NoRefresh
    Open the window at logon without refreshing the data first.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_autostart.ps1

    The -ExecutionPolicy Bypass matters: Windows refuses to run unsigned .ps1
    files by default, and it refuses quietly enough to look like a broken
    script. It applies to this one invocation only and changes no setting.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Python "C:\Users\joey\stocks\jostocks\.venv\Scripts\pythonw.exe"
#>

[CmdletBinding()]
param(
    [switch] $Remove,
    [string] $Python = '',
    [switch] $NoRefresh
)

$ErrorActionPreference = 'Stop'

function Write-Step { param($m) Write-Host "  $m" }
function Write-Ok   { param($m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "  [x]  $m" -ForegroundColor Red }

# ---------------------------------------------------------------- locations --
$root = if ($PSScriptRoot) { $PSScriptRoot }
        else { Split-Path -Parent $MyInvocation.MyCommand.Definition }

$autorun  = Join-Path $root 'jostocks_autorun.py'
$appScript = Join-Path $root 'jostocks.py'
$startup  = Join-Path ([Environment]::GetFolderPath('Startup')) 'jostocks.lnk'

Write-Host ''
Write-Host '  jostocks - autostart setup' -ForegroundColor Cyan
Write-Host '  ----------------------------------------------------------------'
Write-Step "project folder:  $root"
Write-Step "startup folder:  $(Split-Path -Parent $startup)"
Write-Host ''

# ------------------------------------------------------------------ remove --
if ($Remove) {
    if (Test-Path -LiteralPath $startup) {
        Remove-Item -LiteralPath $startup -Force
        Write-Ok "removed $startup"
    } else {
        Write-Step 'nothing to remove - jostocks was not set to start at logon'
    }
    Write-Host ''
    Write-Host '  Done. Nothing else was changed.'
    Write-Host ''
    return
}

# ------------------------------------------------------------ sanity check --
$missing = @()
if (-not (Test-Path -LiteralPath $autorun))   { $missing += $autorun }
if (-not (Test-Path -LiteralPath $appScript)) { $missing += $appScript }
if ($missing.Count -gt 0) {
    Write-Fail 'Could not find the scripts this shortcut points at:'
    $missing | ForEach-Object { Write-Host "        $_" }
    Write-Host ''
    Write-Host '  Run this from inside the jostocks folder.'
    Write-Host ''
    exit 1
}

# ----------------------------------------------------------------- python --
# pythonw.exe, because a logon should not flash a console window. Preference
# order: what was passed in, a venv beside the project, then the launcher's
# idea of the default install.
function Find-Pythonw {
    if ($Python) {
        if (Test-Path -LiteralPath $Python) { return (Resolve-Path -LiteralPath $Python).Path }
        Write-Fail "the -Python path does not exist: $Python"
        exit 1
    }

    foreach ($relative in @('.venv\Scripts\pythonw.exe', 'venv\Scripts\pythonw.exe')) {
        $candidate = Join-Path $root $relative
        if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    }

    # py.exe knows where the real install is; ask it rather than guessing.
    try {
        $found = & py -c "import sys, os; print(os.path.join(sys.base_prefix, 'pythonw.exe'))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found -and (Test-Path -LiteralPath $found.Trim())) {
            return (Resolve-Path -LiteralPath $found.Trim()).Path
        }
    } catch { }

    try {
        $command = Get-Command pythonw.exe -ErrorAction Stop
        return $command.Source
    } catch { }

    return $null
}

$pythonw = Find-Pythonw
if (-not $pythonw) {
    Write-Fail 'Could not find pythonw.exe.'
    Write-Host ''
    Write-Host '  Install Python from https://www.python.org/downloads/ and tick'
    Write-Host '  "Add python.exe to PATH", then run this again - or pass the path:'
    Write-Host ''
    Write-Host '    -Python "C:\path\to\pythonw.exe"'
    Write-Host ''
    exit 1
}
Write-Step "interpreter:     $pythonw"

if ($pythonw -notlike '*pythonw.exe') {
    Write-Warn 'that is not pythonw.exe, so a console window may flash at logon'
}

# Report whether the dependencies are there, but do not install them: a logon
# hook that pip-installs on first run is a surprise nobody asked for.
$pythonExe = $pythonw -replace 'pythonw\.exe$', 'python.exe'
if (Test-Path -LiteralPath $pythonExe) {
    & $pythonExe -c "import PyQt6" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'PyQt6 is not installed for that interpreter. Install it with:'
        Write-Host "        `"$pythonExe`" -m pip install -r `"$(Join-Path $root 'requirements.txt')`""
    } else {
        Write-Ok 'PyQt6 is installed for that interpreter'
    }
}
Write-Host ''

# ----------------------------------------------------------------- create --
$arguments = '"{0}" --reason logon' -f $autorun
if ($NoRefresh) { $arguments = '"{0}"' -f $appScript }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($startup)
$link.TargetPath       = $pythonw
$link.Arguments        = $arguments
$link.WorkingDirectory = $root
$link.Description      = 'Refresh the stocks scan data if stale, then open jostocks'
$link.WindowStyle      = 1
$icon = Join-Path $root 'jostocks.ico'
if (Test-Path -LiteralPath $icon) { $link.IconLocation = $icon }
$link.Save()

Write-Ok "created $startup"
Write-Host ''
Write-Host '  jostocks will now start when you log on.' -ForegroundColor Cyan
Write-Host ''
Write-Host "    at logon    $(Split-Path -Leaf $pythonw) jostocks_autorun.py --reason logon"
Write-Host '                refreshes the data if it is stale, then opens the window'
Write-Host ''
Write-Host '    on wake     detected inside the app, which then refreshes.'
Write-Host '                With the window CLOSED a wake does NOT refresh -'
Write-Host '                the next logon does. There is no scheduled task.'
Write-Host ''
Write-Host '  Settings live in data\settings.json:'
Write-Host '    auto_refresh_on_logon, auto_refresh_on_wake, open_window_on_logon,'
Write-Host '    min_minutes_between_scans, network_wait_minutes, scan_enabled'
Write-Host ''
Write-Host '  Re-run with -Remove, or run uninstall_autostart.ps1, to undo this.'
Write-Host ''
