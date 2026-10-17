<h1 align=center>PS5 EXE Toolkit</h1>
<div align=center>
<img src=assets/app-icon.png width=20%>
</div>

---

PS5 EXE Toolkit is a basic GUI based wrapper to automate the conversion of extracted PS5 pkg files to a native windows executable using [Relinker from AnyPS5](https://github.com/boykopovar/AnyPS5).

> **No game files are included. You are responsible for the game files and the methods used to obtain them. This project is not affiliated with Sony Interactive Entertainment.**

[Download the latest Windows build](https://github.com/biologyscience/ps5-exe-toolkit/releases/latest). Download the `.exe` and run it after placing it in a dedicated folder; there is no separate installer.

<div align=center>
⚠️ <strong>READ</strong> ⚠️
</div>
<div align=center style="margin: 15px 0">
Not interested in reading lengthy docs? Want to go from .pkg to .exe quick? -> <a href=README_SIMPLIFIED.md>CLICK HERE!</a> <-
</div>
<div align=center>
⚠️ <strong>READ</strong> ⚠️
</div>

## Requirements

- Windows
- An extracted game folder containing `eboot.bin`, `sce_module/`, and `sce_sys/` for building a game executable. You are responsible on the methods you use to obtain these files.
- An internet connection the first time the toolkit downloads Relinker tools, and when checking for updates.

On first launch, it may download Relinker and its supporting libraries from [AnyPS5](https://github.com/boykopovar/AnyPS5). Keep the app open until the download finishes.

## Convert SELF files

1. Select the folder containing the extracted game files. The folder must contain `eboot.bin`, `sce_module/`, and `sce_sys/`; the GUI marks each item green when present and red when missing.
2. Click **unSELF**.
3. Follow the conversion results in the **Output** panel.

> unSELF works only with files extracted from a fpkg file

The converter searches the selected folder and its subfolders, recognises files inside SELF containers and are converted in place; unrecognized files are skipped. Files with encrypted/compressed segments or invalid data are reported as failures. Back up important files before running conversion.

## Build a Windows executable of the Game

1. Select the extracted game folder containing `eboot.bin`, `sce_module/`, and `sce_sys/`. *([unSELF](#convert-self-files) the files if required)*
2. Choose whether to include the Media folder. This option is enabled by default, and the GUI remembers your choice.
3. Click **Build .exe** and monitor progress in the **Output** panel.

The build uses Relinker to create `game.exe`, assembles the game files under `app0/`, and copies supporting libraries into a sibling `libs/` directory. Existing items in `app0/` take precedence: missing contents of matching directories are merged, while conflicts are skipped. Copy progress is reported for each root-level item.

> **Important:** Building rearranges and removes files in the selected game
> folder as it assembles `app0/`. Back up the folder before building. Do not
> run another operation on the same folder while a build is in progress.

## Updates

- **Check for Relinker Updates** downloads the latest Windows Relinker and supporting library archive from AnyPS5. The app also checks for these tools on launch and starts setup if its `tools/` directory is missing.
- **Check for GUI Updates** is available in the packaged Windows app. It checks the latest GitHub release, validates the downloaded executable, then closes the current app and starts the updated build. Keep the app in a folder where it can replace its executable.

Both update operations require an internet connection. Progress and errors are shown in the **Output** panel.

## Build the GUI from source

Clone the project
```
git clone https://github.com/biologyscience/ps5-exe-toolkit.git

cd ps5-exe-toolkit
```

Install requirements
```
python -m pip install -r requirements.txt
```

Run the GUI
```
python app.py
```

Or build the executable and then run the GUI
```
python -m PyInstaller --noconfirm --clean "PS5 EXE Toolkit.spec"
```
Open the executable from `dist/PS5 EXE Toolkit.exe`

---

> Vibe coded using Claude and Copilot