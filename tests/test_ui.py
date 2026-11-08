import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QEventLoop,QTimer,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QFileDialog
from calman_py.ui import Workbench,configure_app
from calman_py.session import Session
import pytest


@pytest.fixture(scope='module')
def app():
    a=QApplication.instance() or QApplication([]); configure_app(a)
    yield a
    a.closeAllWindows()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    a.processEvents()


def wait_task(w,app):
    app.processEvents()
    if not w.task: return
    loop=QEventLoop()
    poll=QTimer(); poll.setInterval(10); poll.timeout.connect(lambda:loop.quit() if w.task is None else None); poll.start()
    timer=QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(10000)
    loop.exec(); app.processEvents()
    assert w.task is None


def test_gui_connect_sweep_repeat_and_config_reset(app):
    w=Workbench(); w.show()
    try:
        assert not w.start_button.isEnabled()
        w.connect_button.click(); wait_task(w,app)
        assert w.connected() and w.start_button.isEnabled()
        w.tabs.setCurrentIndex(3)
        w.settle.setValue(0); w.start_button.click(); wait_task(w,app)
        assert len(w.session.records)==39 and w.session.status=='completed'
        assert f"{w.session.summary()['de2000_mean']:.3f}" in w.stats[0].text()
        assert w.session.summary()['statistics_count']==38
        assert not w.load_button.isEnabled()
        w.table.selectRow(0); w.single_button.click(); wait_task(w,app)
        assert len(w.session.records)==39
        assert len({r['pattern']['id'] for r in w.session.records})==39
        w.gamma.setCurrentIndex(2)
        assert w.session is not None and len(w.records)==39
        assert w.config().gamma=='bt1886'
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_gui_session_load_restores_configuration_and_mode(app,tmp_path,monkeypatch):
    w=Workbench()
    try:
        w.connect_devices(); wait_task(w,app)
        w.tabs.setCurrentIndex(3)
        w.settle.setValue(0); w.start_measurement(); wait_task(w,app)
        path=tmp_path/'s.json'; w.session.save(path)
        w.disconnect_all()
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),'JSON'))
        w.load_session()
        assert w.session is not None and len(w.records)==39
        assert w.config()==w.session.config
        assert w.mode.currentIndex()==0
        assert w.session.simulated
        assert w.table.item(0,6).text()!='—'
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_tabs_dispatch_only_expected_codes_and_keep_mode_results(app):
    w=Workbench()
    try:
        w.connect_devices(); wait_task(w,app); w.settle.setValue(0)
        emitted=[]; original=w.generator.show_pattern
        def show(codes,*args):
            emitted.append(tuple(codes)); return original(codes,*args)
        w.generator.show_pattern=show
        assert w.table.rowCount()==7 and not w.gray_count.isEnabled()
        w.start_button.click(); wait_task(w,app)
        gamut=w.session
        assert emitted==[(255,255,255),(255,0,0),(0,255,0),(0,0,255),(0,255,255),(255,0,255),(255,255,0)]
        assert 'gamut_xy' in gamut.summary()
        assert gamut.summary()['gamut_xy']['point_count']==6
        w.tabs.setCurrentIndex(1); emitted.clear()
        assert w.gray_count.isEnabled() and w.table.rowCount()==17
        w.start_button.click(); wait_task(w,app)
        gray17=w.session
        assert len(emitted)==17 and all(len(set(c))==1 for c in emitted)
        assert emitted[0]==(0,0,0) and emitted[-1]==(255,255,255)
        w.tabs.setCurrentIndex(2)
        assert w.session is gray17 and w.table.rowCount()==17
        w.gray_count.setCurrentText('33'); emitted.clear()
        assert w.table.rowCount()==33
        w.start_button.click(); wait_task(w,app)
        assert len(emitted)==33 and emitted[2]==(16,16,16)
        assert all(len(set(c))==1 for c in emitted)
        assert w.session.summary()['gamma_fit']==pytest.approx(2.08,abs=.003)
        w.tabs.setCurrentIndex(0)
        assert w.session is gamut and w.table.rowCount()==7
        w.tabs.setCurrentIndex(3); emitted.clear()
        assert not w.gray_count.isEnabled() and w.table.rowCount()==39
        w.start_button.click(); wait_task(w,app)
        assert len(emitted)==39 and len(w.session.records)==39
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_changing_tab_while_running_does_not_change_sequence(app):
    w=Workbench()
    try:
        w.connect_devices(); wait_task(w,app); w.settle.setValue(.005)
        w.tabs.setCurrentIndex(1); w.gray_count.setCurrentText('33')
        w.start_measurement(); running=w.session
        w.tabs.setCurrentIndex(3)
        assert w.session is running and len(running.patterns)==33
        wait_task(w,app)
        assert len(running.records)==33
        assert all(len(set(r['codes']))==1 for r in running.records)
        assert w.table.rowCount()==39 and w.session is None
        w.tabs.setCurrentIndex(2)
        assert w.session is running and w.table.rowCount()==33
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_gray33_load_restores_count_and_can_remeasure_dynamic_point(app,tmp_path,monkeypatch):
    w=Workbench()
    try:
        w.tabs.setCurrentIndex(2); w.gray_count.setCurrentText('33')
        w.connect_devices(); wait_task(w,app); w.settle.setValue(0)
        w.start_measurement(); wait_task(w,app)
        path=tmp_path/'gray33.json'; w.session.save(path); w.disconnect_all()
        w.tabs.setCurrentIndex(0)
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),'JSON'))
        w.load_session()
        assert w.measurement_mode()=='grayscale' and w.gray_count.currentText()=='33'
        assert w.table.rowCount()==33 and w.config()==w.session.config
        assert w.table.item(2,1).text()=='16'
        w.connect_devices(); wait_task(w,app)
        w.table.selectRow(2); w.single_button.click(); wait_task(w,app)
        assert len(w.session.records)==33 and w.session.records[2]['codes']==[16,16,16]
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_analysis_controls_recalculate_existing_readings_and_restore_options(app,tmp_path,monkeypatch):
    w=Workbench()
    try:
        w.connect_devices(); wait_task(w,app); w.tabs.setCurrentIndex(1)
        w.settle.setValue(0); w.sample_count.setValue(2)
        w.start_measurement(); wait_task(w,app)
        session=w.session; raw=[list(r['raw_xyz']) for r in session.records]
        codes=[list(r['codes']) for r in session.records]
        assert all(r['sample_count']==2 for r in session.records)
        w.white_ref.setCurrentIndex(w.white_ref.findData('measured'))
        assert w.session is session and session.summary()['references']['white']==pytest.approx(raw[-1][1])
        w.gamma.setCurrentIndex(w.gamma.findData('srgb'))
        assert session.config.gamma=='srgb' and [r['codes'] for r in session.records]==codes
        assert [r['raw_xyz'] for r in session.records]==raw
        path=tmp_path/'analysis.json'; session.save(path); w.disconnect_all()
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(path),'JSON'))
        w.load_session()
        assert w.white_ref.currentData()=='measured' and w.sample_count.value()==2
        assert w.session.summary()['references']['ready']
    finally:
        w.disconnect_all(); w.close(); app.processEvents()


def test_cancelled_session_single_remeasure_keeps_cancelled_and_gray_count_visibility(app,tmp_path,monkeypatch):
    w=Workbench()
    try:
        # Switching from 256-point Full to Limited while on Gamut tab and back to Grayscale keeps gray_count visible
        w.tabs.setCurrentIndex(1); w.gray_count.setCurrentText('256')
        w.tabs.setCurrentIndex(0)
        w.range.setCurrentIndex(w.range.findData('limited'))
        w.tabs.setCurrentIndex(1)
        assert not w.gray_count.isHidden()
        w.range.setCurrentIndex(w.range.findData('full'))
        w.gray_count.setCurrentText('17')
        # Single-patch remeasurement on an incomplete (cancelled) session preserves partial records & cancelled status
        w.connect_devices(); wait_task(w,app); w.settle.setValue(0)
        from calman_py.patterns import build_measurement_patterns
        c=w.config(); pats=build_measurement_patterns('gamut',c)
        s=Session(c,True,pats,{'profile':w.profile.currentData(),'measurement_mode':'gamut'})
        from calman_py.session import run_measurements
        run_measurements(s,w.meter,w.generator,settle=0,pattern_ids=['white','full-red'])
        assert s.status=='cancelled' and len(s.records)==2
        w.tabs.setCurrentIndex(0)
        key=w.workflow_key(); w.sessions[key]=s; w.workflow_changed()
        w.table.selectRow(0); w.single_button.click(); wait_task(w,app)
        assert w.session is s and len(s.records)==2 and s.status=='cancelled'
        assert w.resume_button.isEnabled()
        # RGB balance tab (index 4) maps to grayscale mode
        w.tabs.setCurrentIndex(4)
        assert w.measurement_mode()=='grayscale'
        # Importing Four-Color Matrix JSON via load_correction succeeds without AttributeError
        import json
        from calman_py.color_math import calculate_target
        ref_r=calculate_target([1,0,0],c).tolist()
        ref_g=calculate_target([0,1,0],c).tolist()
        ref_b=calculate_target([0,0,1],c).tolist()
        ref_w=calculate_target([1,1,1],c).tolist()
        fcm_path=tmp_path/'fcm.json'
        fcm_path.write_text(json.dumps({
            'name':'TestFCM',
            'ref_white':ref_w,'ref_red':ref_r,'ref_green':ref_g,'ref_blue':ref_b,
            'meas_white':ref_w,'meas_red':ref_r,'meas_green':ref_g,'meas_blue':ref_b,
        }),encoding='utf-8')
        monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**k:(str(fcm_path),'JSON'))
        w.load_correction()
        assert w.correction_options.correction_name=='TestFCM'
    finally:
        w.disconnect_all(); w.close(); app.processEvents()
