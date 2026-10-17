import ctypes
import json
import os
import sys
import threading
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QPainter,
    QPainterPath,
    QPen,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from util import APP_DIR, CommandError
from util.relinker import BUILD_OPTIONS, build_exe, update_relinker
from util.unself import run_task
from util.updater import GUI_UPDATE_ERROR_FILE, update_gui


STATE_FILE = os.path.join(APP_DIR, ".state.json")
WINDOW_WIDTH = 1080
WINDOW_HEIGHT = 720
UBUNTU_FONT_FILES = (
    "Ubuntu-Regular.ttf",
    "Ubuntu-Medium.ttf",
    "UbuntuMono-Regular.ttf",
)


def _load_app_fonts(application):
    bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    font_dir = os.path.join(bundle_dir, "assets", "fonts")
    for filename in UBUNTU_FONT_FILES:
        font_path = os.path.join(font_dir, filename)
        if QFontDatabase.addApplicationFont(font_path) < 0:
            raise RuntimeError(f"Could not load bundled font: {font_path}")
    application.setFont(QFont("Ubuntu", 10))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as state_file:
            data = json.load(state_file)
    except Exception:
        data = {}
    saved = data.get("options", {})
    return {
        "folder": data.get("folder", ""),
        "options": {
            option["id"]: bool(saved.get(option["id"], option.get("default", False)))
            for option in BUILD_OPTIONS
        },
    }


def save_state(folder=None, options=None):
    """Only the fields passed are changed; the rest keep their saved value."""
    state = load_state()
    if folder is not None:
        state["folder"] = folder
    if options is not None:
        state["options"] = {
            option["id"]: bool(options.get(option["id"], False))
            for option in BUILD_OPTIONS
        }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    except Exception:
        pass


class StopRequested(BaseException):
    """Raised inside run_task() so ordinary exception handlers do not swallow Stop."""


class _LogWriter:
    """File-like object that sends printed text, line by line, to the GUI log."""

    encoding = "utf-8"

    def __init__(self, runner, tag):
        self.runner, self.tag, self.buffer = runner, tag, ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.runner.add(line + "\n", self.tag)
        return len(text)

    def flush(self):
        if self.buffer:
            self.runner.add(self.buffer, self.tag)
            self.buffer = ""

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
        enabled = {
            option["id"] for option in BUILD_OPTIONS if options.get(option["id"])
        }
        return self._launch(
            build_exe,
            (folder, enabled, self),
            f"build_exe({folder!r}, {sorted(enabled)})",
        )

    def start_update(self):
        log = lambda message: self.add(message + "\n", "muted")
        return self._launch(update_relinker, (log,), "check_relinker_updates()")

    def start_gui_update(self):
        log = lambda message: self.add(message + "\n", "muted")
        return self._launch(update_gui, (log, self), "check_gui_updates()")

    def _launch(self, function, args, label):
        with self.lock:
            if self.status == "running":
                return "Already running."
            self.run_id += 1
            self.log = [{"t": "cmd", "s": f"$ {label}\n\n"}]
            self.status, self.code = "running", None
            self.stop_requested = False
        self.thread = threading.Thread(
            target=self._worker, args=(function, args), daemon=True
        )
        self.thread.start()
        return None

    def _worker(self, function, args):
        out, err = _LogWriter(self, None), _LogWriter(self, "err")
        old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        status = "done"
        try:
            function(*args)
        except StopRequested:
            status = "stopped"
        except CommandError as error:
            if self.stop_requested:
                status = "stopped"
            else:
                err.write(f"{error}\n")
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
        thread = self.thread
        if thread and thread.is_alive():
            self.stop_requested = True
            self.add("\n[Stop requested]\n", "warn")
            child = self.child
            if child and child.poll() is None:
                child.terminate()
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread.ident), ctypes.py_object(StopRequested)
            )


runner = Runner()


class MediaCheckBox(QCheckBox):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumHeight(38)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(text) + 54)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        if self.underMouse() or self.hasFocus():
            surface = QPainterPath()
            surface.addRoundedRect(0.5, 0.5, width - 1, height - 1, 9, 9)
            painter.fillPath(surface, QColor("#303641"))
            painter.setPen(QPen(QColor("#414a5a"), 1))
            painter.drawPath(surface)

        checked = self.isChecked()
        indicator = QPainterPath()
        indicator.addRoundedRect(10, (height - 19) / 2, 19, 19, 5, 5)
        painter.fillPath(
            indicator, QColor("#5269aa" if checked else "#343a46" if self.underMouse() else "#232832")
        )
        border = "#91a8f5" if self.hasFocus() else (
            "#647cc1" if checked else "#768092" if self.underMouse() else "#596273"
        )
        painter.setPen(QPen(QColor(border), 1.3))
        painter.drawPath(indicator)
        if checked:
            painter.setPen(QPen(QColor("#ffffff"), 2.2, cap=Qt.PenCapStyle.RoundCap,
                                join=Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(14, height // 2, 17, height // 2 + 3)
            painter.drawLine(17, height // 2 + 3, 24, height // 2 - 4)

        painter.setFont(self.font())
        painter.setPen(QColor("#e6e9ef" if self.underMouse() else "#d8dde6"))
        painter.drawText(38, 0, max(0, width - 44), height, Qt.AlignmentFlag.AlignVCenter,
                         self.text())


class ToolkitApp(QMainWindow):
    COLORS = {
        "idle": ("Idle", "#343d53", "#c2cff8"),
        "running": ("Working…", "#4a4031", "#ecd09b"),
        "done": ("Done", "#30443a", "#b9e4c7"),
        "stopped": ("Stopped", "#4a4031", "#ecd09b"),
    }
    LOG_COLORS = {
        "default": "#d9dee7",
        "cmd": "#a8b9f0",
        "err": "#f0aaa5",
        "ok": "#a6d8b5",
        "warn": "#e5c28b",
        "muted": "#a3acba",
    }

    def __init__(self):
        super().__init__()
        self.state = load_state()
        self.option_checks = {}
        self.run_id = -1
        self.log_index = 0
        self.last_status = None
        self.closed = False

        self._configure_window()
        self._build_ui()

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(300)
        self.save_timer.timeout.connect(self._save_state)
        self.folder_entry.textChanged.connect(self._folder_changed)
        for checkbox in self.option_checks.values():
            checkbox.toggled.connect(self._schedule_save)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(150)
        self.poll_timer.timeout.connect(self._poll_runner)
        self.poll_timer.start()
        self._validate_folder(self.folder_entry.text())

        if not os.path.isdir(os.path.join(APP_DIR, "tools")):
            error = runner.start_update()
            if error:
                self._append(f"Could not start Relinker setup: {error}\n", "err")
        if os.path.isfile(GUI_UPDATE_ERROR_FILE):
            with open(GUI_UPDATE_ERROR_FILE, encoding="utf-8") as error_file:
                update_error = error_file.read().strip()
            os.remove(GUI_UPDATE_ERROR_FILE)
            if update_error:
                self._append(f"GUI update failed: {update_error}\n", "err")

    def _configure_window(self):
        self.setWindowTitle("PS5 EXE Toolkit")
        self.setObjectName("mainWindow")
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        screen = QApplication.primaryScreen()
        if screen:
            self.move(screen.availableGeometry().center() - self.rect().center())

        self.setStyleSheet("""
            QMainWindow#mainWindow {
                background: #20242c;
            }
            QWidget {
                color: #e6e9ef;
                font-family: "Ubuntu";
                font-size: 10pt;
                background: transparent;
            }
            QFrame#card {
                background: #292e38;
                border: 1px solid #3b424e;
                border-radius: 18px;
            }
            QFrame#outputCard {
                background: #252a33;
                border: 1px solid #3b424e;
                border-radius: 14px;
            }
            QFrame#outlinedComponent {
                background: transparent;
                border: 1px solid #3b424e;
                border-radius: 12px;
            }
            QLabel#title {
                color: #f0f2f6;
                font-size: 23pt;
                font-family: "Ubuntu Medium";
                font-weight: 500;
            }
            QLabel#subtitle, QLabel#hint {
                color: #a3acba;
            }
            QLabel#sectionTitle {
                color: #e6e9ef;
                font-size: 12pt;
                font-family: "Ubuntu Medium";
                font-weight: 500;
            }
            QLineEdit {
                background: #232832;
                color: #e3e7ee;
                border: 1px solid #454d5b;
                border-radius: 10px;
                padding: 10px 12px;
                selection-background-color: #5269aa;
            }
            QLineEdit[folderState="valid"] {
                background: #293a33;
                color: #b9e4ca;
                border-color: #527c65;
            }
            QLineEdit[folderState="invalid"] {
                background: #423337;
                color: #f0b5b0;
                border-color: #80585c;
            }
            QPushButton {
                background: #343a46;
                color: #e3e7ee;
                border: 1px solid #4a5260;
                border-radius: 11px;
                padding: 9px 16px;
                min-height: 20px;
            }
            QPushButton:hover {
                background: #414958;
                border-color: #596273;
            }
            QPushButton:pressed { background: #303641; }
            QPushButton:disabled {
                background: #30353f;
                color: #858d9a;
                border-color: #3b424e;
            }
            QPushButton#primaryButton {
                background: #5269aa;
                color: #ffffff;
                border-color: #5269aa;
                font-family: "Ubuntu Medium";
                font-weight: 500;
            }
            QPushButton#primaryButton:hover {
                background: #6078ba;
                border-color: #7188cd;
            }
            QPushButton#dangerButton {
                background: #493638;
                color: #f0b5b0;
                border-color: #68474a;
            }
            QPushButton#dangerButton:hover {
                background: #5a3e41;
                border-color: #80585c;
            }
            QPushButton#actionButton {
                background: #e1e4e9;
                color: #20242c;
                border-color: #c9ced7;
                font-family: "Ubuntu Medium";
                font-weight: 500;
            }
            QPushButton#actionButton:hover {
                background: #f0f2f5;
                border-color: #ffffff;
            }
            QLabel#statusBadge {
                font-family: "Ubuntu Medium";
                font-weight: 500;
            }
            QPlainTextEdit {
                font-family: "Ubuntu Mono";
                background: transparent;
                color: #d9dee7;
                border: none;
                padding: 12px 15px;
                selection-background-color: #5269aa;
            }
            QLabel#statusDot {
                background: #aab2bf;
                border-radius: 5px;
                min-width: 10px;
                max-width: 10px;
                min-height: 10px;
                max-height: 10px;
            }
            QScrollBar:vertical {
                background: #232832;
                width: 12px;
                margin: 4px 2px 4px 2px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #3a414d;
                min-height: 25px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover { background: #4a5362; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

    @staticmethod
    def _card(parent_layout, margins=(20, 17, 20, 17)):
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(*margins)
        layout.setSpacing(10)
        parent_layout.addWidget(card)
        return card, layout

    @staticmethod
    def _label(text, object_name=None):
        label = QLabel(text)
        if object_name:
            label.setObjectName(object_name)
        return label

    @staticmethod
    def _button(text, callback, kind=None):
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if kind:
            button.setObjectName(kind)
        button.clicked.connect(callback)
        return button

    def _build_ui(self):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(28, 28, 28, 28)
        page_layout.setSpacing(14)
        self.setCentralWidget(page)

        header = QHBoxLayout()
        header.addWidget(self._label("PS5 EXE Toolkit", "title"), 1)
        self.gui_update_button = self._button(
            "Check for GUI Updates", self.check_gui_updates
        )
        self.relinker_update_button = self._button(
            "Check for Relinker Updates", self.check_relinker_updates
        )
        header.addWidget(self.gui_update_button)
        header.addWidget(self.relinker_update_button)
        page_layout.addLayout(header)

        setup_row = QHBoxLayout()
        setup_row.setContentsMargins(0, 0, 0, 0)
        setup_row.setSpacing(42)

        ehh = QFrame()
        ehh.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        ehh.setMinimumHeight(150)
        instructions = QVBoxLayout(ehh)
        instructions.setContentsMargins(0, 0, 0, 0)
        instructions.addWidget(self._label("Select a folder having these files:", "hint"))
        structure_box = QFrame()
        structure_box.setObjectName("structureBox")
        structure_box.setStyleSheet(
            "QFrame#structureBox {"
            " border: 1px solid #3b424e;"
            " border-radius: 12px;"
            "}"
        )
        structure_layout = QVBoxLayout(structure_box)
        structure_layout.setContentsMargins(14, 12, 14, 12)
        self.structure_items = {}
        for name, display in (
            ("eboot.bin", "    eboot.bin"),
            ("sce_module", "    sce_module/"),
            ("sce_sys", "    sce_sys/"),
        ):
            item = QLabel(display)
            item.setFont(QFont("Ubuntu", 10))
            self.structure_items[name] = item
            structure_layout.addWidget(item)
        others = QLabel("    others*")
        others.setFont(QFont("Ubuntu", 10))
        others.setStyleSheet("color: #a3acba;")
        structure_layout.addWidget(others)
        instructions.addWidget(structure_box)
        setup_row.addWidget(ehh, 2)

        folder_group = QFrame()
        folder_group.setMinimumHeight(150)
        folder_group.setObjectName("outlinedComponent")
        folder_group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        folder_form = QVBoxLayout(folder_group)
        folder_form.setContentsMargins(14, 12, 14, 12)
        folder_form.setSpacing(7)
        folder_form.addWidget(self._label("Folder", "hint"))
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        self.folder_entry = QLineEdit(self.state["folder"])
        self.folder_entry.setFont(QFont("Ubuntu", 10))
        self.folder_entry.setPlaceholderText("/path/to/folder")
        self.folder_entry.setClearButtonEnabled(True)
        folder_row.addWidget(self.folder_entry, 1)
        self.browse_button = self._button("Browse…", self.pick_folder)
        self.browse_button.setMinimumWidth(110)
        folder_row.addWidget(self.browse_button)
        folder_form.addLayout(folder_row)

        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)
        options.addStretch(1)
        options_group_layout = QHBoxLayout()
        options_group_layout.setContentsMargins(0, 0, 0, 0)
        options_group_layout.setSpacing(0)
        for option in BUILD_OPTIONS:
            checkbox = MediaCheckBox(option["label"])
            checkbox.setChecked(self.state["options"][option["id"]])
            self.option_checks[option["id"]] = checkbox
            options_group_layout.addWidget(checkbox)
        options.addLayout(options_group_layout)
        
        folder_form.addLayout(options)

        setup_row.addWidget(folder_group, 8)

        setup_section = QWidget()
        setup_section.setLayout(setup_row)
        page_layout.addWidget(setup_section)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        actions_row.addStretch(1)
        self.run_button = self._button("unSELF", self.start_run, "actionButton")
        self.build_button = self._button("Build .exe", self.start_build, "actionButton")
        self.stop_button = self._button("Stop", runner.stop, "dangerButton")
        self.stop_button.setEnabled(False)
        actions_row.addWidget(self.run_button)
        actions_row.addWidget(self.build_button)
        actions_row.addWidget(self.stop_button)
        self.stop_button.hide()
        page_layout.addLayout(actions_row)

        logs_card = QFrame()
        logs_card.setObjectName("outputCard")
        logs_layout = QVBoxLayout(logs_card)
        logs_layout.setContentsMargins(0, 0, 0, 8)
        logs_layout.setSpacing(0)
        logs_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        logs_heading = QHBoxLayout()
        logs_heading.setContentsMargins(16, 10, 14, 9)
        logs_heading.setSpacing(10)
        logs_heading.addWidget(self._label("Output", "hint"), 1)
        self.status_dot = QLabel()
        self.status_dot.setObjectName("statusDot")
        logs_heading.addWidget(self.status_dot)
        self.status_label = self._label("", "statusBadge")
        logs_heading.addWidget(self.status_label)
        self.clear_button = self._button("Clear", self.clear_output)
        logs_heading.addWidget(self.clear_button)
        logs_layout.addLayout(logs_heading)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #3b424e; background: #3b424e; max-height: 1px;")
        logs_layout.addWidget(divider)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        logs_layout.addWidget(self.output, 1)
        page_layout.addWidget(logs_card, 1)

        self._set_status("idle", None)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._disable_manual_resize)

    def _disable_manual_resize(self):
        if sys.platform == "win32" and QApplication.platformName() != "offscreen":
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(int(self.winId()))
            get_window_long = getattr(
                user32, "GetWindowLongPtrW", user32.GetWindowLongW
            )
            set_window_long = getattr(
                user32, "SetWindowLongPtrW", user32.SetWindowLongW
            )
            get_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int)
            get_window_long.restype = ctypes.c_ssize_t
            set_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
            set_window_long.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            )
            user32.SetWindowPos.restype = ctypes.c_int

            style = get_window_long(hwnd, -16)
            style = (style & ~0x00040000) | 0x00010000
            if not set_window_long(hwnd, -16, style):
                raise ctypes.WinError()
            if not user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020
            ):
                raise ctypes.WinError()

        if self.windowHandle():
            margins = self.windowHandle().frameMargins()
            target_width = WINDOW_WIDTH - margins.left() - margins.right()
            target_height = WINDOW_HEIGHT - margins.top() - margins.bottom()
        else:
            target_width, target_height = WINDOW_WIDTH, WINDOW_HEIGHT

        if sys.platform != "win32" or QApplication.platformName() == "offscreen":
            self.setFixedSize(target_width, target_height)
        else:
            self.resize(target_width, target_height)

        screen = self.screen()
        if screen:
            frame = self.frameGeometry()
            offset = screen.availableGeometry().center() - frame.center()
            self.move(self.pos() + offset)

    def _get_options(self):
        return {
            name: checkbox.isChecked()
            for name, checkbox in self.option_checks.items()
        }

    def _folder_changed(self, path):
        self._validate_folder(path)
        self._schedule_save()

    def _validate_folder(self, path):
        path = path.strip()
        if not path.strip():
            folder_state = "empty"
        else:
            valid = (
                os.path.isfile(os.path.join(path, "eboot.bin"))
                and os.path.isdir(os.path.join(path, "sce_module"))
                and os.path.isdir(os.path.join(path, "sce_sys"))
            )
            folder_state = "valid" if valid else "invalid"
        self.folder_entry.setProperty("folderState", folder_state)
        self.folder_entry.style().unpolish(self.folder_entry)
        self.folder_entry.style().polish(self.folder_entry)
        self.folder_entry.update()
        self._update_structure_status(path)

    def _update_structure_status(self, path):
        checks = {name: False for name in self.structure_items}
        if os.path.isdir(path):
            checks = {
                "eboot.bin": os.path.isfile(os.path.join(path, "eboot.bin")),
                "sce_module": os.path.isdir(os.path.join(path, "sce_module")),
                "sce_sys": os.path.isdir(os.path.join(path, "sce_sys")),
            }
        for name, exists in checks.items():
            color = "#a6d8b5" if exists else "#f0aaa5"
            self.structure_items[name].setStyleSheet(f"color: {color};")

    def _schedule_save(self, *_args):
        self.save_timer.start()

    def _save_state(self):
        save_state(self.folder_entry.text(), self._get_options())

    def pick_folder(self):
        initial = self.folder_entry.text().strip()
        if not os.path.isdir(initial):
            initial = APP_DIR
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select PS5 game folder",
            initial,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self.folder_entry.setText(selected)

    def _append(self, text, tag=None):
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(self.LOG_COLORS.get(tag, self.LOG_COLORS["default"])))
        cursor.insertText(text, text_format)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def clear_output(self):
        self.output.clear()

    def start_run(self):
        self._save_state()
        error = runner.start(self.folder_entry.text())
        if error:
            self._append(error + "\n", "warn")

    def start_build(self):
        self._save_state()
        error = runner.start_build(self.folder_entry.text(), self._get_options())
        if error:
            self._append(error + "\n", "warn")

    def check_relinker_updates(self):
        error = runner.start_update()
        if error:
            self._append(error + "\n", "warn")

    def check_gui_updates(self):
        answer = QMessageBox.question(
            self,
            "Check for GUI updates",
            "Check for a new GUI build? If one is available, it will be installed "
            "and the app will restart.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        error = runner.start_gui_update()
        if error:
            self._append(error + "\n", "warn")

    def _set_status(self, status, code):
        labels = {
            **self.COLORS,
            "failed": (
                f"Failed ({code})" if code is not None else "Failed",
                "#493638",
                "#f0b5b0",
            ),
        }
        text, background, foreground = labels.get(
            status, ("Idle", "#343d53", "#c2cff8")
        )
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"QLabel#statusBadge {{ color: {foreground}; }}"
        )
        dot_colors = {
            "idle": "#aab2bf",
            "running": "#ecd09b",
            "done": "#b9e4c7",
            "stopped": "#ecd09b",
            "failed": "#f0b5b0",
        }
        self.status_dot.setStyleSheet(
            f"QLabel#statusDot {{ background: {dot_colors.get(status, '#aab2bf')}; }}"
        )

    def _poll_runner(self):
        if self.closed:
            return
        with runner.lock:
            reset = self.run_id != runner.run_id
            start = 0 if reset else self.log_index
            entries = runner.log[start:]
            self.run_id = runner.run_id
            self.log_index = start + len(entries)
            status = runner.status
            code = runner.code
            restart_requested = runner.restart_requested

        if reset and entries:
            self.clear_output()
        for entry in entries:
            self._append(entry["s"], entry["t"])

        if status != self.last_status:
            self._set_status(status, code)
        self.last_status = status
        running = status == "running"
        for button in (
            self.run_button,
            self.build_button,
            self.gui_update_button,
            self.relinker_update_button,
            self.browse_button,
        ):
            button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.stop_button.setVisible(running)

        if restart_requested:
            self.close()

    def closeEvent(self, event):
        self.closed = True
        self.poll_timer.stop()
        if self.save_timer.isActive():
            self._save_state()
        if not runner.restart_requested:
            runner.stop()
        event.accept()


def main():
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        try:
            dpi_aware = user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except AttributeError:
            dpi_aware = False
        if not dpi_aware:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except AttributeError:
                user32.SetProcessDPIAware()

    application = QApplication(sys.argv)
    _load_app_fonts(application)
    application.setApplicationName("PS5 EXE Toolkit")
    application.setStyle("Fusion")
    window = ToolkitApp()
    window.show()
    return_code = application.exec()
    if not runner.restart_requested:
        runner.stop()
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
