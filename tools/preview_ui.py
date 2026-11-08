"""Render our own Qt widget offscreen and exercise its simulated measurement UI."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication,QTabWidget
from PySide6.QtCore import QEventLoop,QTimer
from calman_py.ui import Workbench,configure_app

app=QApplication([]); configure_app(app)
w=Workbench(); w.resize(1440,980); w.show()
def wait_task():
    if not w.task: return
    loop=QEventLoop()
    poll=QTimer(); poll.setInterval(10); poll.timeout.connect(lambda:loop.quit() if w.task is None else None); poll.start()
    timer=QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(20000)
    loop.exec(); app.processEvents()
    if w.task: raise RuntimeError('UI task timeout')
w.connect_button.click(); wait_task()
assert w.connected()
w.tabs.setCurrentIndex(3)
w.settle.setValue(0); w.start_button.click(); wait_task()
assert w.session.status=='completed' and len(w.session.records)==39
assert w.progress.value()==39
out=Path(__file__).resolve().parents[1]/'output'; out.mkdir(exist_ok=True)
tabs=w.findChild(QTabWidget)
for i,name in enumerate(('workbench-preview','luminance-preview','gamma-preview','delta-preview')):
    tabs.setCurrentIndex(i)
    if i == 1: w.gray_count.setCurrentText('33')
    if i != 2:
        w.start_button.click(); wait_task()
    app.processEvents(); w.grab().save(str(out/(name+'.png')))
w.disconnect_all(); w.close(); app.processEvents()
print('UI simulated sweep passed; output/workbench-preview.png')
