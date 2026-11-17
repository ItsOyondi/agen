"""Build a downloadable Agen package that installs without the source tree.

The zip contains a wheel of compiled bytecode plus the C++ extension, the
editor extension, example programs, and install.ps1. It does not contain
Python sources, the C++ VM, tests, or secrets.

Run from anywhere:

    python release/build_release.py
"""

from __future__ import annotations

import base64
import compileall
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"
RELEASE_NAME = f"Agen-{VERSION}-win64"


def _sha256(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def build_wheel(wheel_dir: Path) -> Path:
    try:
        import wheel  # noqa: F401
    except ImportError:
        _run([sys.executable, "-m", "pip", "install", "wheel"], ROOT)
    if wheel_dir.exists():
        shutil.rmtree(wheel_dir)
    wheel_dir.mkdir(parents=True)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
        ],
        ROOT,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel, found {wheels}")
    return wheels[0]


def _rewrite_metadata(staging: Path) -> None:
    dist_info = next(staging.glob("*.dist-info"))
    metadata = dist_info / "METADATA"
    text = metadata.read_text(encoding="utf-8")
    header, _, _body = text.partition("\n\n")
    lines = []
    for line in header.splitlines():
        if line.startswith("Description-Content-Type:"):
            lines.append("Description-Content-Type: text/plain")
        else:
            lines.append(line)
    description = textwrap.dedent(
        """\
        Agen is a language for agentic AI, automation, and cloud deployment.
        This package is the compiled program. Install it with the Agen installer.
        """
    ).strip()
    metadata.write_text("\n".join(lines) + "\n\n" + description + "\n", encoding="utf-8")


def _write_record(staging: Path) -> None:
    dist_info = next(staging.glob("*.dist-info"))
    record_path = dist_info / "RECORD"
    rows: list[str] = []
    files = sorted(path for path in staging.rglob("*") if path.is_file() and path != record_path)
    for path in files:
        rel = path.relative_to(staging).as_posix()
        data = path.read_bytes()
        rows.append(f"{rel},{_sha256(data)},{len(data)}")
    rows.append(f"{record_path.relative_to(staging).as_posix()},,")
    record_path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def strip_sources(wheel_path: Path) -> None:
    staging = Path(tempfile.mkdtemp(prefix="agen-wheel-"))
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            archive.extractall(staging)
        package = staging / "agen"
        if not package.is_dir():
            raise SystemExit("wheel has no agen package")
        if not compileall.compile_dir(str(package), force=True, legacy=True, quiet=1):
            raise SystemExit("could not compile Agen")
        for cache in list(package.rglob("__pycache__")):
            if cache.is_dir():
                shutil.rmtree(cache)
        for path in list(package.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".py", ".cpp", ".h", ".hpp", ".c"}:
                path.unlink()
        if list(package.rglob("*.py")):
            raise SystemExit("Python sources were left in the wheel")
        if not any(package.rglob("*.pyc")):
            raise SystemExit("wheel has no compiled Python files")
        _rewrite_metadata(staging)
        _write_record(staging)
        tmp = wheel_path.with_suffix(".whl.tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
        tmp.replace(wheel_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def installer_script() -> str:
    major, minor = sys.version_info[:2]
    return textwrap.dedent(
        f"""\
        $ErrorActionPreference = "Stop"
        $root = Split-Path -Parent $MyInvocation.MyCommand.Path
        $wheel = Get-ChildItem -LiteralPath (Join-Path $root "package") -Filter *.whl | Select-Object -First 1
        if (-not $wheel) {{ throw "The package folder has no Agen wheel." }}

        function Find-Python {{
            $attempts = @(
                @("py", "-{major}.{minor}"),
                @("python", "")
            )
            foreach ($attempt in $attempts) {{
                $exe = $attempt[0]
                if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {{ continue }}
                $pyArgs = @()
                if ($attempt[1]) {{ $pyArgs += $attempt[1] }}
                $pyArgs += @("-c", "import sys; print(sys.executable)")
                try {{
                    $found = & $exe @pyArgs
                    if ($LASTEXITCODE -eq 0 -and $found) {{ return $found.Trim() }}
                }} catch {{ }}
            }}
            return $null
        }}

        $python = Find-Python
        if (-not $python) {{
            throw "Python {major}.{minor} (64-bit) is required. Install it from https://www.python.org/downloads/ and run this again."
        }}
        & $python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == ({major}, {minor}) and sys.maxsize > 2**32 else 1)"
        if ($LASTEXITCODE -ne 0) {{
            throw "This Agen package needs 64-bit Python {major}.{minor}. `python --version` reported a different interpreter."
        }}

        $runtime = Join-Path $env:LOCALAPPDATA "Agen\\runtime"
        & $python -m venv $runtime
        if ($LASTEXITCODE -ne 0) {{ throw "Could not create the Agen runtime." }}
        $runtimePython = Join-Path $runtime "Scripts\\python.exe"
        & $runtimePython -m pip install --disable-pip-version-check --no-deps --force-reinstall $wheel.FullName
        if ($LASTEXITCODE -ne 0) {{ throw "Could not install Agen." }}

        $scripts = Join-Path $runtime "Scripts"
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) {{ $userPath = "" }}
        $parts = @($userPath -split ";" | Where-Object {{ $_ -and ($_ -ne $scripts) }})
        $newPath = (@($scripts) + $parts) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = $scripts + ";" + $env:Path

        $editor = Join-Path $root "editor"
        $version = (Get-Content -LiteralPath (Join-Path $editor "package.json") -Raw | ConvertFrom-Json).version
        $targets = @(
            @{{ Root = (Join-Path $env:USERPROFILE ".cursor\\extensions"); Cli = @(
                (Join-Path $env:LOCALAPPDATA "Programs\\cursor\\resources\\app\\bin\\cursor.cmd"),
                (Join-Path $env:LOCALAPPDATA "Programs\\cursor\\Cursor.exe")
            ) }},
            @{{ Root = (Join-Path $env:USERPROFILE ".vscode\\extensions"); Cli = @(
                (Join-Path $env:LOCALAPPDATA "Programs\\Microsoft VS Code\\bin\\code.cmd")
            ) }}
        )
        if (Get-Command cursor -ErrorAction SilentlyContinue) {{
            $targets[0].Cli = @((Get-Command cursor).Source) + $targets[0].Cli
        }}
        if (Get-Command code -ErrorAction SilentlyContinue) {{
            $targets[1].Cli = @((Get-Command code).Source) + $targets[1].Cli
        }}
        $vsix = Get-ChildItem -LiteralPath (Join-Path $root "package") -Filter *.vsix | Select-Object -First 1
        foreach ($target in $targets) {{
            $dest = Join-Path $target.Root "agen-lang.agen-$version"
            if (Test-Path -LiteralPath $dest) {{ Remove-Item -LiteralPath $dest -Recurse -Force }}
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            Get-ChildItem -LiteralPath $editor -Force | Copy-Item -Destination $dest -Recurse -Force
            $cache = Join-Path $target.Root "extensions.json"
            if (Test-Path -LiteralPath $cache) {{ Remove-Item -LiteralPath $cache -Force }}
            if ($vsix) {{
                foreach ($cli in $target.Cli) {{
                    if (-not (Test-Path -LiteralPath $cli)) {{ continue }}
                    try {{
                        & $cli --install-extension $vsix.FullName --force
                    }} catch {{ }}
                    if ($LASTEXITCODE -eq 0) {{ break }}
                }}
            }}
        }}
        & $runtimePython (Join-Path $root "apply_editor_settings.py")
        if ($LASTEXITCODE -ne 0) {{ throw "Could not save the Agen icon theme and keyword colors." }}

        Write-Output "Agen is installed."
        & (Join-Path $scripts "agen.exe") --version
        Write-Output "Open a new terminal, then run: agen run examples\\hello.agn"
        Write-Output "Reload Cursor or VS Code (Developer: Reload Window) for icons and colors."
        """
    )


def install_text() -> str:
    major, minor = sys.version_info[:2]
    return textwrap.dedent(
        f"""\
        Agen {VERSION}

        This package installs Agen and its editor support. It does not include
        the program source.

        You need 64-bit Python {major}.{minor}. Check with: python --version

        1. Unzip this folder.
        2. In the folder, run:

           powershell -ExecutionPolicy Bypass -File .\\install.ps1

        3. Open a new terminal and run:

           agen run examples\\hello.agn

        4. In Cursor or VS Code, run Developer: Reload Window.
           .agn files then use the Agen icon and keyword colors.

        Read Agen-Getting-Started.pdf in this folder before writing a program.

        agen --version
        agen run examples\\hello.agn
        agen check examples\\calculator.agn
        """
    )


def build_vsix(editor: Path, dest_dir: Path) -> Path:
    package = json.loads((editor / "package.json").read_text(encoding="utf-8"))
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{package["name"]}" Version="{package["version"]}" Publisher="{package["publisher"]}" />
    <DisplayName>{package["displayName"]}</DisplayName>
    <Description xml:space="preserve">{package["description"]}</Description>
    <Categories>Programming Languages,Themes</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.70.0" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""
    content_types = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="vsixmanifest" ContentType="text/xml"/>
  <Default Extension="json" ContentType="application/json"/>
  <Default Extension="js" ContentType="application/javascript"/>
  <Default Extension="svg" ContentType="image/svg+xml"/>
  <Default Extension="woff" ContentType="font/woff"/>
  <Default Extension="txt" ContentType="text/plain"/>
  <Default Extension="xml" ContentType="text/xml"/>
</Types>
"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"agen-{package['version']}.vsix"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("extension.vsixmanifest", manifest)
        for path in sorted(editor.rglob("*")):
            if path.is_file():
                archive.write(path, Path("extension") / path.relative_to(editor).as_posix())
    return out


def assemble(wheel_path: Path) -> Path:
    dist = ROOT / "dist"
    stage = dist / RELEASE_NAME
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "package").mkdir(parents=True)
    shutil.copy2(wheel_path, stage / "package" / wheel_path.name)
    shutil.copytree(ROOT / "editors" / "vscode", stage / "editor")
    build_vsix(stage / "editor", stage / "package")
    shutil.copy2(ROOT / "release" / "apply_editor_settings.py", stage / "apply_editor_settings.py")
    examples = stage / "examples"
    examples.mkdir()
    for name in ("hello.agn", "calculator.agn", "use_library.agn", "native_speed.agn"):
        shutil.copy2(ROOT / "examples" / name, examples / name)
    shutil.copytree(ROOT / "examples" / "lib", examples / "lib")
    shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
    guide = ROOT / "docs" / "Agen-Getting-Started.pdf"
    if guide.is_file():
        shutil.copy2(guide, stage / "Agen-Getting-Started.pdf")
    (stage / "install.ps1").write_text(installer_script(), encoding="utf-8", newline="\r\n")
    (stage / "INSTALL.txt").write_text(install_text(), encoding="utf-8", newline="\r\n")

    archive_path = dist / f"{RELEASE_NAME}.zip"
    if archive_path.exists():
        archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in stage.rglob("*"):
            if path.is_file():
                archive.write(path, Path(RELEASE_NAME) / path.relative_to(stage))
    return archive_path


def assert_package_is_closed(archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    banned = [
        name
        for name in names
        if not name.endswith("/apply_editor_settings.py")
        and (
            name.endswith((".py", ".cpp", ".h", ".hpp", ".c", ".env"))
            or name.replace("\\", "/").endswith("vault.json")
        )
    ]
    if banned:
        raise SystemExit("package still contains source or secrets:\n" + "\n".join(banned))
    if not any(name.endswith(".pyd") or "/agen/__pycache__/" in name.replace("\\", "/") for name in names):
        # The compiled files live inside the wheel, not as loose zip entries.
        pass
    wheel_name = next(name for name in names if name.endswith(".whl"))
    with zipfile.ZipFile(archive_path) as archive:
        wheel_bytes = archive.read(wheel_name)
    tmp = Path(tempfile.mkdtemp(prefix="agen-check-"))
    try:
        wheel_file = tmp / Path(wheel_name).name
        wheel_file.write_bytes(wheel_bytes)
        with zipfile.ZipFile(wheel_file) as wheel:
            inner = wheel.namelist()
        leaked = [name for name in inner if name.endswith((".py", ".cpp", ".h", ".hpp"))]
        if leaked:
            raise SystemExit("wheel still contains source:\n" + "\n".join(leaked))
        if not any(name.endswith(".pyc") for name in inner):
            raise SystemExit("wheel has no bytecode")
        if not any(name.endswith(".pyd") for name in inner):
            raise SystemExit("wheel is missing the compiled backend")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def smoke_test(archive_path: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="agen-install-"))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(work)
        wheel = next((work / RELEASE_NAME / "package").glob("*.whl"))
        runtime = work / "runtime"
        _run([sys.executable, "-m", "venv", str(runtime)], work)
        python = runtime / "Scripts" / "python.exe"
        _run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(wheel)],
            work,
        )
        agen = runtime / "Scripts" / "agen.exe"
        _run([str(agen), "--version"], work)
        program = work / "hello.agn"
        program.write_text('fn greet(name) { return "Hello, " + name }\nprint(greet("Agen"))\n', encoding="utf-8")
        _run([str(agen), "run", str(program)], work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    if sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise SystemExit("Build this package with 64-bit Python 3.11.")
    if sys.platform != "win32":
        raise SystemExit("This package builder currently produces the Windows install package.")
    wheel_dir = ROOT / "dist" / "wheels"
    wheel = build_wheel(wheel_dir)
    if PY_TAG not in wheel.name or "win_amd64" not in wheel.name:
        raise SystemExit(f"unexpected wheel name: {wheel.name}")
    strip_sources(wheel)
    archive_path = assemble(wheel)
    installer = (ROOT / "dist" / RELEASE_NAME / "install.ps1").read_text(encoding="utf-8")
    if 'LiteralPath (Join-Path $editor "*")' in installer:
        raise SystemExit("installer still skips the editor files")
    if "Get-ChildItem -LiteralPath $editor" not in installer:
        raise SystemExit("installer does not copy the editor files")
    assert_package_is_closed(archive_path)
    smoke_test(archive_path)
    print(archive_path)


if __name__ == "__main__":
    main()
