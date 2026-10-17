import ctypes
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.request
import zipfile
import struct
from pathlib import PurePosixPath

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "index.html")
STATE_FILE = os.path.join(APP_DIR, ".state.json")

MAGICS = {0x1D3D154F: "PS4", 0xEEF51454: "PS5"}


def unself(d):
    nseg = struct.unpack_from("<H", d, 0x18)[0]
    eoff = 0x20 + 0x20 * nseg
    if d[eoff:eoff+4] != b"\x7fELF":
        raise ValueError("no ELF header where expected")
    e_phoff, = struct.unpack_from("<Q", d, eoff + 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", d, eoff + 0x36)

    phdrs = []
    for i in range(e_phnum):
        f = struct.unpack_from("<IIQQQQQQ", d, eoff + e_phoff + i * e_phentsize)
        phdrs.append((f[2], f[5]))

    head = e_phoff + e_phnum * e_phentsize
    end = max([head] + [o + s for o, s in phdrs])
    out = bytearray(end)
    out[:head] = d[eoff:eoff + head]

    for i in range(nseg):
        flags, off, csize, dsize = struct.unpack_from("<QQQQ", d, 0x20 + i * 0x20)
        if not (flags & 0x800):
            continue
        if flags & 0xA:
            raise ValueError(f"segment {i} is encrypted/compressed")
        p_offset, p_filesz = phdrs[(flags >> 20) & 0xFFF]
        n = min(dsize, p_filesz)
        out[p_offset:p_offset + n] = d[off:off + n]
    return out


def handle(path):
    """Returns "ok", "fail" or "skip"."""
    with open(path, "rb") as fh:
        head = fh.read(4)
        if len(head) < 4 or struct.unpack("<I", head)[0] not in MAGICS:
            return "skip"
        d = head + fh.read()
    kind = MAGICS[struct.unpack_from("<I", d, 0)[0]]
    tmp = path + ".tmp"
    try:
        out = unself(d)
        with open(tmp, "wb") as fh:
            fh.write(out)
        os.replace(tmp, path)
        print("OK  ", kind, path)
        return "ok"
    except Exception as e:
        print("FAIL", kind, path, "-", e, file=sys.stderr)
        return "fail"
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def run_task(folder):
    counts = {"ok": 0, "fail": 0, "skip": 0}
    for dp, _, files in os.walk(folder):
        for n in files:
            counts[handle(os.path.join(dp, n))] += 1
    print(f"\n{counts['ok']} converted, {counts['fail']} failed, {counts['skip']} skipped")


PROGRAM = "tools/relinker.exe"
GUI_RELEASE_API = "https://api.github.com/repos/biologyscience/ps5-exe-toolkit/releases/latest"
GUI_RELEASE_ASSET = "PS5.EXE.Toolkit.exe"
GUI_UPDATE_ERROR_FILE = os.path.join(APP_DIR, ".gui-update-error.txt")

BUILD_OPTIONS = [
    {"id": "media", "label": "Include Media", "arg": ["--module-dir", "Media/Modules", "--module-dir", "Media/Plugins"], "default": True},
]


def find_program(name):
    candidates = []
    if not os.path.isabs(name):
        candidates.append(os.path.join(APP_DIR, "tools", name))
        candidates.append(os.path.join(APP_DIR, name))
    candidates.extend((name, name + ".exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return name


def download_release_asset(url, destination, digest, label, log):
    temp_path = destination + ".download"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Folder-Runner"},
    )
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            last_progress = -1
            log(f"Downloading {label}: 0%")
            with open(temp_path, "wb") as output:
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
                    f"Incomplete download for {label}: expected {total} bytes, received {downloaded}."
                )
        if digest and digest.startswith("sha256:"):
            actual_digest = "sha256:" + hasher.hexdigest()
            if actual_digest.lower() != digest.lower():
                raise ValueError(f"SHA-256 check failed for {os.path.basename(destination)}")
        os.replace(temp_path, destination)
        log(f"Downloaded {label}.")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def extract_prx_archive(archive_path, destination, log):
    def safe_target(root, member_name):
        normalized_name = member_name.replace("\\", "/")
        member = PurePosixPath(normalized_name)
        if (
            member.is_absolute()
            or not member.parts
            or any(part in ("", ".", "..") for part in member.parts)
            or any(":" in part for part in member.parts)
            or "\0" in normalized_name
        ):
            raise ValueError(f"Unsafe path in PRX archive: {member_name!r}")
        target = os.path.abspath(os.path.join(root, *member.parts))
        if os.path.commonpath((os.path.abspath(root), target)) != os.path.abspath(root):
            raise ValueError(f"Unsafe path in PRX archive: {member_name!r}")
        return target, member.parts

    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        total = sum(info.file_size for info in members if not info.is_dir())
        extracted = 0
        last_progress = -1
        extracted_files = []
        log(f"Extracting PRX archive: 0% ({len(members)} entries)")
        for info in members:
            target, parts = safe_target(destination, info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"PRX archive contains a symbolic link: {info.filename!r}")

            parent = os.path.abspath(destination)
            for part in parts[:-1]:
                parent = os.path.join(parent, part)
                if os.path.lexists(parent):
                    if os.path.islink(parent) or not os.path.isdir(parent):
                        raise ValueError(f"Unsafe extraction directory: {parent!r}")
                else:
                    os.mkdir(parent)

            if info.is_dir():
                if os.path.lexists(target):
                    if os.path.islink(target) or not os.path.isdir(target):
                        raise ValueError(f"Cannot extract directory over existing item: {target!r}")
                else:
                    os.mkdir(target)
                continue

            if os.path.lexists(target) and (os.path.islink(target) or os.path.isdir(target)):
                raise ValueError(f"Cannot extract file over existing item: {target!r}")
            with archive.open(info) as source, open(target, "wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    extracted += len(chunk)
                    if total:
                        progress = extracted * 100 // total
                        if progress >= last_progress + 5:
                            log(f"Extracting PRX archive: {min(progress, 100)}%")
                            last_progress = progress - progress % 5
            extracted_files.append(os.path.relpath(target, destination))
        log(f"Extracted {len(members)} PRX archive entries.")
        return extracted_files


def update_relinker(log):
    api_url = "https://api.github.com/repos/boykopovar/AnyPS5/releases/latest"
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Folder-Runner",
        },
    )
    try:
        log("Checking the latest GitHub release...")
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

        relinker = next(
            (
                asset for asset in assets
                if isinstance(asset, dict)
                and isinstance(asset.get("name"), str)
                and asset["name"].lower().startswith("relinker-")
                and asset["name"].lower().endswith(".exe")
            ),
            None,
        )
        prx = next(
            (
                asset for asset in assets
                if isinstance(asset, dict)
                and isinstance(asset.get("name"), str)
                and asset["name"].lower().startswith("prx-windows-")
                and asset["name"].lower().endswith(".zip")
            ),
            None,
        )
        if relinker is None or prx is None:
            raise ValueError("The latest release does not contain both Windows assets.")
        if not relinker.get("browser_download_url") or not prx.get("browser_download_url"):
            raise ValueError("The latest release is missing an asset download URL.")

        tools_dir = os.path.join(APP_DIR, "tools")
        os.makedirs(tools_dir, exist_ok=True)
        marker_path = os.path.join(tools_dir, ".relinker-version.json")
        relinker_path = os.path.join(tools_dir, "relinker.exe")
        prx_path = os.path.join(tools_dir, "prx.zip")

        try:
            with open(marker_path, encoding="utf-8") as marker_file:
                installed = json.load(marker_file)
        except FileNotFoundError:
            installed = {}
        if (
            installed.get("tag") == tag
            and os.path.isfile(relinker_path)
            and installed.get("relinker_digest") == relinker.get("digest")
            and installed.get("prx_digest") == prx.get("digest")
            and isinstance(installed.get("prx_files"), list)
            and all(
                isinstance(path, str) and os.path.isfile(os.path.join(tools_dir, path))
                for path in installed["prx_files"]
            )
        ):
            log(f"Already up to date ({tag}); no downloads needed.")
            return

        download_release_asset(
            relinker["browser_download_url"],
            relinker_path,
            relinker.get("digest"),
            f"relinker.exe ({tag})",
            log,
        )
        download_release_asset(
            prx["browser_download_url"],
            prx_path,
            prx.get("digest"),
            f"prx.zip ({tag})",
            log,
        )
        extracted_files = extract_prx_archive(prx_path, tools_dir, log)
        os.remove(prx_path)
        log("Deleted prx.zip after extraction.")

        marker_temp = marker_path + ".tmp"
        try:
            with open(marker_temp, "w", encoding="utf-8") as marker_file:
                json.dump({
                    "tag": tag,
                    "relinker_digest": relinker.get("digest"),
                    "prx_digest": prx.get("digest"),
                    "prx_files": extracted_files,
                }, marker_file)
            os.replace(marker_temp, marker_path)
        finally:
            if os.path.exists(marker_temp):
                os.remove(marker_temp)
        log(f"Relinker tools updated successfully to {tag}.")
    except Exception as e:
        raise CommandError(f"Relinker update failed: {e}") from e


def update_gui(log):
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        raise CommandError("GUI updates are available only in the packaged Windows app.")

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
            raise ValueError(f"The latest release does not contain {GUI_RELEASE_ASSET}.")

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
            raise ValueError("Windows PowerShell was not found; the GUI cannot be restarted to install the update.")
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
                "param([int]$ProcessId, [string]$UpdatePath, [string]$TargetPath, [string]$ErrorPath)\n"
                "$ErrorActionPreference = 'Stop'\n"
                "while ($true) {\n"
                "  try { Get-Process -Id $ProcessId -ErrorAction Stop | Out-Null; Start-Sleep -Milliseconds 250 }\n"
                "  catch { break }\n"
                "}\n"
                "try {\n"
                "  Move-Item -LiteralPath $UpdatePath -Destination $TargetPath -Force\n"
                "  Start-Process -FilePath $TargetPath\n"
                "} catch {\n"
                "  $_ | Out-String | Set-Content -LiteralPath $ErrorPath -Encoding UTF8\n"
                "  if (Test-Path -LiteralPath $TargetPath) { Start-Process -FilePath $TargetPath }\n"
                "} finally {\n"
                "  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
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
            creationflags=subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        runner.restart_requested = True
        log(f"GUI build {tag} downloaded. Restarting to install the update...")
        _window.destroy()
    except Exception as e:
        for path in (update_path, update_temp_path):
            if os.path.exists(path):
                os.remove(path)
        if helper_path and os.path.exists(helper_path):
            os.remove(helper_path)
        raise CommandError(f"GUI update failed: {e}") from e


def build_exe(folder, enabled):
    """enabled: set of BUILD_OPTIONS ids whose checkbox is ticked."""
    optional = []
    for o in BUILD_OPTIONS:
        if o["id"] in enabled:
            arg = o["arg"]
            if isinstance(arg, str):
                optional.extend(arg.split())
            else:
                optional.extend(arg)
    run_cmd(
        [
            find_program(PROGRAM),
            "--windows",
            *optional,
            os.path.join(folder, "eboot.bin"),
            os.path.join(folder, "game.exe"),
        ],
        cwd=folder,
    )

    app0 = os.path.join(folder, "app0")
    os.makedirs(app0, exist_ok=True)
    entries_to_move = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if (
                os.path.normcase(entry.name) != os.path.normcase("game.exe")
                and os.path.normcase(entry.name) != os.path.normcase("app0")
                and (entry.is_file() or entry.is_dir())
            ):
                entries_to_move.append(entry.name)

    def copy_missing(source, destination):
        os.makedirs(destination, exist_ok=True)
        with os.scandir(source) as entries:
            for entry in entries:
                source_path = entry.path
                destination_path = os.path.join(destination, entry.name)
                if not os.path.lexists(destination_path):
                    if entry.is_dir(follow_symlinks=False):
                        shutil.copytree(source_path, destination_path)
                    elif entry.is_file(follow_symlinks=False):
                        shutil.copy2(source_path, destination_path)
                    continue
                if (
                    entry.is_dir(follow_symlinks=False)
                    and os.path.isdir(destination_path)
                ):
                    copy_missing(source_path, destination_path)

    for name in entries_to_move:
        source = os.path.join(folder, name)
        destination = os.path.join(app0, name)
        if os.path.lexists(destination):
            if os.path.isdir(source) and os.path.isdir(destination):
                copy_missing(source, destination)
                print(f"Merged missing items from {name} into app0/")
            else:
                print(f"Skipped {name}: already exists in app0/")
                continue
        else:
            shutil.move(source, destination)
            print(f"Moved {name} to app0/")

    with os.scandir(folder) as entries:
        leftovers = [
            entry for entry in entries
            if os.path.normcase(entry.name) not in {
                os.path.normcase("game.exe"),
                os.path.normcase("app0"),
            }
        ]
    for entry in leftovers:
        if entry.is_symlink() or entry.is_file():
            os.remove(entry.path)
        elif entry.is_dir():
            shutil.rmtree(entry.path)
        else:
            os.remove(entry.path)
        print(f"Deleted {entry.name} outside app0/")

    libs_source = os.path.join(APP_DIR, "tools", "libs")
    libs_destination = os.path.join(folder, "libs")
    shutil.copytree(libs_source, libs_destination)
    print("Copied libs/ beside app0/")



_window = None


def load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    saved = data.get("options", {})
    return {
        "folder": data.get("folder", ""),
        "options": {
            o["id"]: bool(saved.get(o["id"], o.get("default", False)))
            for o in BUILD_OPTIONS
        },
    }


def save_state(folder=None, options=None):
    """Only the fields you pass are changed; the rest keep their saved value."""
    state = load_state()
    if folder is not None:
        state["folder"] = folder
    if options is not None:
        state["options"] = {o["id"]: bool(options.get(o["id"], False)) for o in BUILD_OPTIONS}
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


class StopRequested(BaseException):
    """Raised inside run_task() when Stop is pressed (BaseException so `except Exception` won't swallow it)."""


class CommandError(Exception):
    """An external command failed. Shown as a short red message, without a traceback."""


class _LogWriter:
    """File-like object that sends printed text, line by line, to the GUI log."""

    encoding = "utf-8"

    def __init__(self, runner, tag):
        self.runner, self.tag, self.buf = runner, tag, ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.runner.add(line + "\n", self.tag)
        return len(text)

    def flush(self):
        if self.buf:
            self.runner.add(self.buf, self.tag)
            self.buf = ""

    def isatty(self):
        return False


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.run_id = 0
        self.log = []
        self.status = "idle"
        self.code = None
        self.child = None
        self.stop_requested = False
        self.restart_requested = False

    def add(self, text, tag=None):
        with self.lock:
            self.log.append({"t": tag, "s": text})

    def start(self, folder):
        folder = folder.strip()
        with self.lock:
            if self.status == "running":
                return "Already running."
        if not folder:
            return "Please select a folder first."
        if not os.path.isdir(folder):
            return "That folder does not exist."

        folder = os.path.join(folder, "")
        return self._launch(run_task, (folder,), f"run_task({folder!r})")

    def start_build(self, folder, options):
        folder = folder.strip()
        if not folder:
            return "Please select a folder first."
        if not os.path.isdir(folder):
            return "That folder does not exist."
        enabled = {o["id"] for o in BUILD_OPTIONS if options.get(o["id"])}
        return self._launch(build_exe, (folder, enabled), f"build_exe({folder!r}, {sorted(enabled)})")

    def start_update(self):
        log = lambda message: self.add(message + "\n", "muted")
        return self._launch(update_relinker, (log,), "check_relinker_updates()")

    def start_gui_update(self):
        log = lambda message: self.add(message + "\n", "muted")
        return self._launch(update_gui, (log,), "check_gui_updates()")

    def _launch(self, func, args, label):
        with self.lock:
            if self.status == "running":
                return "Already running."
            self.run_id += 1
            self.log = [{"t": "cmd", "s": f"$ {label}\n\n"}]
            self.status, self.code = "running", None
            self.stop_requested = False
        self.thread = threading.Thread(target=self._worker, args=(func, args), daemon=True)
        self.thread.start()
        return None

    def _worker(self, func, args):
        out, err = _LogWriter(self, None), _LogWriter(self, "err")
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        status = "done"
        try:
            func(*args)
        except StopRequested:
            status = "stopped"
        except CommandError as e:
            if self.stop_requested:
                status = "stopped"
            else:
                err.write(f"{e}\n")
                status = "failed"
        except BaseException:
            if self.stop_requested:
                status = "stopped"
            else:
                err.write(traceback.format_exc())
                status = "failed"
        finally:
            out.flush()
            err.flush()
            sys.stdout, sys.stderr = old

        with self.lock:
            self.status = status
        if status == "done":
            self.add("\n✓ Finished\n", "ok")
        elif status == "failed":
            self.add("\n✗ Failed\n", "err")

    def stop(self):
        t = self.thread
        if t and t.is_alive():
            self.stop_requested = True
            self.add("\n[Stop requested]\n", "warn")
            child = self.child
            if child and child.poll() is None:
                child.terminate()
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(t.ident), ctypes.py_object(StopRequested))


runner = Runner()


def run_cmd(args, cwd=None):
    """Run an external program, streaming its output live into the GUI log.

    stdout shows normally, stderr in red. Raises CommandError on a non-zero exit code.
    """
    print("> " + subprocess.list2cmdline(args))
    if cwd:
        print(f"  (in {cwd})")
    print()

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    extra = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            env=env,
            **extra,
        )
    except FileNotFoundError:
        raise CommandError(f"Program not found: {args[0]!r}. Put it on your PATH or set a full path in PROGRAM.") from None
    runner.child = proc

    out, err = sys.stdout, sys.stderr

    def pump(stream, target):
        for line in stream:
            target.write(line)

    pumps = [
        threading.Thread(target=pump, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err), daemon=True),
    ]
    for t in pumps:
        t.start()
    try:
        code = proc.wait()
        for t in pumps:
            t.join()
    finally:
        runner.child = None
    if code != 0:
        raise CommandError(f"Program exited with code {code}")
    return code


class Api:
    def get_state(self):
        return dict(load_state(), build_options=[{"id": o["id"], "label": o["label"]} for o in BUILD_OPTIONS])

    def set_state(self, folder, options):
        save_state(folder, options)

    def check_relinker_updates(self):
        error = runner.start_update()
        return {"ok": error is None, "error": error}

    def check_gui_updates(self):
        error = runner.start_gui_update()
        return {"ok": error is None, "error": error}

    def pick_folder(self):
        import webview

        kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        result = _window.create_file_dialog(kind)
        return result[0] if result else ""

    def list_folder(self, path):
        """One level only: the files and folders directly inside `path`."""
        limit = 500
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
            items = []
            for e in entries[:limit]:
                is_dir = e.is_dir()
                try:
                    size = None if is_dir else e.stat().st_size
                except OSError:
                    size = None
                items.append({"name": e.name, "is_dir": is_dir, "size": size})
            return {"ok": True, "items": items, "total": len(entries)}
        except FileNotFoundError:
            return {"ok": False, "error": "Folder not found."}
        except NotADirectoryError:
            return {"ok": False, "error": "That path is not a folder."}
        except PermissionError:
            return {"ok": False, "error": "Permission denied."}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def run(self, folder):
        save_state(folder)
        err = runner.start(folder)
        return {"ok": err is None, "error": err}

    def build(self, folder, options):
        save_state(folder, options)
        err = runner.start_build(folder, options)
        return {"ok": err is None, "error": err}

    def stop(self):
        runner.stop()

    def poll(self, run, since):
        with runner.lock:
            reset = run != runner.run_id
            start = 0 if reset else since
            entries = runner.log[start:]
            return {
                "html": os.path.getmtime(HTML_FILE) if os.path.exists(HTML_FILE) else 0,
                "run": runner.run_id,
                "reset": reset,
                "entries": entries,
                "next": start + len(entries),
                "status": runner.status,
                "code": runner.code,
            }


def main():
    global _window
    try:
        import webview
    except ImportError:
        sys.exit("pywebview is not installed. Run:  pip install pywebview")

    _window = webview.create_window(
        "PS5 EXE Toolkit",
        HTML_FILE,
        js_api=Api(),
        width=760,
        height=700,
        min_size=(520, 420),
    )
    if not os.path.isdir(os.path.join(APP_DIR, "tools")):
        error = runner.start_update()
        if error:
            runner.add(f"Could not start Relinker setup: {error}\n", "err")
    if os.path.isfile(GUI_UPDATE_ERROR_FILE):
        with open(GUI_UPDATE_ERROR_FILE, encoding="utf-8") as error_file:
            update_error = error_file.read().strip()
        os.remove(GUI_UPDATE_ERROR_FILE)
        if update_error:
            runner.add(f"GUI update failed: {update_error}\n", "err")
    webview.start()
    if not runner.restart_requested:
        runner.stop()


if __name__ == "__main__":
    main()