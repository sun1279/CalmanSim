"""Local Qt workbench. Serial I/O runs outside the UI thread."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from dataclasses import replace
import json
import math
import threading
from PySide6.QtCore import Qt,QThread,Signal,QPointF,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QPolygonF,QFont,QFontDatabase
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QFormLayout,QGroupBox,QComboBox,QDoubleSpinBox,QSpinBox,QPushButton,QLabel,QCheckBox,
    QTableWidget,QTableWidgetItem,QHeaderView,QTabWidget,QProgressBar,QPlainTextEdit,
    QFileDialog,QScrollArea,QSplitter,QMessageBox)
from .color_math import (TargetConfig,validate_config,resolve_pattern,xyz_to_xyY,eotf,calculate_gamma_metrics,
    calculate_cct,calculate_rgb_balance,calculate_four_color_matrix)
from .patterns import PATTERNS,build_measurement_patterns,GAMUT_BOUNDARY_IDS,PATTERN_MAP
from .presets import GAMUTS,WHITE_POINTS,SIMULATED_DISPLAY_PROFILES
from .devices import CA410,MurideoSIXG,SimulatedRig,available_ports
from .session import Session,run_measurements
from .measurement import MeasurementOptions


STYLE='''
QWidget { background:#111821; color:#dce5ef; font-family:"Microsoft YaHei UI"; font-size:12px; }
QMainWindow { background:#111821; }
QGroupBox { border:1px solid #2c3948; border-radius:8px; margin-top:12px; padding:14px 9px 9px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 4px; color:#99b0c7; }
QComboBox,QDoubleSpinBox,QSpinBox { background:#1b2633; border:1px solid #34465a; border-radius:4px; padding:5px; min-height:19px; }
QPushButton { background:#213349; border:1px solid #3b526d; border-radius:5px; padding:8px 12px; }
QPushButton:hover { background:#2b4561; }
QPushButton:disabled { color:#687889; border-color:#283442; }
QPushButton#primary { background:#087f8c; border-color:#22a5ae; color:white; font-weight:700; }
QPushButton#stop { background:#613b31; border-color:#9d6450; }
QTableWidget { background:#141e29; alternate-background-color:#192634; gridline-color:#29394b; border:1px solid #2c3948; }
QHeaderView::section { background:#213044; color:#a9c2d9; padding:7px; border:none; }
QTabWidget::pane { border:1px solid #2c3948; }
QTabBar::tab { background:#192633; padding:9px 18px; }
QTabBar::tab:selected { background:#264257; color:#69d4d1; }
QProgressBar { border:1px solid #34465a; border-radius:4px; text-align:center; height:18px; }
QProgressBar::chunk { background:#168996; }
QPlainTextEdit { background:#101720; border:1px solid #2c3948; color:#9ab0c5; }
QScrollArea { border:none; }
QLabel#stat { background:#1a2937; border:1px solid #2c4053; border-radius:7px; padding:10px; font-size:15px; }
'''


class Task(QThread):
    succeeded=Signal(object)
    failed=Signal(str)
    point=Signal(object)

    def __init__(self,fn,parent=None):
        super().__init__(parent); self.fn=fn; self.cancel=threading.Event()

    def run(self):
        try: self.succeeded.emit(self.fn(self))
        except Exception as e: self.failed.emit(str(e))


class Plot(QWidget):
    def __init__(self,kind,parent=None):
        super().__init__(parent); self.kind=kind; self.session=None; self.config=TargetConfig(); self.gamma_metric='point'
        self.setMinimumSize(430,240)

    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#141f2b'))
        area=QRectF(58,30,self.width()-88,self.height()-78)
        records=self.session.records if self.session else []
        c=self.session.config if self.session else self.config
        summary=self.session.summary() if self.session else {}
        if summary.get('analysis_config'):
            values=dict(summary['analysis_config']); values['white_xy']=tuple(values['white_xy']); c=TargetConfig(**values)
        def text(x,y,t,color='#9bb0c5'):
            p.setPen(QColor(color)); p.drawText(QPointF(x,y),t)
        def line(points,color,width=2,dashed=False):
            pen=QPen(QColor(color),width)
            if dashed: pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen); p.drawPolyline(QPolygonF(points))
        def axes(xmax,ymax):
            for i in range(6):
                x=area.left()+area.width()*i/5; y=area.bottom()-area.height()*i/5
                line([QPointF(x,area.top()),QPointF(x,area.bottom())],'#263747',1)
                line([QPointF(area.left(),y),QPointF(area.right(),y)],'#263747',1)
                text(x-8,area.bottom()+22,f'{xmax*i/5:g}')
                text(area.left()-48,y+4,f'{ymax*i/5:.2g}')
            return lambda x,y:QPointF(area.left()+x/xmax*area.width(),area.bottom()-y/ymax*area.height())
        def dot(pt,color,size=4):
            p.setPen(QPen(QColor(color),1)); p.setBrush(QColor(color)); p.drawEllipse(pt,size,size)
        if self.kind=='xy':
            # A square xy scale preserves the geometry of the six-color boundary.
            side=min(area.width(),area.height()); area=QRectF(area.left()+(area.width()-side)/2,area.top(),side,side)
            pos=axes(.8,.8)
            target_xy=[xyz_to_xyY(resolve_pattern(PATTERN_MAP[k],c)['target_xyz'])[:2] for k in GAMUT_BOUNDARY_IDS]
            line([pos(*v) for v in (*target_xy,target_xy[0])],'#758dff',2,True)
            summary=self.session.summary() if self.session else {}
            boundary=summary.get('gamut_xy',{}).get('measured_polygon')
            if boundary:
                line([pos(*v) for v in (*boundary,boundary[0])],'#48c5bd',2)
            for r in records:
                target=xyz_to_xyY(r['target_xyz']); actual=xyz_to_xyY(r.get('analysis_xyz',r['raw_xyz']))
                if min(r['raw_xyz'])<0 or min(r.get('analysis_xyz',r['raw_xyz']))<0 or actual[0] is None: continue
                a,b=pos(*target[:2]),pos(*actual[:2]); line([a,b],'#5a6475',1)
                dot(a,'#758dff',2); dot(b,'#edc275',3)
            if boundary:
                for label,xy in zip(('R','Y','G','C','B','M'),boundary):
                    point=pos(*xy); text(point.x()+6,point.y()-5,label,'#6bd4ce')
            text(16,19,'CIE 1931 xy · 六点多边形 R–Y–G–C–B–M')
            if summary.get('gamut_status')=='invalid':
                text(16,area.top()+16,'六色面积无效：'+summary.get('gamut_message',''),'#efb268')
            text(area.right()+10,area.bottom(),'x')
            text(area.left(),area.top()-10,'y')
        elif self.kind in ('luminance','gamma'):
            summary=self.session.summary() if self.session else {}
            gray=summary.get('grayscale',[])
            ymax=max(c.white*1.1,max((r['Y'] for r in gray),default=0)*1.1) if self.kind=='luminance' else max(4,max((r['gamma'] for r in gray if r['gamma'] is not None),default=0)*1.1)
            pos=axes(100,ymax)
            if self.kind=='luminance':
                if not self.session or summary.get('references',{}).get('ready'):
                    points=[pos(i,float(eotf(i/100,c))*c.white) for i in range(101)]
                    line(points,'#758dff',2,True)
                actual=[pos(r['signal']*100,r['Y']) for r in gray]
                text(16,19,'灰阶亮度 · cd/m²（蓝：目标 / 金：实测）')
            else:
                actual=[pos(r['signal']*100,r['gamma']) for r in gray if r['gamma'] is not None and 0<=r['gamma']<=ymax]
                metric=summary.get('gamma_metric',self.gamma_metric)
                if not self.session or summary.get('references',{}).get('ready'):
                    target_points=[]
                    for i in range(1,100):
                        target=calculate_gamma_metrics(i/100,float(eotf(i/100,c))*c.white,c)[metric]
                        if target is not None and 0<=target<=ymax: target_points.append(pos(i,target))
                    if target_points: line(target_points,'#758dff',2,True)
                text(16,19,{'point':'GammaPoint · 按所选 EOTF 与会话参考', 'absolute':'GammaAbsolute · Y/W 的对数指数', 'effective':'有效 Gamma · 扣除会话黑位'}[metric])
                if not actual: text(area.left()+20,area.center().y(),'测量黑、白场和中间灰阶后显示')
            if actual: line(actual,'#edc275',2)
            for pt in actual: dot(pt,'#edc275')
            text(area.right()-70,area.bottom()+40,'信号 %')
        elif self.kind=='rgb_balance':
            summary=self.session.summary() if self.session else {}
            gray=[r for r in summary.get('grayscale',[]) if r.get('rgb_balance') is not None and r['signal']>0]
            vals=[v for r in gray for v in r['rgb_balance']]
            ymin=min(80,min(vals,default=90)-5); ymax=max(120,max(vals,default=110)+5)
            for i in range(6):
                x=area.left()+area.width()*i/5; y=area.bottom()-area.height()*i/5
                line([QPointF(x,area.top()),QPointF(x,area.bottom())],'#263747',1)
                line([QPointF(area.left(),y),QPointF(area.right(),y)],'#263747',1)
                text(x-8,area.bottom()+22,f'{20*i:g}')
                text(area.left()-48,y+4,f'{ymin+(ymax-ymin)*i/5:.0f}%')
            pos=lambda x,y:QPointF(area.left()+x/100*area.width(),area.bottom()-(y-ymin)/(ymax-ymin)*area.height())
            line([pos(0,100),pos(100,100)],'#758dff',1,True)
            for ch,col in ((0,'#ef6868'),(1,'#58d48d'),(2,'#5da5ff')):
                pts=[pos(r['signal']*100,r['rgb_balance'][ch]) for r in gray]
                if pts: line(pts,col,2)
                for pt in pts: dot(pt,col,3)
            text(16,19,'灰阶 RGB 平衡 · %（100% 基准，红 R / 绿 G / 蓝 B）')
            if not gray: text(area.left()+20,area.center().y(),'测量灰阶后显示 RGB 平衡曲线')
            text(area.right()-70,area.bottom()+40,'信号 %')
        else:
            de=[r['delta_e']['de2000'] if r.get('delta_e') else None for r in records]
            ymax=max(5,max((v for v in de if v is not None),default=0)*1.15)
            pos=axes(max(1,len(de)),ymax)
            line([pos(0,3),pos(max(1,len(de)),3)],'#b06d54',1,True)
            for i,v in enumerate(de):
                if v is None: continue
                x=pos(i+.15,0).x(); y=pos(0,v).y(); width=max(1,area.width()/max(1,len(de))*.65)
                p.fillRect(QRectF(x,y,width,area.bottom()-y),QColor('#52beb4' if v<=3 else '#e3a45c'))
            text(16,19,'CIEDE2000 · 共同参考白 / 包含亮度误差')
        p.end()


def spin(value,lo,hi,decimals=2):
    s=QDoubleSpinBox(); s.setRange(lo,hi); s.setDecimals(decimals); s.setValue(value); return s


class Workbench(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle('ColorLab · CA410 / Murideo SDR 测量工作台')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(1440,980); self.setMinimumSize(1100,760)
        self.session=None; self.meter=None; self.generator=None; self.task=None; self.close_pending=False
        self.records={}; self.plots=[]; self._building=True
        self.correction_options=MeasurementOptions()
        self.sessions={}; self.active_key=None; self.table_patterns=[]; self.row_by_id={}
        root=QWidget(); self.setCentralWidget(root); layout=QHBoxLayout(root)
        sidebar=QWidget(); sidebar.setFixedWidth(310); side=QVBoxLayout(sidebar)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(sidebar); scroll.setFixedWidth(330)
        layout.addWidget(scroll)
        title=QLabel('ColorLab'); title.setStyleSheet('font-size:27px; font-weight:700; color:#6bd4ce;')
        side.addWidget(title); side.addWidget(QLabel('SDR 测量与验证工作台'))
        self.device_box=QGroupBox('01 设备'); f=QFormLayout(self.device_box)
        self.mode=QComboBox(); self.mode.addItems(['模拟设备（无需硬件）','CA410 + Murideo SIX-G 实机'])
        f.addRow('工作模式',self.mode)
        self.profile=QComboBox()
        for key,p in SIMULATED_DISPLAY_PROFILES.items(): self.profile.addItem(p['name'],key)
        f.addRow('模拟面板',self.profile)
        self.meter_port=QComboBox(); self.meter_port.setEditable(True)
        self.source_port=QComboBox(); self.source_port.setEditable(True)
        f.addRow('CA410 串口',self.meter_port); f.addRow('SIX-G 串口',self.source_port)
        self.refresh_button=QPushButton('刷新串口'); self.refresh_button.clicked.connect(self.refresh_ports); f.addRow(self.refresh_button)
        self.channel=QSpinBox(); self.channel.setRange(0,99); f.addRow('校准通道',self.channel)
        self.sync=spin(60,.5,240); f.addRow('内部同步 Hz',self.sync)
        self.sync_mode=QComboBox()
        for value,label in ((4,'INTERNAL'),(3,'UNIVERSAL'),(0,'NTSC'),(1,'PAL'),(2,'EXTERNAL'),(5,'MANUAL')):
            self.sync_mode.addItem(label,value)
        f.addRow('同步模式',self.sync_mode)
        self.exposure=spin(100,4,4000,1); f.addRow('MANUAL 曝光 ms',self.exposure)
        self.speed=QComboBox()
        for value,label in enumerate(('SLOW','FAST','LTD.AUTO','AUTO','ORG.AUTO（限支持探头）')): self.speed.addItem(label,value)
        f.addRow('采样速度',self.speed)
        self.timeout=spin(30,5,120,0); f.addRow('读数超时 s',self.timeout)
        self.connect_button=QPushButton('连接设备'); self.connect_button.clicked.connect(self.connect_devices); f.addRow(self.connect_button)
        self.zero_button=QPushButton('零校准（先将探头切到 ZERO）'); self.zero_button.clicked.connect(self.zero); f.addRow(self.zero_button)
        self.device_status=QLabel('未连接'); self.device_status.setWordWrap(True); f.addRow(self.device_status); side.addWidget(self.device_box)
        self.target_box=QGroupBox('02 测量目标'); f=QFormLayout(self.target_box)
        self.gamut=QComboBox()
        for key,g in GAMUTS.items(): self.gamut.addItem(g['name'],key)
        f.addRow('目标色域',self.gamut)
        self.whitepoint=QComboBox()
        for key,w in WHITE_POINTS.items(): self.whitepoint.addItem(w['name'],key)
        self.whitepoint.addItem('自定义 xy','custom'); f.addRow('参考白',self.whitepoint)
        self.wx=spin(.312713,.000001,.999999,6); self.wy=spin(.329016,.000001,.999999,6)
        row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(self.wx); h.addWidget(self.wy); f.addRow('白点 x / y',row)
        self.gamma=QComboBox(); self.gamma.addItem('Power Gamma','power'); self.gamma.addItem('sRGB','srgb'); self.gamma.addItem('BT.1886','bt1886'); f.addRow('EOTF',self.gamma)
        self.exponent=spin(2.2,.1,10,3); f.addRow('Gamma 指数',self.exponent)
        self.black=spin(0,0,9999,5); self.white=spin(100,.00001,10000,3)
        f.addRow('目标黑位 nit',self.black); f.addRow('目标白场 nit',self.white)
        self.depth=QComboBox(); self.depth.addItem('8 bit',8); self.depth.addItem('10 bit（模拟/计算）',10); f.addRow('码值精度',self.depth)
        self.range=QComboBox(); self.range.addItem('Full · 0–255','full'); self.range.addItem('Limited · 16–235','limited'); f.addRow('信号范围',self.range)
        self.round_percent=QCheckBox('信号百分比取整（Calman 默认）'); self.round_percent.setChecked(True); f.addRow(self.round_percent)
        side.addWidget(self.target_box)
        self.analysis_box=QGroupBox('计算与校正'); f=QFormLayout(self.analysis_box)
        self.black_ref=QComboBox()
        for label,key in (('手动黑位','manual'),('实测黑场','measured'),('随 EOTF（BT.1886 实测）','auto')): self.black_ref.addItem(label,key)
        self.white_ref=QComboBox(); self.white_ref.addItem('手动白场','manual'); self.white_ref.addItem('实测白场亮度','measured')
        self.gamma_metric=QComboBox()
        for label,key in (('GammaPoint','point'),('GammaAbsolute','absolute'),('有效 Gamma','effective')): self.gamma_metric.addItem(label,key)
        self.include_black=QCheckBox('黑场计入平均 / 最大色差')
        f.addRow('黑位来源',self.black_ref); f.addRow('白场来源',self.white_ref); f.addRow('Gamma 指标',self.gamma_metric); f.addRow(self.include_black)
        self.correction_label=QLabel('无软件校正'); self.correction_label.setWordWrap(True); f.addRow(self.correction_label)
        self.correction_button=QPushButton('导入 XYZ 校正矩阵 JSON'); self.correction_button.clicked.connect(self.load_correction); f.addRow(self.correction_button)
        self.clear_correction_button=QPushButton('清除软件校正'); self.clear_correction_button.clicked.connect(self.clear_correction); f.addRow(self.clear_correction_button)
        self.reference_status=QLabel('参考：手动黑白'); self.reference_status.setWordWrap(True); f.addRow(self.reference_status)
        side.addWidget(self.analysis_box)
        self.flow_box=QGroupBox('03 测量流程'); f=QFormLayout(self.flow_box)
        self.settle=spin(.5,0,60,2); self.patch_size=QSpinBox(); self.patch_size.setRange(1,100); self.patch_size.setValue(100)
        f.addRow('稳定等待 s',self.settle); f.addRow('色块窗口 %',self.patch_size)
        self.sample_count=QSpinBox(); self.sample_count.setRange(1,100); self.sample_count.setValue(1)
        self.sample_interval=spin(0,0,60,2); self.end_black=QCheckBox('完成 / 停止后显示全场黑色')
        f.addRow('每点采样次数',self.sample_count); f.addRow('采样间隔 s',self.sample_interval); f.addRow(self.end_black)
        self.gray_count=QComboBox(); self.gray_count.setEditable(True)
        self.gray_count.addItems(['17','33','65','129','256'])
        from PySide6.QtGui import QIntValidator
        self.gray_count.setValidator(QIntValidator(2,1024,self.gray_count))
        self.gray_count.setToolTip('包含黑、白端点；也可输入自定义点数。8 bit Full 最多 256 点。')
        self.sequence_label=QLabel(); self.sequence_label.setWordWrap(True)
        side.addWidget(self.flow_box); side.addStretch()
        main=QVBoxLayout(); layout.addLayout(main,1)
        self.banner=QLabel('模拟模式 · 所有模拟读数均标记 SIMULATED'); self.banner.setStyleSheet('background:#1c3c42; color:#83dfd3; padding:11px; border-radius:6px; font-weight:700;'); main.addWidget(self.banner)
        stats=QHBoxLayout(); self.stats=[]
        for label in ('平均 ΔE00','最大 ΔE00','色域覆盖 xy','平均 GammaPoint'):
            item=QLabel(label+'\n—'); item.setObjectName('stat'); stats.addWidget(item,1); self.stats.append(item)
        main.addLayout(stats)
        bar=QHBoxLayout(); self.start_button=QPushButton('测量序列'); self.start_button.setObjectName('primary')
        self.single_button=QPushButton('测量选中色块'); self.resume_button=QPushButton('继续未完成序列')
        self.stop_button=QPushButton('停止'); self.stop_button.setObjectName('stop'); self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(lambda:self.start_measurement()); self.single_button.clicked.connect(lambda:self.start_measurement(single=True))
        self.resume_button.clicked.connect(self.resume); self.stop_button.clicked.connect(self.stop)
        for b in (self.start_button,self.single_button,self.resume_button,self.stop_button): bar.addWidget(b)
        bar.addStretch()
        self.gray_count_label=QLabel('灰阶点数（含黑白）')
        self.gray_count.setFixedWidth(90)
        bar.addWidget(self.gray_count_label); bar.addWidget(self.gray_count)
        main.addLayout(bar); main.addWidget(self.sequence_label)
        self.progress=QProgressBar(); self.progress.setValue(0); main.addWidget(self.progress)
        splitter=QSplitter(Qt.Orientation.Vertical); main.addWidget(splitter,1)
        self.tabs=QTabWidget(); tabs=self.tabs
        for kind,label in (('xy','CIE xy / 色域'),('luminance','灰阶亮度'),('gamma','Gamma 曲线'),('delta','色准 ΔE'),('rgb_balance','RGB 平衡')):
            plot=Plot(kind); self.plots.append(plot); tabs.addTab(plot,label)
        splitter.addWidget(tabs)
        self.table=QTableWidget(len(PATTERNS),14)
        self.table.setHorizontalHeaderLabels(['Pattern','R','G','B','实测 x','实测 y','实测 Y nit','target_x','target_y','目标 Y','ΔE76','ΔE94','ΔE00','ΔE00 LC'])
        self.table.setAlternatingRowColors(True); self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0,160)
        for i in range(1,14): self.table.setColumnWidth(i,74 if i>3 else 42)
        self.table.itemSelectionChanged.connect(self.show_selection); splitter.addWidget(self.table); splitter.setSizes([380,340])
        self.selection=QLabel(''); self.selection.setWordWrap(True); main.addWidget(self.selection)
        files=QHBoxLayout(); self.save_button=QPushButton('保存会话 JSON'); self.csv_button=QPushButton('导出测量 CSV'); self.load_button=QPushButton('打开会话')
        self.save_button.clicked.connect(self.save_session); self.csv_button.clicked.connect(self.export_csv); self.load_button.clicked.connect(self.load_session)
        for b in (self.save_button,self.csv_button,self.load_button): files.addWidget(b)
        main.addLayout(files); self.log=QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(85); main.addWidget(self.log)
        self.mode.currentIndexChanged.connect(self.mode_changed); self.profile.currentIndexChanged.connect(self.mode_changed)
        self.whitepoint.currentIndexChanged.connect(self.white_changed); self.gamut.currentIndexChanged.connect(self.gamut_changed)
        for w in (self.gamut,self.whitepoint,self.depth,self.range): w.currentIndexChanged.connect(self.settings_changed)
        for w in (self.wx,self.wy): w.valueChanged.connect(self.settings_changed)
        self.gamma.currentIndexChanged.connect(self.analysis_changed)
        for w in (self.exponent,self.black,self.white): w.valueChanged.connect(self.analysis_changed)
        for w in (self.black_ref,self.white_ref,self.gamma_metric): w.currentIndexChanged.connect(self.analysis_changed)
        self.include_black.toggled.connect(self.analysis_changed)
        self.sync_mode.currentIndexChanged.connect(self.update_controls)
        self.round_percent.toggled.connect(self.settings_changed)
        self.tabs.currentChanged.connect(self.workflow_changed)
        self.gray_count.currentTextChanged.connect(self.workflow_changed)
        self._building=False; self.refresh_ports(); self.workflow_changed(); self.update_controls()
        self.log.appendPlainText('就绪。先连接模拟设备，再点击“测量序列”。实机驱动尚未通过硬件验证。')

    def config(self):
        return validate_config(TargetConfig(gamut=self.gamut.currentData(),white_xy=(self.wx.value(),self.wy.value()),
            gamma=self.gamma.currentData(),exponent=self.exponent.value(),black=self.black.value(),white=self.white.value(),
            bit_depth=self.depth.currentData(),range=self.range.currentData(),round_percent=self.round_percent.isChecked()))

    def measurement_options(self):
        return replace(self.correction_options,black_reference=self.black_ref.currentData(),white_reference=self.white_ref.currentData(),
                       gamma_metric=self.gamma_metric.currentData(),include_black=self.include_black.isChecked(),
                       sample_count=self.sample_count.value(),sample_interval=self.sample_interval.value(),end_black=self.end_black.isChecked(),legacy=False)

    def analysis_changed(self):
        if self._building or self.task: return
        try:
            config=self.config(); options=self.measurement_options()
            for session in self.sessions.values():
                session.config=config
                session.options=replace(session.options,black_reference=options.black_reference,white_reference=options.white_reference,
                    include_black=options.include_black,gamma_metric=options.gamma_metric,correction_name=options.correction_name,
                    correction_matrix=options.correction_matrix,correction_offset=options.correction_offset,legacy=False)
                session.recalculate(); session.save()
            self.fill_table(); self.update_charts(); self.update_controls()
        except (ValueError,OSError) as e: self.failed(str(e))

    def load_correction(self):
        path,_=QFileDialog.getOpenFileName(self,'导入 XYZ 校正矩阵','','JSON (*.json)')
        if not path: return
        try:
            source=Path(path)
            if source.stat().st_size>100_000: raise ValueError('校正文件过大')
            data=json.loads(source.read_text(encoding='utf-8-sig'))
            if 'matrix' in data:
                matrix=data['matrix']
            elif all(k in data for k in ('ref_white','ref_red','ref_green','ref_blue','meas_white','meas_red','meas_green','meas_blue')):
                matrix=calculate_four_color_matrix(
                    data['ref_white'],data['ref_red'],data['ref_green'],data['ref_blue'],
                    data['meas_white'],data['meas_red'],data['meas_green'],data['meas_blue']
                )
            else:
                raise KeyError('matrix')
            self.correction_options=MeasurementOptions(correction_name=data.get('name',source.stem),
                correction_matrix=matrix,correction_offset=data.get('offset',(0,0,0)))
            self.correction_label.setText('软件校正：'+self.correction_options.correction_name)
            self.analysis_changed()
        except (ValueError,KeyError,TypeError,OSError) as e: self.failed(str(e))

    def clear_correction(self):
        self.correction_options=MeasurementOptions(); self.correction_label.setText('无软件校正'); self.analysis_changed()

    def refresh_ports(self):
        for combo in (self.meter_port,self.source_port):
            value=combo.currentText(); combo.clear()
            for port,description in available_ports(): combo.addItem(port); combo.setItemData(combo.count()-1,description,Qt.ItemDataRole.ToolTipRole)
            if value: combo.setCurrentText(value)

    def gamut_changed(self):
        key='cinema' if self.gamut.currentData()=='dcip3' else 'd65'
        self.whitepoint.setCurrentIndex(self.whitepoint.findData(key)); self.white_changed()

    def white_changed(self):
        key=self.whitepoint.currentData()
        self.wx.setEnabled(key=='custom'); self.wy.setEnabled(key=='custom')
        if key in WHITE_POINTS:
            self.wx.blockSignals(True); self.wy.blockSignals(True)
            self.wx.setValue(WHITE_POINTS[key]['xy'][0]); self.wy.setValue(WHITE_POINTS[key]['xy'][1])
            self.wx.blockSignals(False); self.wy.blockSignals(False)
            self.settings_changed()

    def settings_changed(self):
        if self._building: return
        self.exponent.setEnabled(self.gamma.currentData()=='power')
        self.range.setItemText(0,'Full · 0–1023' if self.depth.currentData()==10 else 'Full · 0–255')
        self.range.setItemText(1,'Limited · 64–940' if self.depth.currentData()==10 else 'Limited · 16–235')
        # Completed data is already autosaved; each new target starts a new session.
        self.sessions={}; self.session=None; self.active_key=None; self.records={}; self.workflow_changed()

    def mode_changed(self):
        if self._building: return
        if self.meter or self.generator:
            self.run_task(lambda task:self.disconnect_all(),lambda _:self.device_status.setText('未连接'))
        self.sessions={}; self.session=None; self.active_key=None; self.records={}; self.workflow_changed()
        sim=self.mode.currentIndex()==0
        self.banner.setText('模拟模式 · 所有读数标记 SIMULATED' if sim else '实机模式 · CA410 / SIX-G 串口驱动待实机验证；显示器输入范围须匹配 Full/Limited')
        self.update_controls()

    def connected(self):
        return bool(self.meter and self.generator and self.meter.connected and self.generator.connected)

    def measurement_mode(self):
        return ('gamut','grayscale','grayscale','accuracy','grayscale')[self.tabs.currentIndex()]

    def gray_count_value(self):
        try: return int(self.gray_count.currentText())
        except ValueError as e: raise ValueError('灰阶点数必须为整数') from e

    def workflow_key(self):
        mode=self.measurement_mode()
        self.gray_count.setVisible(mode=='grayscale'); self.gray_count_label.setVisible(mode=='grayscale')
        return (mode,self.gray_count_value() if mode=='grayscale' else None)

    def planned_patterns(self):
        return build_measurement_patterns(self.measurement_mode(),self.config(),
                                         self.gray_count_value() if self.measurement_mode()=='grayscale' else 17)

    def workflow_changed(self):
        if self._building: return
        mode=self.measurement_mode()
        self.gray_count.setVisible(mode=='grayscale'); self.gray_count_label.setVisible(mode=='grayscale')
        self.gray_count.setEnabled(mode=='grayscale' and self.task is None)
        self.round_percent.setEnabled(mode!='grayscale')
        self.round_percent.setToolTip('灰阶按精确码值生成，不使用百分比取整' if mode=='grayscale' else '')
        if self.task is not None:
            self.sequence_label.setText('正在执行已固定的测量序列；切换图表不会改变本次发送的色块。')
            return
        try:
            patterns=self.planned_patterns(); key=self.workflow_key()
            self.active_key=key; self.session=self.sessions.get(key)
            self.records={r['pattern']['id']:r for r in self.session.records} if self.session else {}
            # Older sessions may contain a user-selected subset or legacy gray IDs.
            if self.session:
                planned_ids={p['id'] for p in patterns}
                patterns+= [p for p in self.session.patterns if p['id'] not in planned_ids]
            self.table_patterns=patterns
            label={'gamut':'色域：白 + RGB + CMY，固定 7 点',
                   'grayscale':f'灰阶：{self.gray_count.currentText()} 点，R=G=B，含黑白端点',
                   'accuracy':'色准：当前完整库，共 39 点'}[mode]
            self.sequence_label.setText(label)
            self.fill_table(); self.update_charts()
            self.progress.setRange(0,len(self.session.patterns) if self.session else len(patterns))
            self.progress.setValue(len(self.session.records) if self.session else 0)
            state={'ready':'就绪','running':'运行中','completed':'已完成','cancelled':'已停止','failed':'失败'}
            self.progress.setFormat(('%v / %m') if not self.session else state[self.session.status]+' · %v / %m')
        except (ValueError,KeyError) as e:
            self.sequence_label.setText(str(e)); self.table_patterns=[]; self.fill_table()
        self.update_controls()

    def update_controls(self):
        busy=self.task is not None; connected=self.connected(); sim=self.mode.currentIndex()==0
        for w in (self.device_box,self.target_box,self.analysis_box,self.flow_box,self.load_button,self.save_button,self.csv_button): w.setEnabled(not busy)
        self.connect_button.setText('断开设备' if connected else '连接设备')
        for w in (self.meter_port,self.source_port,self.channel,self.sync,self.timeout,self.sync_mode,self.speed,self.exposure): w.setEnabled(not sim and not connected)
        self.sync.setEnabled(not sim and not connected and self.sync_mode.currentData()==4)
        self.exposure.setEnabled(not sim and not connected and self.sync_mode.currentData()==5)
        self.profile.setEnabled(sim); self.zero_button.setEnabled(connected)
        self.wx.setEnabled(self.whitepoint.currentData()=='custom'); self.wy.setEnabled(self.whitepoint.currentData()=='custom')
        self.exponent.setEnabled(self.gamma.currentData()=='power')
        self.start_button.setEnabled(connected and not busy); self.single_button.setEnabled(connected and not busy)
        self.resume_button.setEnabled(connected and not busy and self.session is not None and self.session.status in ('cancelled','failed','running') and len(self.session.records)<len(self.session.patterns))
        self.stop_button.setEnabled(busy)
        self.save_button.setEnabled(not busy and bool(self.session)); self.csv_button.setEnabled(not busy and bool(self.session and self.session.records))
        self.load_button.setEnabled(not busy and not connected)
        self.load_button.setToolTip('请先断开设备再打开会话')
        self.gray_count.setEnabled(not busy and self.measurement_mode()=='grayscale')
        self.round_percent.setEnabled(self.measurement_mode()!='grayscale')

    def run_task(self,fn,on_success=None):
        if self.task: return
        task=Task(fn,self); self.task=task
        task.point.connect(self.point_measured)
        if on_success: task.succeeded.connect(on_success)
        task.failed.connect(self.failed)
        task.finished.connect(self.task_finished)
        self.update_controls(); task.start()

    def task_finished(self):
        old=self.task; self.task=None
        if old: old.deleteLater()
        self.update_controls()
        if not self.connected(): self.device_status.setText('未连接')
        if not self.close_pending: self.workflow_changed()
        if self.close_pending: self.close()

    def failed(self,message):
        self.log.appendPlainText('错误：'+message)
        self.progress.setFormat('失败 · 已保留完成的读数')
        self.update_charts()

    def disconnect_all(self):
        devices=[self.meter,self.generator] if self.meter is not self.generator else [self.meter]
        try:
            for d in devices:
                if d:
                    try: d.disconnect()
                    except Exception: pass
        finally: self.meter=None; self.generator=None

    def connect_devices(self):
        if self.connected():
            self.run_task(lambda t:self.disconnect_all(),lambda _:self.device_status.setText('未连接')); return
        sim=self.mode.currentIndex()==0; profile=self.profile.currentData()
        meter_port=self.meter_port.currentText().strip(); source_port=self.source_port.currentText().strip()
        channel=self.channel.value(); sync=self.sync.value(); timeout=self.timeout.value()
        speed=self.speed.currentData(); sync_mode=self.sync_mode.currentData(); exposure=self.exposure.value()
        if not sim and (not meter_port or not source_port or meter_port==source_port):
            self.failed('请选择两个不同的串口'); return
        def work(t):
            self.disconnect_all()
            if sim:
                rig=SimulatedRig(profile); rig.connect(); self.meter=self.generator=rig
            else:
                self.meter=CA410(meter_port,timeout,channel,sync,speed=speed,sync_mode=sync_mode,exposure_ms=exposure); self.generator=MurideoSIXG(source_port)
                try: self.meter.connect(); self.generator.connect()
                except Exception: self.disconnect_all(); raise
            return '模拟 CA410 + SIX-G 已连接' if sim else 'CA410 + SIX-G 已连接（待实机验证）'
        self.run_task(work,self.connected_message)

    def connected_message(self,message):
        if isinstance(self.meter,CA410):
            message+='\n'+json.dumps(self.meter.snapshot(),ensure_ascii=False)+'\n状态：'+json.dumps(self.meter.health,ensure_ascii=False)
        self.device_status.setText(message); self.log.appendPlainText(message)

    def zero(self):
        self.log.appendPlainText('执行零校准；实机应已将探头置于 ZERO 状态。完成后切回 MEASURE。')
        self.run_task(lambda t:self.meter.zero_calibrate(),lambda result:self.log.appendPlainText('零校准完成。'+'; '.join(result.get('warnings',[]))))

    def selected_pattern(self):
        row=self.table.currentRow()
        if not 0<=row<len(self.table_patterns): raise ValueError('请先选择一个色块')
        return self.table_patterns[row]

    def start_measurement(self,single=False):
        try:
            c=self.config(); planned=self.planned_patterns(); key=self.workflow_key()
            selected=self.selected_pattern() if single else None
        except Exception as e: self.failed(str(e)); return
        if not self.connected(): self.failed('请先连接设备'); return
        if self.mode.currentIndex()==1 and c.bit_depth!=8: self.failed('SIX-G 实机模式请选择 8 bit'); return
        if single:
            patterns=[selected]
            # Repeated single reads update the existing session instead of losing prior patches.
            if self.session and self.session.status in ('completed','cancelled','failed') and self.session.config==c and self.session.simulated==(self.mode.currentIndex()==0):
                pid=patterns[0]['id']
                if not any(p['id']==pid for p in self.session.patterns): self.session.patterns.append(patterns[0])
                self.execute([pid]); return
        else:
            patterns=planned
        metadata={'profile':self.profile.currentData() if self.mode.currentIndex()==0 else None,
                  'meter_port':self.meter_port.currentText(),'source_port':self.source_port.currentText(),
                  'settle_seconds':self.settle.value(),'window_percent':self.patch_size.value(),
                  'ca410_channel':self.channel.value(),'sync_hz':self.sync.value(),
                  'measurement_mode':self.measurement_mode(),
                  'gray_count':self.gray_count_value() if self.measurement_mode()=='grayscale' else None}
        self.session=Session(c,self.mode.currentIndex()==0,patterns,metadata,options=self.measurement_options())
        self.active_key=key; self.sessions[key]=self.session
        self.table_patterns=planned
        self.session.save_path=Path(__file__).resolve().parents[1]/'output'/'sessions'/f'{datetime.now():%Y%m%d_%H%M%S}_{self.session.id[:8]}.json'
        self.records={}; self.fill_table(); self.execute()

    def execute(self,pattern_ids=None):
        session=self.session; settle=session.metadata.get('settle_seconds',self.settle.value()); size=session.metadata.get('window_percent',self.patch_size.value())
        self.progress.setRange(0,len(session.patterns)); self.progress.setValue(len(session.records)); self.progress.setFormat('%v / %m')
        self.log.appendPlainText('开始测量 · '+str(session.save_path))
        self.run_task(lambda task:run_measurements(session,self.meter,self.generator,settle,size,task.cancel,task.point.emit,pattern_ids),self.measurements_finished)

    def resume(self):
        if not self.session: return
        if self.session.simulated!=(self.mode.currentIndex()==0): self.failed('会话模式与设备模式不同'); return
        if self.session.simulated and self.session.metadata.get('profile')!=self.profile.currentData(): self.failed('继续测量必须使用相同模拟面板'); return
        if not self.session.simulated and (self.session.metadata.get('ca410_channel')!=self.channel.value() or self.session.metadata.get('sync_hz')!=self.sync.value()):
            self.failed('继续测量必须使用相同 CA410 校准通道与同步频率'); return
        self.execute()

    def measurements_finished(self,session):
        self.progress.setFormat(('已停止' if session.status=='cancelled' else '已完成')+' · %v / %m')
        self.log.appendPlainText(('测量已停止' if session.status=='cancelled' else '测量完成')+'；结果已自动保存。')
        self.update_charts()

    def stop(self):
        if self.task:
            self.task.cancel.set(); self.log.appendPlainText('已请求停止；正在进行的读数将在设备返回或超时后结束。')

    def point_measured(self,r):
        self.records={v['pattern']['id']:v for v in self.session.records}; self.fill_table(); self.update_charts()
        self.progress.setValue(len(self.session.records))
        self.log.appendPlainText(r['pattern']['name']+' · '+('模拟' if self.session.simulated else '实测')+' · Y='+f"{r['raw_xyz'][1]:.5f}"+(' · '+'; '.join(r['warnings']) if r['warnings'] else ''))

    def fill_table(self):
        try: c=self.config()
        except Exception: return
        if self.session: c=self.session.recalculate()[0] or c
        old=self.table_patterns[self.table.currentRow()]['id'] if 0<=self.table.currentRow()<len(self.table_patterns) else None
        self.table.blockSignals(True); self.table.setRowCount(len(self.table_patterns))
        self.row_by_id={p['id']:i for i,p in enumerate(self.table_patterns)}
        for i,p in enumerate(self.table_patterns):
            resolved=resolve_pattern(p,c)
            target_x,target_y,target_Y=xyz_to_xyY(resolved['target_xyz'])
            for j,value in enumerate([p['name'],*resolved['codes'], '—','—','—',f'{target_x:.6f}',f'{target_y:.6f}',f'{target_Y:.5f}','—','—','—','—']):
                self.table.setItem(i,j,QTableWidgetItem(str(value)))
        for key in self.records: self.fill_row(key)
        if self.table_patterns: self.table.selectRow(self.row_by_id.get(old,0))
        self.table.blockSignals(False)
        self.show_selection()

    def fill_row(self,key):
        if key not in self.row_by_id: return
        r=self.records[key]; i=self.row_by_id[key]
        x,y,Y=xyz_to_xyY(r.get('analysis_xyz',r['raw_xyz'])); de=r.get('delta_e') or {}; lc=r.get('delta_e_lc') or {}
        target=xyz_to_xyY(r['target_xyz']) if r.get('reference_ready',True) else (None,None,None)
        values=[r['pattern']['name'],*r['codes'],x,y,Y,*target,de.get('de76'),de.get('de94'),de.get('de2000'),lc.get('de2000')]
        for j,v in enumerate(values):
            text='—' if v is None else f'{v:.6f}' if j in (7,8) else f'{v:.5f}' if j in (4,5,6,9) else f'{v:.3f}' if j>=10 else str(v)
            item=QTableWidgetItem(text); item.setToolTip('; '.join(r['warnings']))
            if j==12 and v is not None: item.setForeground(QColor('#69d4c5' if v<=3 else '#efb268'))
            self.table.setItem(i,j,item)

    def show_selection(self):
        if self._building: return
        try:
            p=self.selected_pattern(); r=self.records.get(p['id']); resolved=resolve_pattern(p,self.config())
            target=xyz_to_xyY(r['target_xyz'] if r else resolved['target_xyz'])
            target_text='目标等待参考读数' if r and not r.get('reference_ready',True) else f"目标 xyY：{target[0]:.6f}, {target[1]:.6f}, {target[2]:.5f} nit"
            extra=''
            if r:
                de=r.get('delta_e') or {}
                xy=xyz_to_xyY(r.get('analysis_xyz',r['raw_xyz']))[:2]
                cct=calculate_cct(xy) if xy[0] is not None else None
                cct_str=f"  |  CCT：{cct:.0f} K" if cct is not None else ''
                itp_str=f"  |  ΔE_ITP：{de['de_itp']:.2f}" if de.get('de_itp') is not None else ''
                extra=f"  |  原始 XYZ：{r['raw_xyz']}  |  计算 XYZ：{r.get('analysis_xyz',r['raw_xyz'])}{cct_str}{itp_str}  |  采样 {r.get('sample_count',1)} 次"
            self.selection.setText(p['label']+'  |  '+target_text+extra+('  |  '+'; '.join(resolved['warnings']) if resolved['warnings'] else ''))
        except Exception as e: self.selection.setText(str(e))

    def update_charts(self):
        summary=self.session.summary() if self.session else {}
        metric=summary.get('gamma_metric',self.gamma_metric.currentData())
        gamma_label={'point':'平均 GammaPoint','absolute':'平均 GammaAbsolute','effective':'平均有效 Gamma'}[metric]
        values=[summary.get('de2000_mean'),summary.get('de2000_max'),summary.get('gamut_xy',{}).get('coverage_percent'),summary.get('gamma_mean')]
        for i,(label,v) in enumerate(zip(('平均 ΔE00','最大 ΔE00','色域覆盖 xy',gamma_label),values)):
            self.stats[i].setText(label+'\n'+('—' if v is None else f'{v:.3f}'+('%' if i==2 else '')))
        if summary.get('gamut_status')=='invalid': self.stats[2].setText('色域覆盖 xy\n无效')
        reference=summary.get('references',{})
        self.reference_status.setText(reference.get('message') or ('参考黑 / 白：'+str(reference.get('black','—'))+' / '+str(reference.get('white','—'))+' nit\n来源：'+str(reference.get('sources',{}))))
        if self.session and self.session.options.legacy: self.reference_status.setText(self.reference_status.text()+'\n旧版会话：保留原计算口径；调整计算选项后采用新口径')
        self.stats[0].setToolTip(f"统计 {summary.get('statistics_count',0)} 点；黑场{'计入' if summary.get('include_black') else '不计入'}")
        self.stats[3].setToolTip(f"GammaPoint 平均：{summary.get('gamma_point_mean','—')}\nGammaAbsolute 平均：{summary.get('gamma_absolute_mean','—')}\n有效 Gamma 平均：{summary.get('gamma_effective_mean','—')}\n实测黑白归一化的对数拟合（独立指标）：{summary.get('gamma_fit','—')}")
        gamut=summary.get('gamut_xy',{}); uv=summary.get('gamut_uv',{})
        self.stats[2].setToolTip(f"xy 六点面积：{gamut.get('measured_area','—')}\nxy 目标面积：{gamut.get('target_area','—')}\nxy 面积比：{gamut.get('area_ratio_percent','—')}%\nu′v′ 覆盖：{uv.get('coverage_percent','—')}%\n六色边界顺序：R→Y→G→C→B→M；白点不参与面积。\n覆盖率计算六点多边形与目标色域交集；面积比可超过 100%。\n"+summary.get('gamut_message',''))
        for plot in self.plots:
            plot.session=self.session
            plot.gamma_metric=self.gamma_metric.currentData()
            try: plot.config=self.config()
            except Exception: pass
            plot.update()
        self.show_selection()

    def save_session(self):
        if not self.session: return
        path,_=QFileDialog.getSaveFileName(self,'保存会话',str(self.session.save_path or 'session.json'),'JSON (*.json)')
        if path:
            try: self.session.save(path); self.log.appendPlainText('已保存：'+path)
            except Exception as e: self.failed(str(e))

    def export_csv(self):
        if not self.session: return
        path,_=QFileDialog.getSaveFileName(self,'导出测量结果','measurements.csv','CSV (*.csv)')
        if path:
            try: self.session.export_csv(path); self.log.appendPlainText('已导出：'+path)
            except Exception as e: self.failed(str(e))

    def load_session(self):
        path,_=QFileDialog.getOpenFileName(self,'打开会话','','JSON (*.json)')
        if not path: return
        try:
            loaded=Session.load(path); c=loaded.config
            measurement_mode=loaded.metadata.get('measurement_mode')
            if measurement_mode not in ('gamut','grayscale','accuracy'):
                ids={p['id'] for p in loaded.patterns}
                gamut_ids={p['id'] for p in build_measurement_patterns('gamut',c)}
                if ids and ids<=gamut_ids: measurement_mode='gamut'
                elif loaded.patterns and all(p['type']=='rgb' and len(set(p['rgb']))==1 for p in loaded.patterns): measurement_mode='grayscale'
                else: measurement_mode='accuracy'
            count=loaded.metadata.get('gray_count') or max(2,len(loaded.patterns)) if measurement_mode=='grayscale' else 17
            # Set controls with change handlers suppressed; mode changes cannot disconnect mid-load.
            self._building=True
            for w in (self.mode,self.profile,self.gamut,self.whitepoint,self.gamma,self.depth,self.range,self.tabs,self.gray_count): w.blockSignals(True)
            self.mode.setCurrentIndex(0 if loaded.simulated else 1)
            if loaded.metadata.get('profile') in SIMULATED_DISPLAY_PROFILES: self.profile.setCurrentIndex(self.profile.findData(loaded.metadata['profile']))
            wp_key=next((k for k,w in WHITE_POINTS.items() if math.isclose(w['xy'][0],c.white_xy[0],abs_tol=1e-6) and math.isclose(w['xy'][1],c.white_xy[1],abs_tol=1e-6)),'custom')
            self.gamut.setCurrentIndex(self.gamut.findData(c.gamut)); self.whitepoint.setCurrentIndex(self.whitepoint.findData(wp_key))
            self.wx.setValue(c.white_xy[0]); self.wy.setValue(c.white_xy[1]); self.gamma.setCurrentIndex(self.gamma.findData(c.gamma))
            self.exponent.setValue(c.exponent); self.black.setValue(c.black); self.white.setValue(c.white)
            self.depth.setCurrentIndex(self.depth.findData(c.bit_depth)); self.range.setCurrentIndex(self.range.findData(c.range)); self.round_percent.setChecked(c.round_percent)
            self.gray_count.setCurrentText(str(count))
            o=loaded.options; self.correction_options=o
            for combo,value in ((self.black_ref,o.black_reference),(self.white_ref,o.white_reference),(self.gamma_metric,o.gamma_metric)):
                combo.setCurrentIndex(combo.findData(value))
            self.include_black.setChecked(o.include_black); self.sample_count.setValue(o.sample_count)
            self.sample_interval.setValue(o.sample_interval); self.end_black.setChecked(o.end_black)
            self.correction_label.setText('软件校正：'+o.correction_name if o.correction_name else '无软件校正')
            self.settle.setValue(loaded.metadata.get('settle_seconds',.5)); self.patch_size.setValue(loaded.metadata.get('window_percent',100))
            devices=loaded.metadata.get('devices',{}); requested=devices.get('meter',{}).get('requested',{})
            self.channel.setValue(requested.get('channel',loaded.metadata.get('ca410_channel',0)))
            self.sync.setValue(requested.get('sync_hz',loaded.metadata.get('sync_hz',60)))
            self.sync_mode.setCurrentIndex(self.sync_mode.findData(requested.get('sync_mode',4)))
            self.speed.setCurrentIndex(self.speed.findData(requested.get('speed',0)))
            self.exposure.setValue(requested.get('exposure_ms',100))
            self.meter_port.setCurrentText(loaded.metadata.get('meter_port',''))
            self.source_port.setCurrentText(loaded.metadata.get('source_port',''))
            self.tabs.setCurrentIndex({'gamut':0,'grayscale':1,'accuracy':3}[measurement_mode])
            for w in (self.mode,self.profile,self.gamut,self.whitepoint,self.gamma,self.depth,self.range,self.tabs,self.gray_count): w.blockSignals(False)
            self._building=False
            self.disconnect_all(); self.session=loaded; self.records={r['pattern']['id']:r for r in loaded.records}
            self.active_key=self.workflow_key(); self.sessions={self.active_key:loaded}
            self.banner.setText('导入会话 · '+('SIMULATED 模拟数据' if loaded.simulated else '真实设备读数'))
            self.workflow_changed()
            self.progress.setRange(0,len(loaded.patterns)); self.progress.setValue(len(loaded.records))
            self.log.appendPlainText('已打开：'+path)
        except Exception as e:
            self._building=False; self.failed(str(e))

    def closeEvent(self,event):
        if self.task:
            self.close_pending=True; self.task.cancel.set(); event.ignore(); return
        if self.connected():
            self.close_pending=True
            self.run_task(lambda t:self.disconnect_all()); event.ignore(); return
        event.accept()


def configure_app(app):
    # Qt's offscreen platform may expose no system fonts on Windows.
    if not QFontDatabase.families():
        for name in ('msyh.ttc','msyhbd.ttc'):
            path=Path('C:/Windows/Fonts')/name
            if path.exists(): QFontDatabase.addApplicationFont(str(path))
    app.setStyleSheet(STYLE); app.setFont(QFont('Microsoft YaHei UI',10))


def launch():
    app=QApplication.instance() or QApplication([]); configure_app(app)
    app.setFont(QFont('Microsoft YaHei UI',10)); window=Workbench(); window.show()
    return app.exec()
