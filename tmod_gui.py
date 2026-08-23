"""
tmod_gui.py
===========
tMod 模组解包工具 - 图形界面(基于 PySide6)。

功能:批量解包 .tmod 文件,保持原始目录结构,显示进度与详细日志。
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QDragEnterEvent, QDropEvent,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QFileDialog, QProgressBar,
    QCheckBox, QTextEdit, QLineEdit, QHeaderView, QStatusBar, QMessageBox,
    QGroupBox, QAbstractItemView, QMenu,
)

import tmod_core


APP_NAME = "tMod 解包工具"
APP_VERSION = "1.0.0"
APP_AUTHOR = "朱鸿"
APP_AUTHOR_EMAIL = "1594528711@qq.com"

# 配色(柔和、和谐,避免突兀跳色)
COL_BG = "#F4F6F9"
COL_CARD = "#FFFFFF"
COL_BORDER = "#E2E6EC"
COL_TEXT = "#2B3138"
COL_TEXT_DIM = "#6B7480"
COL_PRIMARY = "#3D6FB0"
COL_PRIMARY_HOVER = "#356099"
COL_PRIMARY_LIGHT = "#EAF1FA"
COL_SUCCESS = "#3FA371"
COL_WARN = "#D89A2B"
COL_ERROR = "#CC4B43"
COL_ACCENT_FILE = "#5A6B7E"


# ---------------------------------------------------------------------------
# 后台解包线程
# ---------------------------------------------------------------------------

class UnpackWorker(QThread):
    log_sig = Signal(str, str)            # level, message
    progress_sig = Signal(int, int, str)  # done, total, current_filename
    file_status_sig = Signal(int, str)    # row_index, status_text
    overall_sig = Signal(int, int)        # done_files, total_files(全局)
    finished_sig = Signal(int, int, int)  # tmod_ok, tmod_total, files_extracted
    failed_sig = Signal(int, str)         # row_index, error_message

    def __init__(self, files, out_dir, verify_hash, convert_rawimg):
        super().__init__()
        self.files = files                      # [(row_index, path), ...]
        self.out_dir = out_dir
        self.verify_hash = verify_hash
        self.convert_rawimg = convert_rawimg
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        tmod_total = len(self.files)
        tmod_ok = 0
        files_extracted = 0

        # 预先统计全局文件数,用于总体进度
        global_total = 0
        per_tmod_counts = []
        for _, path in self.files:
            try:
                info = tmod_core.parse_tmod(path)
                per_tmod_counts.append(len(info.files))
                global_total += len(info.files)
            except Exception:
                per_tmod_counts.append(0)

        if global_total == 0:
            global_total = 1  # 防止除零

        global_done = 0
        for ti, (row_index, path) in enumerate(self.files):
            if self._cancel:
                self.log_sig.emit("WARN", "用户已取消,已中止后续任务")
                break
            self.file_status_sig.emit(row_index, "解包中…")
            start = time.time()

            def log(level, msg, _ti=ti):
                self.log_sig.emit(level, f"[{os.path.basename(self.files[_ti][1])}] {msg}")

            # 单 tmod 内进度
            local_count = per_tmod_counts[ti]

            def progress(cur, tot, fname, _base=global_done, _tot=local_count,
                         _gtot=global_total, _ti=ti):
                if tot > 0:
                    # 当前 tmod 已完成 cur 个文件
                    done = _base + cur
                    self.overall_sig.emit(done, _gtot)
                    self.progress_sig.emit(cur, tot, fname)

            try:
                n = tmod_core.extract_tmod(
                    path, self.out_dir, log=log, progress=progress,
                    verify_hash=self.verify_hash,
                    convert_rawimg=self.convert_rawimg,
                    cancel_check=lambda: self._cancel,
                )
                files_extracted += n
                global_done += local_count
                self.overall_sig.emit(global_done, global_total)
                elapsed = time.time() - start
                self.file_status_sig.emit(
                    row_index, f"完成({n} 文件, {elapsed:.1f}s)")
                tmod_ok += 1
            except Exception as e:
                self.file_status_sig.emit(row_index, "失败")
                self.failed_sig.emit(row_index, str(e))
                self.log_sig.emit("ERROR",
                                  f"[{os.path.basename(path)}] 致命错误: {e}")
                self.log_sig.emit("ERROR", traceback.format_exc().strip()[-800:])

        self.finished_sig.emit(tmod_ok, tmod_total, files_extracted)


# ---------------------------------------------------------------------------
# 拖拽文件表格
# ---------------------------------------------------------------------------

class DropTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self):
        super().__init__(0, 3)
        self.setAcceptDrops(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(38)
        self.setAlternatingRowColors(True)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setHorizontalHeaderLabels(["文件路径", "大小", "状态"])
        self.setShowGrid(False)
        self.setWordWrap(False)

    # --- 拖拽 ---
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                for root, _, fs in os.walk(p):
                    for f in fs:
                        if f.lower().endswith(".tmod"):
                            paths.append(os.path.join(root, f))
            elif p.lower().endswith(".tmod"):
                paths.append(p)
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(980, 680)
        self.setMinimumSize(820, 560)
        self.worker: UnpackWorker | None = None
        self.file_rows: dict[str, int] = {}  # path -> row

        self._build_ui()
        self._apply_style()
        self._restore_default_output()
        icon_path = _resource_path("app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(14)

        # 标题区
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        sub = QLabel("批量解包 tModLoader .tmod 模组文件 · 保持原始目录结构 · 支持 SHA1 校验")
        sub.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(sub)

        # 拖拽提示条
        self.drop_hint = QLabel(
            "  将 .tmod 文件拖到下方表格,或点击右侧按钮添加  ")
        self.drop_hint.setObjectName("dropHint")
        dh_layout = QHBoxLayout()
        dh_layout.setContentsMargins(0, 0, 0, 0)
        dh_layout.addWidget(self.drop_hint, 1)
        self.add_btn = QPushButton("添加文件")
        self.add_btn.setObjectName("secondaryBtn")
        self.add_btn.clicked.connect(self._on_add_files)
        self.add_dir_btn = QPushButton("添加文件夹")
        self.add_dir_btn.setObjectName("secondaryBtn")
        self.add_dir_btn.clicked.connect(self._on_add_dir)
        dh_layout.addWidget(self.add_btn)
        dh_layout.addWidget(self.add_dir_btn)
        root.addLayout(dh_layout)

        # 文件表格
        self.table = DropTable()
        self.table.files_dropped.connect(self._add_paths)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.setMinimumHeight(320)
        root.addWidget(self.table, 3)

        # 输出目录
        out_box = QGroupBox("输出目录")
        obl = QHBoxLayout(out_box)
        obl.setContentsMargins(12, 10, 12, 10)
        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        self.out_browse_btn = QPushButton("浏览…")
        self.out_browse_btn.setObjectName("secondaryBtn")
        self.out_browse_btn.clicked.connect(self._on_browse_out)
        obl.addWidget(self.out_edit, 1)
        obl.addWidget(self.out_browse_btn)
        root.addWidget(out_box)

        # 选项 + 操作
        opt_row = QHBoxLayout()
        self.chk_hash = QCheckBox("SHA1 完整性校验")
        self.chk_hash.setChecked(True)
        self.chk_png = QCheckBox("同时把 .rawimg 转为 .PNG")
        self.chk_png.setChecked(True)
        self.chk_open = QCheckBox("完成后打开输出目录")
        self.chk_open.setChecked(True)
        opt_row.addWidget(self.chk_hash)
        opt_row.addWidget(self.chk_png)
        opt_row.addWidget(self.chk_open)
        opt_row.addStretch(1)
        self.about_btn = QPushButton("关于")
        self.about_btn.setObjectName("secondaryBtn")
        self.about_btn.clicked.connect(self._on_about)
        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setObjectName("secondaryBtn")
        self.clear_btn.clicked.connect(self._on_clear)
        self.start_btn = QPushButton("开始解包")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.clicked.connect(self._on_start)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("secondaryBtn")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        opt_row.addWidget(self.about_btn)
        opt_row.addWidget(self.clear_btn)
        opt_row.addWidget(self.cancel_btn)
        opt_row.addWidget(self.start_btn)
        root.addLayout(opt_row)

        # 进度
        prog_box = QGroupBox("进度")
        prog_box.setMaximumHeight(100)
        pl = QVBoxLayout(prog_box)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(6)
        self.cur_file_lbl = QLabel("待开始")
        self.cur_file_lbl.setObjectName("dim")
        self.cur_file_lbl.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("就绪")
        pl.addWidget(self.cur_file_lbl)
        pl.addWidget(self.progress_bar)
        root.addWidget(prog_box)

        # 日志(可折叠,默认折叠以让表格占更多空间)
        self.log_group = QGroupBox("解包日志")
        self.log_group.setMaximumHeight(220)
        ll = QVBoxLayout(self.log_group)
        ll.setContentsMargins(12, 10, 12, 10)
        log_row = QHBoxLayout()
        self.log_toggle = QPushButton("显示")
        self.log_toggle.setObjectName("linkBtn")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(True)
        self.log_toggle.clicked.connect(self._toggle_log)
        self.clear_log_btn = QPushButton("清空日志")
        self.clear_log_btn.setObjectName("linkBtn")
        self.clear_log_btn.clicked.connect(lambda: self.log_edit.clear())
        log_row.addWidget(QLabel("实时日志"))
        log_row.addStretch(1)
        log_row.addWidget(self.clear_log_btn)
        log_row.addWidget(self.log_toggle)
        ll.addLayout(log_row)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName("logEdit")
        ll.addWidget(self.log_edit)
        self.log_group.setMaximumHeight(48)  # 折叠时仅显示标题栏
        self.log_edit.setVisible(False)
        self.clear_log_btn.setVisible(False)
        root.addWidget(self.log_group)

        # 状态栏
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_label = QLabel("就绪")
        sb.addWidget(self.status_label)

    # ---------------- 样式 ----------------
    def _apply_style(self):
        f = QFont("Microsoft YaHei UI", 9)
        QApplication.setFont(f)
        self.setStyleSheet(f"""
            QWidget {{ background: {COL_BG}; color: {COL_TEXT}; }}
            QLabel {{ background: transparent; }}
            #title {{ font-size: 19px; font-weight: 600; color: {COL_TEXT}; }}
            #subtitle {{ color: {COL_TEXT_DIM}; font-size: 11px; }}
            #dim {{ color: {COL_TEXT_DIM}; }}
            #dropHint {{
                background: {COL_PRIMARY_LIGHT}; color: {COL_PRIMARY};
                border: 1px dashed {COL_PRIMARY}; border-radius: 8px;
                padding: 8px; font-size: 11px;
            }}
            QGroupBox {{
                background: {COL_CARD}; border: 1px solid {COL_BORDER};
                border-radius: 10px; margin-top: 12px; font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: {COL_TEXT};
            }}
            QTableWidget {{
                background: {COL_CARD}; border: 1px solid {COL_BORDER};
                border-radius: 10px; gridline-color: {COL_BORDER};
                selection-background-color: {COL_PRIMARY_LIGHT};
                selection-color: {COL_TEXT};
                alternate-background-color: #FAFBFC;
            }}
            QHeaderView::section {{
                background: #F0F2F5; color: {COL_TEXT_DIM};
                border: none; padding: 7px; font-weight: 600;
            }}
            QTableWidget::item {{ padding: 6px 8px; border: none; }}
            QLineEdit {{
                background: {COL_CARD}; border: 1px solid {COL_BORDER};
                border-radius: 6px; padding: 6px 8px;
            }}
            QLineEdit:read-only {{ color: {COL_TEXT}; }}
            #primaryBtn {{
                background: {COL_PRIMARY}; color: white; border: none;
                border-radius: 7px; padding: 8px 22px; font-weight: 600;
            }}
            #primaryBtn:hover {{ background: {COL_PRIMARY_HOVER}; }}
            #primaryBtn:disabled {{ background: #B9C6D6; }}
            #secondaryBtn {{
                background: {COL_CARD}; color: {COL_TEXT};
                border: 1px solid {COL_BORDER}; border-radius: 7px;
                padding: 7px 16px;
            }}
            #secondaryBtn:hover {{ background: #EEF1F5; }}
            #secondaryBtn:disabled {{ color: #AAB2BD; }}
            #linkBtn {{
                background: transparent; color: {COL_PRIMARY};
                border: none; padding: 2px 6px;
            }}
            #linkBtn:hover {{ color: {COL_PRIMARY_HOVER}; }}
            QProgressBar {{
                background: #EDF0F3; border: none; border-radius: 7px;
                height: 18px; text-align: center; color: {COL_TEXT};
                font-size: 10px;
            }}
            QProgressBar::chunk {{
                background: {COL_PRIMARY}; border-radius: 7px;
            }}
            #logEdit {{
                background: #1E2329; color: #D7DDE3; border: 1px solid {COL_BORDER};
                border-radius: 8px; font-family: Consolas, 'Microsoft YaHei UI';
                font-size: 10px; padding: 6px;
            }}
            QCheckBox {{ spacing: 6px; background: transparent; }}
            QStatusBar {{ background: {COL_BG}; color: {COL_TEXT_DIM}; }}
            QToolTip {{
                background: {COL_TEXT}; color: white; border: none;
                padding: 5px 8px; border-radius: 4px;
            }}
        """)

    # ---------------- 文件列表管理 ----------------
    def _add_paths(self, paths):
        for p in paths:
            if p in self.file_rows:
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(p))
            self.table.setItem(row, 1, QTableWidgetItem(self._fmt_size(size)))
            st = QTableWidgetItem("就绪")
            st.setForeground(QColor(COL_TEXT_DIM))
            self.table.setItem(row, 2, st)
            self.file_rows[p] = row
        self._refresh_status()

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 .tmod 文件", "", "tMod 模组 (*.tmod);;所有文件 (*.*)")
        if paths:
            self._add_paths(paths)

    def _on_add_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择包含 .tmod 的文件夹")
        if not d:
            return
        found = []
        for root, _, fs in os.walk(d):
            for f in fs:
                if f.lower().endswith(".tmod"):
                    found.append(os.path.join(root, f))
        if found:
            self._add_paths(found)
        else:
            QMessageBox.information(self, APP_NAME, "该文件夹下未找到 .tmod 文件。")

    def _table_menu(self, pos):
        rows = {i.row() for i in self.table.selectedIndexes()}
        menu = QMenu(self)
        a_remove = QAction("移除选中", self)
        a_remove.triggered.connect(lambda: self._remove_rows(rows))
        a_clear_status = QAction("重置状态", self)
        a_clear_status.triggered.connect(lambda: self._reset_status(rows))
        menu.addAction(a_remove)
        menu.addAction(a_clear_status)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _remove_rows(self, rows):
        for r in sorted(rows, reverse=True):
            path = self.table.item(r, 0).text()
            self.file_rows.pop(path, None)
            self.table.removeRow(r)
        self._refresh_status()

    def _reset_status(self, rows):
        for r in rows:
            it = self.table.item(r, 2)
            if it:
                it.setText("就绪")
                it.setForeground(QColor(COL_TEXT_DIM))

    def _on_clear(self):
        if self.worker and self.worker.isRunning():
            return
        self.table.setRowCount(0)
        self.file_rows.clear()
        self._refresh_status()

    def _on_about(self):
        QMessageBox.about(
            self, f"关于 {APP_NAME}",
            f"<h3 style='margin:0'>{APP_NAME}</h3>"
            f"<p style='color:#6B7480;margin:4px 0 10px'>版本 {APP_VERSION}</p>"
            f"<p style='line-height:150%'>"
            f"批量解包 tModLoader .tmod 模组文件,<br>"
            f"保持原始目录结构,支持 SHA1 校验。"
            f"</p>"
            f"<hr style='border:none;border-top:1px solid #E2E6EC;margin:10px 0'>"
            f"<p style='margin:0'>"
            f"作者:<b>{APP_AUTHOR}</b><br>"
            f"邮箱:<a href='mailto:{APP_AUTHOR_EMAIL}' style='color:#3D6FB0'>"
            f"{APP_AUTHOR_EMAIL}</a>"
            f"</p>"
        )

    # ---------------- 输出目录 ----------------
    def _restore_default_output(self):
        default = os.path.join(os.path.expanduser("~"), "Desktop", "tmod_unpacked")
        self.out_edit.setText(default)

    def _on_browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                             self.out_edit.text() or "")
        if d:
            self.out_edit.setText(d)

    # ---------------- 解包流程 ----------------
    def _on_start(self):
        if self.table.rowCount() == 0:
            QMessageBox.information(self, APP_NAME, "请先添加 .tmod 文件。")
            return
        out_dir = self.out_edit.text().strip()
        if not out_dir:
            QMessageBox.warning(self, APP_NAME, "请选择输出目录。")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"无法创建输出目录:\n{e}")
            return

        files = []
        for r in range(self.table.rowCount()):
            p = self.table.item(r, 0).text()
            files.append((r, p))

        self._set_running(True)
        self.log_edit.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0%")
        self.cur_file_lbl.setText("正在初始化…")
        self._log("INFO", f"开始解包 {len(files)} 个 .tmod 文件 -> {out_dir}")
        self._log("INFO", f"选项: SHA1校验={'开' if self.chk_hash.isChecked() else '关'}"
                           f"  rawimg转PNG={'开' if self.chk_png.isChecked() else '关'}")

        self.worker = UnpackWorker(
            files, out_dir,
            verify_hash=self.chk_hash.isChecked(),
            convert_rawimg=self.chk_png.isChecked(),
        )
        self.worker.log_sig.connect(self._on_log)
        self.worker.progress_sig.connect(self._on_progress)
        self.worker.file_status_sig.connect(self._on_file_status)
        self.worker.overall_sig.connect(self._on_overall)
        self.worker.finished_sig.connect(self._on_finished)
        self.worker.failed_sig.connect(self._on_failed)
        self.worker.start()

    def _on_cancel(self):
        if self.worker:
            self._log("WARN", "正在取消…")
            self.worker.cancel()

    def _set_running(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.add_btn.setEnabled(not running)
        self.add_dir_btn.setEnabled(not running)
        self.clear_btn.setEnabled(not running)
        self.out_browse_btn.setEnabled(not running)
        self.table.setAcceptDrops(not running)

    # --- 信号槽 ---
    def _on_log(self, level: str, msg: str):
        self._log(level, msg)

    def _on_progress(self, cur, tot, fname):
        if fname:
            self.cur_file_lbl.setText(f"正在提取: {fname}")

    def _on_file_status(self, row, status):
        it = self.table.item(row, 2)
        if it:
            it.setText(status)
            if status.startswith("完成"):
                it.setForeground(QColor(COL_SUCCESS))
            elif status == "失败":
                it.setForeground(QColor(COL_ERROR))
            elif status == "解包中…":
                it.setForeground(QColor(COL_PRIMARY))
            else:
                it.setForeground(QColor(COL_TEXT_DIM))

    def _on_overall(self, done, total):
        pct = int(done * 100 / total) if total else 0
        self.progress_bar.setValue(pct)
        self.progress_bar.setFormat(f"{pct}%  ({done}/{total})")

    def _on_failed(self, row, err):
        # 状态已在 _on_file_status 处理,这里仅日志
        pass

    def _on_finished(self, tmod_ok, tmod_total, files_extracted):
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("完成")
        self.cur_file_lbl.setText(f"完成: {tmod_ok}/{tmod_total} 个模组,"
                                  f"共提取 {files_extracted} 个文件")
        self._set_running(False)
        self.status_label.setText(
            f"完成 · 成功 {tmod_ok}/{tmod_total} · 文件 {files_extracted}")
        if tmod_ok == tmod_total and tmod_total > 0:
            self._log("INFO", "全部解包成功 ✓")
        else:
            self._log("WARN", f"完成,但有 {tmod_total - tmod_ok} 个模组失败")
        if self.chk_open.isChecked() and tmod_ok > 0:
            self._open_output()

    def _open_output(self):
        out_dir = self.out_edit.text().strip()
        if out_dir and os.path.isdir(out_dir):
            try:
                os.startfile(out_dir)  # type: ignore[attr-defined]
            except Exception as e:
                self._log("WARN", f"无法打开输出目录: {e}")

    # ---------------- 日志 ----------------
    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        color = {
            "INFO": "#9DC3FF",
            "WARN": "#F0C674",
            "ERROR": "#E88B86",
        }.get(level, "#D7DDE3")
        self.log_edit.append(
            f'<span style="color:#7A828C">[{ts}]</span> '
            f'<span style="color:{color}"><b>{level}</b></span> '
            f'<span style="white-space:pre-wrap">{_html_escape(msg)}</span>')
        self.log_edit.verticalScrollBar().setValue(
            self.log_edit.verticalScrollBar().maximum())

    def _toggle_log(self):
        if self.log_toggle.isChecked():
            # 折叠
            self.log_edit.setVisible(False)
            self.clear_log_btn.setVisible(False)
            self.log_group.setMaximumHeight(48)
            self.log_toggle.setText("显示")
        else:
            # 展开
            self.log_edit.setVisible(True)
            self.clear_log_btn.setVisible(True)
            self.log_group.setMaximumHeight(220)
            self.log_toggle.setText("隐藏")

    # ---------------- 辅助 ----------------
    def _refresh_status(self):
        n = self.table.rowCount()
        self.status_label.setText(f"列表中 {n} 个 .tmod 文件" if n else "就绪")
        self.start_btn.setEnabled(n > 0)

    @staticmethod
    def _fmt_size(n: int) -> str:
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} TB"


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace("\n", "<br>"))


def _resource_path(name: str) -> str:
    """兼容 PyInstaller onefile 的资源路径(解压到 _MEIPASS)。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 高 DPI
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
