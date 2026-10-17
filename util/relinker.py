import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import PurePosixPath

from . import APP_DIR, CommandError
from .updater import download_release_asset


PROGRAM = "tools/relinker.exe"
BUILD_OPTIONS = [
    {
        "id": "media",
        "label": "Include Media Folder",
        "arg": ["--module-dir", "Media/Modules", "--module-dir", "Media/Plugins"],
        "default": True,
    },
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
                raise ValueError(
                    f"PRX archive contains a symbolic link: {info.filename!r}"
                )

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
                        raise ValueError(
                            f"Cannot extract directory over existing item: {target!r}"
                        )
                else:
                    os.mkdir(target)
                continue

            if os.path.lexists(target) and (
                os.path.islink(target) or os.path.isdir(target)
            ):
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
            "User-Agent": "PS5-EXE-TOOLKIT",
        },
    )
    try:
        log("Checking the latest Relinker release...")
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
        if not relinker.get("browser_download_url") or not prx.get(
            "browser_download_url"
        ):
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
                isinstance(path, str)
                and os.path.isfile(os.path.join(tools_dir, path))
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
                json.dump(
                    {
                        "tag": tag,
                        "relinker_digest": relinker.get("digest"),
                        "prx_digest": prx.get("digest"),
                        "prx_files": extracted_files,
                    },
                    marker_file,
                )
            os.replace(marker_temp, marker_path)
        finally:
            if os.path.exists(marker_temp):
                os.remove(marker_temp)
        log(f"Relinker tools updated successfully to {tag}.")
    except Exception as error:
        raise CommandError(f"Relinker update failed: {error}") from error


def run_cmd(args, cwd, runner):
    """Run a child process and stream its output to the GUI's log writers."""
    print("> " + subprocess.list2cmdline(args))
    if cwd:
        print(f"  (in {cwd})")
    print()

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    extra = {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}
    try:
        process = subprocess.Popen(
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
        raise CommandError(
            f"Program not found: {args[0]!r}. Put it on your PATH or set a full "
            "path in PROGRAM."
        ) from None
    runner.child = process

    out, err = sys.stdout, sys.stderr

    def pump(stream, target):
        for line in stream:
            target.write(line)

    pumps = [
        threading.Thread(target=pump, args=(process.stdout, out), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, err), daemon=True),
    ]
    for thread in pumps:
        thread.start()
    try:
        code = process.wait()
        for thread in pumps:
            thread.join()
    finally:
        runner.child = None
    if code != 0:
        raise CommandError(f"Program exited with code {code}")
    return code


def build_exe(folder, enabled, runner):
    """Build an executable and assemble the resulting app0 directory."""
    optional = []
    for option in BUILD_OPTIONS:
        if option["id"] in enabled:
            argument = option["arg"]
            if isinstance(argument, str):
                optional.extend(argument.split())
            else:
                optional.extend(argument)
    run_cmd(
        [
            find_program(PROGRAM),
            "--windows",
            *optional,
            os.path.join(folder, "eboot.bin"),
            os.path.join(folder, "game.exe"),
        ],
        cwd=folder,
        runner=runner,
    )

    app0 = os.path.join(folder, "app0")
    os.makedirs(app0, exist_ok=True)

    def copy_with_progress(source, destination, progress):
        with open(source, "rb") as source_file, open(destination, "wb") as destination_file:
            while True:
                chunk = source_file.read(256 * 1024)
                if not chunk:
                    break
                destination_file.write(chunk)
                progress["copied"] += len(chunk)
                if progress["total"]:
                    percent = progress["copied"] * 100 // progress["total"]
                    if percent >= progress["last"] + 5:
                        print(f"Copying {progress['label']}: {min(percent, 100)}%")
                        progress["last"] = percent - percent % 5
        shutil.copystat(source, destination)
        return destination

    def copy_size(source, destination=None, missing_only=False):
        if os.path.isfile(source):
            return (
                0
                if missing_only and destination and os.path.lexists(destination)
                else os.path.getsize(source)
            )
        if not os.path.isdir(source):
            return 0
        total = 0
        with os.scandir(source) as entries:
            for entry in entries:
                target = (
                    os.path.join(destination, entry.name)
                    if destination
                    else None
                )
                if missing_only and target and os.path.lexists(target):
                    if entry.is_dir(follow_symlinks=False) and os.path.isdir(target):
                        total += copy_size(entry.path, target, missing_only=True)
                    continue
                total += copy_size(entry.path, target)
        return total

    def start_progress(label, total):
        print(f"Copying {label}: 0%")
        return {"label": label, "total": total, "copied": 0, "last": 0}

    def finish_progress(progress):
        if progress["last"] < 100:
            print(f"Copying {progress['label']}: 100%")
            progress["last"] = 100

    entries_to_move = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if (
                os.path.normcase(entry.name) != os.path.normcase("game.exe")
                and os.path.normcase(entry.name) != os.path.normcase("app0")
                and (entry.is_file() or entry.is_dir())
            ):
                entries_to_move.append(entry.name)

    def copy_missing(source, destination, progress):
        os.makedirs(destination, exist_ok=True)
        with os.scandir(source) as entries:
            for entry in entries:
                source_path = entry.path
                destination_path = os.path.join(destination, entry.name)
                if not os.path.lexists(destination_path):
                    if entry.is_dir(follow_symlinks=False):
                        shutil.copytree(
                            source_path,
                            destination_path,
                            copy_function=lambda src, dst: copy_with_progress(
                                src, dst, progress
                            ),
                        )
                    elif entry.is_file(follow_symlinks=False):
                        copy_with_progress(source_path, destination_path, progress)
                    continue
                if (
                    entry.is_dir(follow_symlinks=False)
                    and os.path.isdir(destination_path)
                ):
                    copy_missing(source_path, destination_path, progress)

    def copy_root(source, destination, label, missing_only=False):
        total = copy_size(source, destination, missing_only)
        progress = start_progress(label, total)
        if missing_only:
            copy_missing(source, destination, progress)
        elif os.path.isdir(source):
            shutil.copytree(
                source,
                destination,
                copy_function=lambda src, dst: copy_with_progress(
                    src, dst, progress
                ),
            )
        else:
            copy_with_progress(source, destination, progress)
        finish_progress(progress)

    for name in entries_to_move:
        source = os.path.join(folder, name)
        destination = os.path.join(app0, name)
        if os.path.lexists(destination):
            if os.path.isdir(source) and os.path.isdir(destination):
                copy_root(
                    source,
                    destination,
                    os.path.join("app0", name),
                    missing_only=True,
                )
                print(f"Merged missing items from {name} into app0/")
            else:
                print(f"Skipped {name}: already exists in app0/")
                continue
        else:
            move_progress = [None]

            def move_copy(source_file, destination_file):
                if move_progress[0] is None:
                    move_progress[0] = start_progress(
                        os.path.join("app0", name), copy_size(source)
                    )
                return copy_with_progress(
                    source_file, destination_file, move_progress[0]
                )

            shutil.move(source, destination, copy_function=move_copy)
            if move_progress[0] is not None:
                finish_progress(move_progress[0])
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
    os.makedirs(libs_destination)
    with os.scandir(libs_source) as entries:
        for entry in entries:
            copy_root(
                entry.path,
                os.path.join(libs_destination, entry.name),
                os.path.join("libs", entry.name),
            )
    print("Copied libs/ beside app0/")
