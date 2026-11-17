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
$destinations = @(
    (Join-Path $env:USERPROFILE ".cursor\extensions\agen-lang.agen-$version"),
    (Join-Path $env:USERPROFILE ".vscode\extensions\agen-lang.agen-$version")
)
foreach ($dest in $destinations) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -LiteralPath (Join-Path $editor "*") -Destination $dest -Recurse -Force
}

Write-Output "Agen is installed."
& (Join-Path $scripts "agen.exe") --version
Write-Output "Open a new terminal, then run: agen run examples\hello.agn"
Write-Output "Reload Cursor or VS Code (Developer: Reload Window) for icons and colors."
