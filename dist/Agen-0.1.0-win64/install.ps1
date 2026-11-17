$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheel = Get-ChildItem -LiteralPath (Join-Path $root "package") -Filter *.whl | Select-Object -First 1
if (-not $wheel) { throw "The package folder has no Agen wheel." }

function Find-Python {
    $attempts = @(
        @("py", "-3.11"),
        @("python", "")
    )
    foreach ($attempt in $attempts) {
        $exe = $attempt[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $pyArgs = @()
        if ($attempt[1]) { $pyArgs += $attempt[1] }
        $pyArgs += @("-c", "import sys; print(sys.executable)")
        try {
            $found = & $exe @pyArgs
            if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    throw "Python 3.11 (64-bit) is required. Install it from https://www.python.org/downloads/ and run this again."
}
& $python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and sys.maxsize > 2**32 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "This Agen package needs 64-bit Python 3.11. `python --version` reported a different interpreter."
}

$runtime = Join-Path $env:LOCALAPPDATA "Agen\runtime"
& $python -m venv $runtime
if ($LASTEXITCODE -ne 0) { throw "Could not create the Agen runtime." }
$runtimePython = Join-Path $runtime "Scripts\python.exe"
& $runtimePython -m pip install --disable-pip-version-check --no-deps --force-reinstall $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "Could not install Agen." }

$scripts = Join-Path $runtime "Scripts"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$parts = @($userPath -split ";" | Where-Object { $_ -and ($_ -ne $scripts) })
$newPath = (@($scripts) + $parts) -join ";"
[Environment]::SetEnvironmentVariable("Path", $newPath, "User")
$env:Path = $scripts + ";" + $env:Path

$editor = Join-Path $root "editor"
$version = (Get-Content -LiteralPath (Join-Path $editor "package.json") -Raw | ConvertFrom-Json).version
$targets = @(
    @{ Root = (Join-Path $env:USERPROFILE ".cursor\extensions"); Cli = @(
        (Join-Path $env:LOCALAPPDATA "Programs\cursor\resources\app\bin\cursor.cmd"),
        (Join-Path $env:LOCALAPPDATA "Programs\cursor\Cursor.exe")
    ) },
    @{ Root = (Join-Path $env:USERPROFILE ".vscode\extensions"); Cli = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Microsoft VS Code\bin\code.cmd")
    ) }
)
if (Get-Command cursor -ErrorAction SilentlyContinue) {
    $targets[0].Cli = @((Get-Command cursor).Source) + $targets[0].Cli
}
if (Get-Command code -ErrorAction SilentlyContinue) {
    $targets[1].Cli = @((Get-Command code).Source) + $targets[1].Cli
}
$vsix = Get-ChildItem -LiteralPath (Join-Path $root "package") -Filter *.vsix | Select-Object -First 1
foreach ($target in $targets) {
    $dest = Join-Path $target.Root "agen-lang.agen-$version"
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Get-ChildItem -LiteralPath $editor -Force | Copy-Item -Destination $dest -Recurse -Force
    $cache = Join-Path $target.Root "extensions.json"
    if (Test-Path -LiteralPath $cache) { Remove-Item -LiteralPath $cache -Force }
    if ($vsix) {
        foreach ($cli in $target.Cli) {
            if (-not (Test-Path -LiteralPath $cli)) { continue }
            try {
                & $cli --install-extension $vsix.FullName --force
            } catch { }
            if ($LASTEXITCODE -eq 0) { break }
        }
    }
}
& $runtimePython (Join-Path $root "apply_editor_settings.py")
if ($LASTEXITCODE -ne 0) { throw "Could not save the Agen icon theme and keyword colors." }

Write-Output "Agen is installed."
& (Join-Path $scripts "agen.exe") --version
Write-Output "Open a new terminal, then run: agen run examples\hello.agn"
Write-Output "Reload Cursor or VS Code (Developer: Reload Window) for icons and colors."
