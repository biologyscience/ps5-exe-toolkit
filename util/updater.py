import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from . import APP_DIR, CommandError


GUI_RELEASE_API = (
    "https://api.github.com/repos/biologyscience/ps5-exe-toolkit/releases/latest"
)
GUI_RELEASE_ASSET = "PS5.EXE.Toolkit.exe"
GUI_UPDATE_ERROR_FILE = os.path.join(APP_DIR, ".gui-update-error.txt")


def download_release_asset(url, destination, digest, label, log):
    temporary_path = destination + ".download"
    request = urllib.request.Request(url, headers={"User-Agent": "Folder-Runner"})
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            last_progress = -1
            log(f"Downloading {label}: 0%")
            with open(temporary_path, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    if total:
                        progress = downloaded * 100 // total
                        if progress >= last_progress + 5 or downloaded == total:
                            log(f"Downloading {label}: {min(progress, 100)}%")
                            last_progress = progress - progress % 5
                    elif downloaded - (last_progress + 1) * 5 * 1024 * 1024 >= 0:
                        last_progress += 1
                        log(f"Downloading {label}: {downloaded // (1024 * 1024)} MiB")
            if total and downloaded != total:
                raise ValueError(
                    f"Incomplete download for {label}: expected {total} bytes, "
                    f"received {downloaded}."
                )
        if digest and digest.startswith("sha256:"):
            actual_digest = "sha256:" + hasher.hexdigest()
            if actual_digest.lower() != digest.lower():
                raise ValueError(
                    f"SHA-256 check failed for {os.path.basename(destination)}"
                )
        os.replace(temporary_path, destination)
        log(f"Downloaded {label}.")
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def update_gui(log, runner):
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        raise CommandError(
            "GUI updates are available only in the packaged Windows app."
        )

    update_path = os.path.join(APP_DIR, f".{GUI_RELEASE_ASSET}.update")
    update_temp_path = update_path + ".download"
    helper_path = None
    try:
        log("Checking the latest GUI release...")
        request = urllib.request.Request(
            GUI_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "PS5-EXE-Toolkit",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.load(response)
        if not isinstance(release, dict):
            raise ValueError("GitHub returned invalid release metadata.")

        tag = release.get("tag_name")
        assets = release.get("assets", [])
        if not isinstance(tag, str) or not tag:
            raise ValueError("GitHub release is missing its version tag.")
        if not isinstance(assets, list):
            raise ValueError("GitHub returned an invalid release asset list.")
        asset = next(
            (
                item for item in assets
                if isinstance(item, dict) and item.get("name") == GUI_RELEASE_ASSET
            ),
            None,
        )
        if asset is None or not asset.get("browser_download_url"):
            raise ValueError(
                f"The latest release does not contain {GUI_RELEASE_ASSET}."
            )

        digest = asset.get("digest")
        installed_digest = None
        if isinstance(digest, str) and digest.lower().startswith("sha256:"):
            hasher = hashlib.sha256()
            with open(sys.executable, "rb") as executable:
                for chunk in iter(lambda: executable.read(1024 * 1024), b""):
                    hasher.update(chunk)
            installed_digest = "sha256:" + hasher.hexdigest()
            if installed_digest.lower() == digest.lower():
                log(f"Already running the latest GUI build ({tag}).")
                return

        download_release_asset(
            asset["browser_download_url"],
            update_path,
            digest,
            f"{GUI_RELEASE_ASSET} ({tag})",
            log,
        )
        hasher = hashlib.sha256()
        with open(update_path, "rb") as downloaded:
            for chunk in iter(lambda: downloaded.read(1024 * 1024), b""):
                hasher.update(chunk)
        downloaded_digest = "sha256:" + hasher.hexdigest()
        if installed_digest is None:
            hasher = hashlib.sha256()
            with open(sys.executable, "rb") as executable:
                for chunk in iter(lambda: executable.read(1024 * 1024), b""):
                    hasher.update(chunk)
            installed_digest = "sha256:" + hasher.hexdigest()
        if downloaded_digest == installed_digest:
            os.remove(update_path)
            log(f"Already running the latest GUI build ({tag}).")
            return

        powershell = shutil.which("powershell.exe")
        if not powershell:
            raise ValueError(
                "Windows PowerShell was not found; the GUI cannot be restarted "
                "to install the update."
            )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".ps1",
            prefix=".gui-update-",
            dir=APP_DIR,
            delete=False,
        ) as helper:
            helper_path = helper.name
            helper.write(
                "param([int]$ProcessId, [string]$UpdatePath, "
                "[string]$TargetPath, [string]$ErrorPath)\n"
                "$ErrorActionPreference = 'Stop'\n"
                "while ($true) {\n"
                "  try { Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null; "
                "Start-Sleep -Milliseconds 250 }\n"
                "  catch { break }\n"
                "}\n"
                "try {\n"
                "  Move-Item -LiteralPath $UpdatePath -Destination $TargetPath -Force\n"
                "  $env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
                "  Start-Process -FilePath $TargetPath\n"
                "} catch {\n"
                "  $_ | Out-String | Set-Content -LiteralPath $ErrorPath "
                "-Encoding UTF8\n"
                "  if (Test-Path -LiteralPath $TargetPath) { "
                "Start-Process -FilePath $TargetPath }\n"
                "} finally {\n"
                "  Remove-Item -LiteralPath $PSCommandPath -Force "
                "-ErrorAction SilentlyContinue\n"
                "}\n"
            )

        subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                helper_path,
                str(os.getpid()),
                update_path,
                sys.executable,
                GUI_UPDATE_ERROR_FILE,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        with runner.lock:
            runner.restart_requested = True
        log(f"GUI build {tag} downloaded. Restarting to install the update...")
    except Exception as error:
        for path in (update_path, update_temp_path):
            if os.path.exists(path):
                os.remove(path)
        if helper_path and os.path.exists(helper_path):
            os.remove(helper_path)
        raise CommandError(f"GUI update failed: {error}") from error
