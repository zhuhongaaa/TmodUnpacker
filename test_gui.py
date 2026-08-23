"""offscreen 模式测试 GUI 构造与完整解包流程。"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import tmod_gui

app = QApplication(sys.argv)
w = tmod_gui.MainWindow()
w.show()

TMOD = r"d:\泰拉模组\CalamityModMusic.tmod"
out_dir = tempfile.mkdtemp(prefix="tmod_gui_test_")
w.out_edit.setText(out_dir)
w._add_paths([TMOD])
print("列表文件数:", w.table.rowCount())
print("输出目录:", out_dir)

# 触发解包
w._on_start()

# 在 worker 完成后退出
def check():
    if w.worker is None or not w.worker.isRunning():
        print("worker 已结束")
        print("进度条:", w.progress_bar.value(), w.progress_bar.text())
        print("状态栏:", w.status_label.text())
        print("当前文件标签:", w.cur_file_lbl.text())
        # 检查输出
        n = sum(len(fs) for r, ds, fs in os.walk(out_dir))
        print("磁盘文件数:", n)
        app.quit()
    else:
        QTimer.singleShot(500, check)

QTimer.singleShot(1000, check)
app.exec()
print("测试完成")
