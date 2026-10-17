import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid

from . import APP_DIR, CommandError


GUI_RELEASE_API = (
    "https://api.github.com/repos/biologyscience/ps5-exe-toolkit/releases/latest"
)
GUI_RELEASE_ASSET = "PS5.EXE.Toolkit.exe"
GUI_UPDATE_ERROR_FILE = os.path.join(
    tempfile.gettempdir(), "ps5-exe-toolkit-gui-update-error.txt"
)


def download_release_asset(url, destination, digest, label, log, expected_size=None):
    temporary_path = destination + ".download"
    request = urllib.request.Request(url, headers={"User-Agent": "PS5-EXE-TOOLKIT"})
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            if expected_size is not None and total and total != expected_size:
                raise ValueError(
                    f"Unexpected download size for {label}: expected "
                    f"{expected_size} bytes, server reported {total}."
                )
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
            expected_size = expected_size or total
            if expected_size and downloaded != expected_size:
                raise ValueError(
                    f"Incomplete download for {label}: expected {expected_size} bytes, "
                    f"received {downloaded}."
                )
        if digest is not None:
            if not isinstance(digest, str) or not digest.lower().startswith("sha256:"):
                raise ValueError(f"GitHub returned an unsupported digest for {label}.")
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

    update_path = os.path.join(
        APP_DIR, f".{GUI_RELEASE_ASSET}.{uuid.uuid4().hex}.update"
    )
    update_temp_path = update_path + ".download"
    helper_path = None
    try:
        if os.path.exists(GUI_UPDATE_ERROR_FILE):
            os.remove(GUI_UPDATE_ERROR_FILE)
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
        if asset is None:
            raise ValueError(
                f"The latest release does not contain {GUI_RELEASE_ASSET}."
            )
        download_url = asset.get("browser_download_url")
        if not isinstance(download_url, str) or not download_url.startswith("https://"):
            raise ValueError("The latest release has an invalid download URL.")
        digest = asset.get("digest")
        if digest is not None and (
            not isinstance(digest, str) or not digest.lower().startswith("sha256:")
        ):
            raise ValueError("GitHub returned an unsupported release asset digest.")
        asset_size = asset.get("size")
        if not isinstance(asset_size, int) or asset_size <= 0:
            raise ValueError("GitHub returned an invalid release asset size.")

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
            download_url,
            update_path,
            digest,
            f"{GUI_RELEASE_ASSET} ({tag})",
            log,
            expected_size=asset_size,
        )
        with open(update_path, "rb") as downloaded:
            if downloaded.read(2) != b"MZ":
                raise ValueError("The downloaded GUI update is not a valid Windows executable.")
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
            delete=False,
        ) as helper:
            helper_path = helper.name
            helper.write(
                "param([int]$ProcessId, [string]$UpdatePath, "
                "[string]$TargetPath, [string]$ErrorPath)\n"
                "$ErrorActionPreference = 'Stop'\n"
                "$BackupPath = $TargetPath + '.update-backup'\n"
                "try {\n"
                "  $Deadline = [DateTime]::UtcNow.AddSeconds(120)\n"
                "  while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {\n"
                "    if ([DateTime]::UtcNow -ge $Deadline) {\n"
                "      throw 'Timed out waiting for the GUI to exit.'\n"
                "    }\n"
                "    Start-Sleep -Milliseconds 250\n"
                "  }\n"
                "  if (Test-Path -LiteralPath $BackupPath) {\n"
                "    Remove-Item -LiteralPath $BackupPath -Force\n"
                "  }\n"
                "  $Attempt = 0\n"
                "  while ($true) {\n"
                "    try {\n"
                "      if (Test-Path -LiteralPath $TargetPath) {\n"
                "        [System.IO.File]::Replace($UpdatePath, $TargetPath, $BackupPath)\n"
                "      } else {\n"
                "        [System.IO.File]::Move($UpdatePath, $TargetPath)\n"
                "      }\n"
                "      break\n"
                "    } catch {\n"
                "      $Attempt++\n"
                "      if ($Attempt -ge 20) { throw }\n"
                "      Start-Sleep -Milliseconds 500\n"
                "    }\n"
                "  }\n"
                "  $env:PYINSTALLER_RESET_ENVIRONMENT = '1'\n"
                "  $NewProcess = Start-Process -FilePath $TargetPath "
                "-WorkingDirectory (Split-Path -Parent $TargetPath) -PassThru\n"
                "  try {\n"
                "    if (-not $NewProcess.WaitForInputIdle(60000) "
                "-or $NewProcess.HasExited) {\n"
                "      throw 'The new GUI did not become ready within 60 seconds.'\n"
                "    }\n"
                "    if (Test-Path -LiteralPath $BackupPath) {\n"
                "      Remove-Item -LiteralPath $BackupPath -Force\n"
                "  }\n"
                "  } catch {\n"
                "    $Failure = 'The new GUI was started, but backup cleanup failed "
                "or the GUI did not become ready. Backup retained at ' + "
                "$BackupPath + '`n' + ($_ | Out-String)\n"
                "    try { $Failure | Set-Content -LiteralPath $ErrorPath -Encoding UTF8 }\n"
                "      catch { Write-Error ($Failure + '`nCould not write update error: ' + "
                "($_ | Out-String)) }\n"
                "  }\n"
                "} catch {\n"
                "  $Failure = $_ | Out-String\n"
                "  try {\n"
                "    if (Test-Path -LiteralPath $BackupPath) {\n"
                "      if (Test-Path -LiteralPath $TargetPath) {\n"
                "        Remove-Item -LiteralPath $TargetPath -Force\n"
                "      }\n"
                "      Move-Item -LiteralPath $BackupPath -Destination $TargetPath\n"
                "    }\n"
                "  } catch { $Failure += ('`nRollback failed: ' + ($_ | Out-String)) }\n"
                "  try { $Failure | Set-Content -LiteralPath $ErrorPath -Encoding UTF8 }\n"
                "    catch { Write-Error ($Failure + '`nCould not write update error: ' + "
                "($_ | Out-String)) }\n"
                "  try {\n"
                "    if (Test-Path -LiteralPath $TargetPath) {\n"
                "      Start-Process -FilePath $TargetPath "
                "-WorkingDirectory (Split-Path -Parent $TargetPath)\n"
                "    }\n"
                "  } catch { Write-Error ('Could not restart the previous GUI: ' + "
                "($_ | Out-String)) }\n"
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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        helper_path = None
        with runner.lock:
            runner.restart_requested = True
        log(f"GUI build {tag} downloaded. Restarting to install the update...")
    except Exception as error:
        cleanup_errors = []
        for path in (update_path, update_temp_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as cleanup_error:
                    cleanup_errors.append(f"{path}: {cleanup_error}")
        if helper_path and os.path.exists(helper_path):
            try:
                os.remove(helper_path)
            except OSError as cleanup_error:
                cleanup_errors.append(f"{helper_path}: {cleanup_error}")
        if cleanup_errors:
            error = RuntimeError(
                f"{error}; cleanup also failed: {'; '.join(cleanup_errors)}"
            )
        raise CommandError(f"GUI update failed: {error}") from error
