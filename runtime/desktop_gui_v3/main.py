"""Chinese waveform workbench; Qt UI stays independent of SSH and compression."""
import json,math,os,shutil,sys,threading,time,uuid
from datetime import datetime
from pathlib import Path
import numpy as np
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
import pyqtgraph as pg
from . import dataio
from .backend import Backend,Connection,Cancelled,CONFIG
from desktop_gui_v2.library import DEMO,provenance
from .history_store import HISTORY_ROOT,decode_to_result

APP=Path(__file__).resolve().parent
COLORS=['#2875e8','#13a18c','#9a67db','#e29a23']
pg.setConfigOptions(background='w',foreground='#58667b',antialias=False)
STYLE='''
QMainWindow,QWidget#root {background:#f3f5f8;color:#233149;}
QWidget {font-family:"Microsoft YaHei UI","Segoe UI";font-size:12px;color:#233149;}
QFrame#card {background:white;border:1px solid #e3e8f0;border-radius:8px;}
QLabel#title {font-size:19px;font-weight:600;} QLabel#muted {color:#758398;}
QLabel#section {font-weight:600;font-size:13px;} QLabel#value {font-size:23px;font-weight:600;}
QPushButton {background:white;border:1px solid #dce3ed;border-radius:5px;padding:7px 12px;}
QPushButton:hover {background:#edf3ff;border-color:#abc5f4;} QPushButton:disabled {color:#acb5c3;background:#f5f6f8;}
QPushButton#primary {background:#3478e5;color:white;border:0;font-weight:600;padding:8px 18px;}
QPushButton#primary:hover {background:#2667ce;} QPushButton#primary:disabled {background:#afc8ed;}
QPushButton#stop {color:#c65454;} QLineEdit,QComboBox,QDoubleSpinBox {background:white;border:1px solid #dce3ed;border-radius:4px;padding:5px;}
QPushButton#stop:disabled {color:#a9b3c1;}
QProgressBar {border:0;background:#e5ebf4;border-radius:3px;max-height:7px;min-height:7px;} QProgressBar::chunk {background:#3478e5;border-radius:3px;}
QPlainTextEdit {background:white;border:0;color:#758398;font-size:11px;}
QDockWidget {font-weight:600;} QDockWidget::title {background:#edf1f7;padding:9px;}
QCheckBox {spacing:7px;} QSplitter::handle {background:#edf0f5;}
QScrollArea {border:0;background:transparent;}
'''

def label(text='',name=None,wrap=False):
    w=W.QLabel(text)
    if name:w.setObjectName(name)
    w.setWordWrap(wrap);return w
def card():
    f=W.QFrame();f.setObjectName('card');v=W.QVBoxLayout(f);v.setContentsMargins(15,12,15,12);v.setSpacing(7);return f,v
def button(text,slot,name=None):
    b=W.QPushButton(text);b.clicked.connect(slot)
    if name:b.setObjectName(name)
    return b
def pretty(n):
    if n is None:return '—'
    return f'{n/1e9:.2f} GB' if abs(n)>=1e9 else f'{n/1e6:.2f} MB' if abs(n)>=1e6 else f'{n/1000:.2f} KB' if abs(n)>=1000 else f'{n} B'

class Worker(C.QThread):
    event=C.Signal(str,object);okay=C.Signal(object);error=C.Signal(str,bool)
    def __init__(self,action,parent=None):super().__init__(parent);self.action=action;self.cancel=threading.Event()
    def run(self):
        try:self.okay.emit(self.action(lambda k,**v:self.event.emit(k,v),self.cancel))
        except Cancelled as e:self.error.emit(str(e),True)
        except Exception as e:self.error.emit(f'{type(e).__name__}: {e}',False)

class Window(W.QMainWindow):
    def __init__(self,history_root=None):
        super().__init__();self.history_root=Path(history_root or HISTORY_ROOT).resolve();self.history_root.mkdir(parents=True,exist_ok=True);self.action_mode="compress";self.pending_compressed=None;self.decode_result=None
        self.setWindowTitle('多通道无损压缩工作台');self.resize(1460,930);self.setMinimumSize(1080,730);self.setStyleSheet(STYLE)
        self.source={};self.restored={};self.provenance={};self.report=None;self.folder=None;self.worker=None;self.connected=False;self.busy=False;self.closing=False;self.last_error=None;self.last_operation=None;self.history_mode=False;self.board_shutdown=False
        root=W.QWidget();root.setObjectName('root');self.setCentralWidget(root);layout=W.QVBoxLayout(root);layout.setContentsMargins(20,18,16,14);layout.setSpacing(12)
        head=W.QHBoxLayout();titles=W.QVBoxLayout();titles.addWidget(label('多通道无损压缩工作台','title'));titles.addWidget(label('PYNQ-Z2  /  FPGA · ARM 协同','muted'));head.addLayout(titles);head.addStretch()
        self.connection_status=label('●  未连接板卡','muted');head.addWidget(self.connection_status);layout.addLayout(head)
        toolbar=W.QHBoxLayout();self.import_button=button('导入数据',self.choose_input);self.history_button=button('打开历史结果',self.choose_history);self.decode_button=button('解压文件',self.choose_decode);self.fit_button=button('适应波形',self.fit);self.settings_button=button('显示 / 隐藏设置',lambda:self.dock.setVisible(not self.dock.isVisible()))
        for b in (self.import_button,self.history_button,self.decode_button):toolbar.addWidget(b)
        toolbar.addStretch();toolbar.addWidget(self.fit_button);toolbar.addWidget(self.settings_button);self.run_button=button('开始压缩',self.run_primary,'primary');self.stop_button=button('停止',self.stop,'stop');self.stop_button.setEnabled(False);toolbar.addWidget(self.run_button);toolbar.addWidget(self.stop_button);layout.addLayout(toolbar)
        metrics=W.QHBoxLayout();self.metrics={}
        for key,title in [('raw','原始数据大小'),('compressed','压缩文件大小'),('saving','空间节省'),('verified','无损校验')]:
            f,v=card();v.addWidget(label(title,'muted'));value=label('—','value');note=label('等待数据','muted');v.addWidget(value);v.addWidget(note);metrics.addWidget(f);self.metrics[key]=(value,note)
        layout.addLayout(metrics)
        body=W.QSplitter();body.setChildrenCollapsible(False);left,left_v=card();left.setMinimumWidth(160);left.setMaximumWidth(230);left_v.addWidget(label('通道','section'));left_v.addWidget(label('勾选参与压缩的通道','muted'));self.channel_checks=[];self.channel_details=[]
        for i in range(4):
            ch=W.QCheckBox(f'CH {i}');ch.setStyleSheet(f'color:{COLORS[i]};font-weight:600;padding-top:13px;');ch.setEnabled(False);ch.toggled.connect(self.selection_changed);detail=label('未导入','muted',True);self.channel_checks.append(ch);self.channel_details.append(detail);left_v.addWidget(ch);left_v.addWidget(detail)
        left_v.addStretch();left_v.addWidget(label('查看方式','section'));self.view=W.QComboBox();self.view.addItems(['原始波形','还原波形','原始 / 还原叠加','误差（还原－原始）']);self.view.currentIndexChanged.connect(self.redraw);left_v.addWidget(self.view);left_v.addSpacing(8);left_v.addWidget(label('横轴','section'))
        self.axis_mode=W.QComboBox();self.axis_mode.addItems(['采样点序号','时序']);self.axis_mode.currentIndexChanged.connect(self.redraw);left_v.addWidget(self.axis_mode)
        self.rate_label=label('采样率（Hz）','muted');self.rate=W.QLineEdit();self.rate.setPlaceholderText('请输入真实采样率');validator=G.QDoubleValidator(1e-12,1e8,15,self.rate);validator.setNotation(G.QDoubleValidator.Notation.StandardNotation);validator.setLocale(C.QLocale.c());self.rate.setValidator(validator);self.rate.textChanged.connect(self.redraw);self.axis_hint=label('','muted',True);left_v.addWidget(self.rate_label);left_v.addWidget(self.rate);left_v.addWidget(self.axis_hint)
        left_scroll=W.QScrollArea();left_scroll.setWidgetResizable(True);left_scroll.setMinimumWidth(185);left_scroll.setMaximumWidth(240);left_scroll.setWidget(left);self.left_scroll=left_scroll;body.addWidget(left_scroll)
        self.axis_mode.currentIndexChanged.connect(lambda:C.QTimer.singleShot(0,lambda:self.left_scroll.ensureWidgetVisible(self.rate if self.axis_mode.currentIndex()==1 else self.axis_mode)))
        center,cv=card();row=W.QHBoxLayout();self.source_title=label('尚未导入数据','section');row.addWidget(self.source_title);row.addStretch();self.wave_note=label('滚轮缩放 · 拖动平移','muted');row.addWidget(self.wave_note);cv.addLayout(row)
        self.graph=pg.GraphicsLayoutWidget();self.graph.setBackground('w');self.plots=[];self.curves=[]
        for i in range(4):
            p=self.graph.addPlot(row=i,col=0);p.showGrid(x=True,y=True,alpha=.13);p.setLabel('left',f'CH {i}');p.getAxis('left').setWidth(65);p.getAxis('left').enableAutoSIPrefix(False);p.setMouseEnabled(x=True,y=True);p.setMenuEnabled(False);p.setClipToView(True);p.showAxis('bottom',i==3)
            for axis in ('left','bottom'):p.getAxis(axis).setStyle(tickFont=G.QFont('Segoe UI',8));p.getAxis(axis).enableAutoSIPrefix(False)
            if i:p.setXLink(self.plots[0])
            original=p.plot(pen=pg.mkPen(COLORS[i],width=1));rest=p.plot(pen=pg.mkPen('#25374f',width=1,style=C.Qt.PenStyle.DashLine))
            for curve in (original,rest):curve.setDownsampling(auto=True,method='peak');curve.setClipToView(True)
            self.plots.append(p);self.curves.append((original,rest))
        cv.addWidget(self.graph,1);cv.addWidget(label('波形仅用于观察，显示抽稀不参与压缩或校验。数值单位为原始数字量。','muted',True));body.addWidget(center);body.setStretchFactor(1,1);body.setSizes([175,900]);layout.addWidget(body,1)
        bottom,bv=card();progressrow=W.QHBoxLayout();self.phase=label('准备就绪');self.progress_text=label('','muted');progressrow.addWidget(self.phase);progressrow.addStretch();progressrow.addWidget(self.progress_text);bv.addLayout(progressrow);self.progress=W.QProgressBar();self.progress.setTextVisible(False);self.progress.setRange(0,100);bv.addWidget(self.progress);self.log=W.QPlainTextEdit();self.log.setReadOnly(True);self.log.document().setMaximumBlockCount(150);self.log.setMaximumHeight(72);bv.addWidget(self.log);layout.addWidget(bottom)
        self.build_dock();self.update_metrics();self.redraw();self.update_buttons();self.log_line('导入数据后即可压缩；解压和历史回看可离线使用。')
    def build_dock(self):
        self.dock=W.QDockWidget('连接与处理设置',self);self.dock.setAllowedAreas(C.Qt.DockWidgetArea.RightDockWidgetArea);self.dock.setFeatures(W.QDockWidget.DockWidgetFeature.DockWidgetClosable);self.dock.setMinimumWidth(280);self.dock.setMaximumWidth(350)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);pane=W.QWidget();v=W.QVBoxLayout(pane);v.setContentsMargins(14,10,14,10);v.setSpacing(12)
        f,l=card();l.addWidget(label('PYNQ-Z2','section'));form=W.QFormLayout();self.host=W.QLineEdit('192.168.2.99');self.user=W.QLineEdit('xilinx');self.password=W.QLineEdit('');self.password.setEchoMode(W.QLineEdit.EchoMode.Password)
        for title,w in [('地址',self.host),('用户名',self.user),('密码',self.password)]:form.addRow(title,w)
        l.addLayout(form);self.connect_button=button('检查连接',self.connect_board);l.addWidget(self.connect_button);self.shutdown_button=button('板卡关机',self.request_shutdown,'stop');self.shutdown_button.setToolTip('正常关闭板卡 Linux 系统。电源灯可能仍亮，再次使用需要重新上电。');l.addWidget(self.shutdown_button);self.version_status=label('正式版本 unified-v1\n加载前核对设计及运行文件','muted',True);l.addWidget(self.version_status);v.addWidget(f)
        for field in (self.host,self.user,self.password):field.textEdited.connect(self.connection_changed)
        f,l=card();l.addWidget(label('处理与保存','section'));self.group=W.QComboBox();self.group.addItems(['批量 2 块 · 默认','批量 1 块']);l.addWidget(self.group);l.addWidget(label('1024 / 8192 点完整候选比较\n自适应 Huffman · 原始块回退','muted',True));self.destination=W.QLineEdit(str(self.history_root));self.destination.setReadOnly(True);self.destination.setToolTip(str(self.history_root));l.addWidget(label('历史结果汇总位置','muted'));l.addWidget(self.destination);self.dest_button=button('打开历史结果汇总文件夹',self.open_history_root);l.addWidget(self.dest_button);v.addWidget(f)
        f,l=card();l.addWidget(label('本次结果','section'));self.result_origin=label('尚无结果','muted',True);l.addWidget(self.result_origin);self.times=label('板端处理    —\n端到端        —','muted',True);l.addWidget(self.times);l.addWidget(label('板端含写入；端到端含传输、加载和全量校验，二者不可相加。','muted',True));self.export_button=button('导出结果副本',self.export_result);self.folder_button=button('打开结果文件夹',self.open_folder);l.addWidget(self.export_button);l.addWidget(self.folder_button);v.addWidget(f)
        f,l=card();l.addWidget(label('设计资源','section'));h=CONFIG['hardware'];self.resource_label=label(f"LUT      {int(h['lut']):,} / 53,200   ({h['lut']/53200:.2%})\nFF         {int(h['ff']):,} / 106,400   ({h['ff']/106400:.2%})\nBRAM   {h['bram36_equivalent']:g} / 140   ({h['bram36_equivalent']/140:.2%})",wrap=True);l.addWidget(self.resource_label);l.addWidget(label('当前设计实现报告\n这些数字不是实时利用率。','muted',True));v.addWidget(f);v.addStretch();scroll.setWidget(pane);self.dock.setWidget(scroll);self.addDockWidget(C.Qt.DockWidgetArea.RightDockWidgetArea,self.dock)
    def credentials(self):return Connection(self.host.text().strip(),self.user.text().strip(),self.password.text())
    def connection_display(self,text,ready=False):
        self.connection_status.setText(text);self.connection_status.setStyleSheet('color:#12835d;font-weight:600;' if ready else 'color:#758398;');self.version_status.setStyleSheet('color:#12835d;' if ready else '')
    def connection_changed(self):self.connected=False;self.connection_display('●  连接设置已更改');self.version_status.setText('连接设置已更改\n请重新检查连接');self.update_buttons()
    def log_line(self,text):self.log.appendPlainText(datetime.now().strftime('%H:%M:%S')+'  '+text)
    def update_buttons(self):
        for w in (self.import_button,self.history_button,self.decode_button,self.connect_button,self.host,self.user,self.password,self.group,self.destination,self.dest_button):w.setEnabled(not self.busy)
        for i,ch in enumerate(self.channel_checks):ch.setEnabled(not self.busy and self.action_mode=='compress' and i in self.source and len(self.source[i])>0)
        self.run_button.setText('开始解压' if self.action_mode=='decode' else '开始压缩');self.run_button.setEnabled(not self.busy and (self.pending_compressed is not None if self.action_mode=='decode' else not self.board_shutdown and bool(self.selected())));self.stop_button.setEnabled(self.busy and self.last_operation=='compress');self.shutdown_button.setEnabled(not self.busy and self.connected and not self.board_shutdown);self.export_button.setEnabled(not self.busy and self.folder is not None and self.report is not None);self.folder_button.setEnabled(self.folder is not None)
    def selected(self):return {i:self.source[i] for i,ch in enumerate(self.channel_checks) if ch.isChecked() and i in self.source and len(self.source[i])}
    def set_data(self,channels,provenance,restored=None,report=None,folder=None,history=False):
        self.action_mode="compress";self.pending_compressed=None;self.decode_result=None
        self.source=channels;self.restored=restored or {};self.provenance=provenance;self.report=report;self.folder=folder;self.history_mode=history
        for i,ch in enumerate(self.channel_checks):
            ch.blockSignals(True);ch.setChecked(i in channels and len(channels[i])>0);ch.blockSignals(False);self.channel_details[i].setText(f'{len(channels[i]):,} 点 · 16位整数' if i in channels else '未导入')
        self.rate.blockSignals(True);fs=provenance.get('sample_rate_hz');self.rate.setText(format(float(fs),'.12g') if fs else '');self.rate.blockSignals(False)
        if report is None:self.axis_mode.blockSignals(True);self.axis_mode.setCurrentIndex(0);self.axis_mode.blockSignals(False)
        self.source_title.setText(str(provenance.get('record') or provenance.get('name') or '采样数据'));self.source_title.setToolTip(str(provenance));self.view.setCurrentIndex(2 if restored else 0);self.update_metrics();self.redraw();self.fit();self.update_buttons()
    def selection_changed(self):
        # A report describes the exact selected source, never a changed channel subset.
        self.report=None;self.folder=None;self.restored={};self.history_mode=False;self.decode_result=None;self.update_metrics();self.redraw();self.update_buttons()
    def update_metrics(self):
        raw=sum(v.nbytes for v in self.selected().values());r=self.report
        self.metrics['raw'][0].setText(pretty(r['raw_sample_bytes'] if r else raw) if raw or r else '—');self.metrics['raw'][1].setText('选中通道的有效数据');self.metrics['raw'][0].setToolTip('每个采样值以16位整数保存，占2字节；统计的是送入压缩器的有效数据，不是CSV或NPZ文件的磁盘大小。KB = 1000字节，MB = 1000000字节。')
        self.metrics['compressed'][0].setText(pretty(r['compressed_bytes']) if r else '—');self.metrics['compressed'][1].setText('包含完整文件头与帧头')
        self.metrics['saving'][0].setText(f"{r['saving_pct']:.2f}%" if r else '—');self.metrics['saving'][1].setText('1 − 完整压缩体积 / 原始采样体积')
        self.metrics['verified'][0].setText('全部一致' if r else '待验证');self.metrics['verified'][0].setStyleSheet('color:#139c85;' if r else '');self.metrics['verified'][1].setText(f"{r['verification']['compared_samples']:,} 点 · CRC / 序号通过" if r else '全量逐点核对后显示结果')
        self.result_origin.setText(('历史结果 · '+r['completed_at'] if self.history_mode else '板卡实跑 · '+r['completed_at']) if r else '尚无已验证的结果')
        self.times.setText(f"板端处理    {r['board_process_ns']/1e6:,.2f} ms\n端到端        {r['end_to_end_ns']/1e9:,.2f} s" if r else '板端处理    —\n端到端        —')
        if self.action_mode=='decode':
            result=self.decode_result;self.metrics['compressed'][0].setText(pretty(result['compressed_bytes']) if result else '待解压');self.metrics['saving'][0].setText('—');self.metrics['saving'][1].setText('解压模式');self.metrics['verified'][0].setText('CRC 通过' if result else '待解压');self.metrics['verified'][0].setStyleSheet('color:#139c85;' if result else '');self.metrics['verified'][1].setText('CRC / 序号 / 总数通过；未与外部原值核对' if result else '点击蓝色按钮后开始完整解码');self.result_origin.setText('电脑解压 · 已保存还原数据' if result else '已选压缩文件 · 等待开始解压');self.times.setText('电脑离线解压\n不需要连接板卡')
    def effective_rate(self):
        if self.axis_mode.currentIndex()==0:return None
        try:value=float(self.rate.text())
        except ValueError:return None
        return value if math.isfinite(value) and 0<value<=1e8 else None
    def sync_axis_controls(self):
        timed=self.axis_mode.currentIndex()==1;self.rate_label.setVisible(timed);self.rate.setVisible(timed)
        valid=self.effective_rate()
        self.axis_hint.setText('按采样点序号显示。' if not timed else '按真实采样率换算为秒。' if valid else '请填写采样率；目前仍按采样点显示。');self.axis_hint.setStyleSheet('color:#758398;' if not timed or valid else 'color:#bd6b24;')
    def redraw(self,*args):
        self.sync_axis_controls();mode=self.view.currentIndex();rate=self.effective_rate()
        for ch,(original,rest) in enumerate(self.curves):
            visible=ch in self.selected();self.plots[ch].setVisible(True);self.plots[ch].setOpacity(1.);left=self.plots[ch].getAxis('left');left.setPen(pg.mkPen('#758398' if visible else '#cbd2dc'));left.setTextPen(pg.mkPen('#758398' if visible else '#cbd2dc'))
            a=self.source.get(ch,np.array([],dtype='<i2'));b=self.restored.get(ch);original.clear();rest.clear()
            if not visible:continue
            x=np.arange(len(a),dtype=np.float64)/rate if rate else None
            def put(curve,y):curve.setData(x=x,y=y) if x is not None else curve.setData(y=y)
            if mode in (0,2):put(original,a)
            if mode==1 and b is not None:put(original,b)
            if mode==2 and b is not None:put(rest,b)
            if mode==3 and b is not None:put(original,b.astype(np.int32)-a.astype(np.int32))
        if rate:self.plots[3].setLabel('bottom','时间',units='s')
        else:self.plots[3].setLabel('bottom','采样点序号',units='')
        self.plots[3].getAxis('bottom').enableAutoSIPrefix(False)
        self.wave_note.setText('尚无还原结果，先完成压缩或打开历史' if mode and not self.restored else '彩色：原始  /  虚线：还原' if mode==2 else '滚轮缩放 · 拖动平移')
        self.fit()
    def fit(self):
        if not self.selected():return
        # Linked views plus clip-to-view must not repeatedly auto-fit an already
        # clipped curve. Set the full source extent explicitly.
        mode=self.view.currentIndex();rate=self.effective_rate() or 1.;end=max(len(a) for a in self.selected().values())-1
        for ch,p in enumerate(self.plots):
            p.disableAutoRange();a=self.source.get(ch);b=self.restored.get(ch)
            y=b.astype(np.int32)-a.astype(np.int32) if mode==3 and b is not None and a is not None else b if mode==1 and b is not None else a
            if y is not None and len(y):
                lo=int(y.min());hi=int(y.max());p.setYRange(lo if lo!=hi else lo-1,hi if lo!=hi else hi+1,padding=.08)
        self.plots[0].setXRange(0,max(1,end)/rate,padding=.01)
    def start(self,kind,action):
        if self.busy:return
        self.last_operation=kind;self.last_error=None;self.busy=True;self.progress.setRange(0,0);self.phase.setText('正在准备');self.progress_text.setText('');self.update_buttons();self.worker=Worker(action,self);self.worker.event.connect(self.on_event);self.worker.okay.connect(self.on_success);self.worker.error.connect(self.on_error);self.worker.finished.connect(self.on_finished);self.worker.start()
    @C.Slot(str,object)
    def on_event(self,kind,row):
        if kind=='phase':self.phase.setText(row['text']);self.log_line(row['text'])
        elif kind=='progress':self.progress.setRange(0,10000);self.progress.setValue(round(row['processed']/max(1,row['total'])*10000));self.phase.setText('FPGA / ARM 正在处理');self.progress_text.setText(f"{row['processed']:,} / {row['total']:,} 点 · {pretty(row['output_bytes'])}")
        elif kind=='transfer':self.phase.setText(row['text']);self.progress.setRange(0,10000);self.progress.setValue(round(row['done']/max(1,row['total'])*10000));self.progress_text.setText(pretty(row['done'])+' / '+pretty(row['total']))
    @C.Slot(object)
    def on_success(self,result):
        kind=self.last_operation
        if kind=='connect':self.connected=True;self.board_shutdown=False;self.connection_display('●  板卡连接正常',True);self.version_status.setText('unified-v1 版本校验通过\n检查连接不会启动压缩');self.phase.setText('板卡已就绪');self.log_line('连接和正式版本文件核对通过。')
        elif kind=='load':self.set_data(result['source'],result['provenance']);self.phase.setText('数据已导入，可以开始压缩');self.log_line('导入成功，采样值未缩放或量化。')
        elif kind=='history':
            source,restored,report,check=result['data'];self.set_data(source,report['source'],restored,report,result['folder'],True);self.phase.setText('历史结果已重新全量校验');self.log_line('历史文件哈希与全部还原值通过校验。')
        elif kind=='compress':self.connected=True;self.board_shutdown=False;self.connection_display('●  板卡连接正常',True);self.set_data(result['source'],self.provenance,result['restored'],result['report'],result['folder']);self.phase.setText('完成 · 全量无损校验通过');self.log_line('压缩、还原、逐点与 CRC 校验完成，结果已保存。')
        elif kind=='decode':
            path=Path(result['path']);self.set_data(result['arrays'],dict(name=path.stem+' · 还原数据'),result['arrays'],folder=result['folder']);self.action_mode='decode';self.pending_compressed=path;self.decode_result=result;self.view.setCurrentIndex(1);self.update_metrics();self.phase.setText('解压完成 · 文件 CRC / 序号 / 总数通过');self.log_line('已保存还原 NPZ：'+result['folder']+'。可以点击“打开结果文件夹”查看。')
        elif kind=='shutdown':
            self.connected=False;self.board_shutdown=True;self.connection_display('●  关机指令已发送');self.version_status.setText('板卡正在正常关机\n再次使用需要重新上电');self.phase.setText('关机指令已发送，等待板卡系统关闭');self.log_line('已提交系统关机。电源灯可能仍亮；系统关闭后可断电。');self.shutdown_receipt=result
        elif kind=='export':self.phase.setText('结果副本已导出');self.log_line('导出并核对文件哈希：'+result['folder'])
        self.progress.setRange(0,100);self.progress.setValue(100)
    @C.Slot(str,bool)
    def on_error(self,text,cancelled):
        self.last_error=text;self.phase.setText('已停止 · 未发布未完成结果' if cancelled else '操作失败，请查看日志');self.log_line(text);self.progress.setRange(0,100);self.progress.setValue(0)
        if not cancelled:
            if self.last_operation in ('connect','compress','shutdown'):self.connected=False;self.connection_display('●  需要重新检查连接')
            # Nonmodal feedback keeps the worker lifecycle and automated recovery usable.
            self.phase.setToolTip(text)
    @C.Slot()
    def on_finished(self):
        w=self.worker;self.worker=None;self.busy=False;self.update_buttons()
        if w:w.deleteLater()
        if self.closing:C.QTimer.singleShot(0,self.close)
    def stop(self):
        if self.worker and self.last_operation=='compress':self.worker.cancel.set();self.stop_button.setEnabled(False);self.phase.setText('正在安全停止，请稍候');self.log_line('已请求停止；等待板卡复位确认，不强制释放活动缓冲。')
    def connect_board(self):
        credentials=self.credentials();self.start('connect',lambda notify,cancel:Backend(credentials,notify,cancel).connect())
    def request_shutdown(self):
        if self.busy or not self.connected or self.board_shutdown:return
        dialog=W.QMessageBox(self);dialog.setWindowTitle('板卡关机');dialog.setIcon(W.QMessageBox.Icon.Question);dialog.setText('将正常关闭板卡系统并断开连接。');dialog.setInformativeText('再次使用需要重新上电。确认关机吗？');yes=dialog.addButton('关机',W.QMessageBox.ButtonRole.AcceptRole);no=dialog.addButton('取消',W.QMessageBox.ButtonRole.RejectRole);dialog.setDefaultButton(no);dialog.exec()
        if dialog.clickedButton() is not yes:return
        credentials=self.credentials();self.start('shutdown',lambda notify,cancel:Backend(credentials,notify,cancel).shutdown())
    def load_path(self,path):self.start('load',lambda notify,cancel:dict(source=dataio.load(path),provenance=provenance(path)))
    def choose_input(self):
        path,_=W.QFileDialog.getOpenFileName(self,'导入采样数据',str(DEMO),'采样数据 (*.csv *.npz)')
        if path:self.load_path(path)
    def open_history(self,folder):self.start('history',lambda notify,cancel:dict(data=dataio.history(folder),folder=str(folder)))
    def choose_history(self):
        folder=W.QFileDialog.getExistingDirectory(self,'历史结果汇总：进入数据名文件夹，再选择一次历史结果',str(self.history_root))
        if folder:
            path=Path(folder)
            if not (path/'report.json').exists():
                folder=W.QFileDialog.getExistingDirectory(self,'进入并选择以“数据名＋日期＋时间”命名的结果文件夹',str(path))
                if not folder:return
            self.open_history(folder)
    def open_history_root(self):
        G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(self.history_root)))
    def run_primary(self):
        if self.action_mode=='decode':
            if not self.busy and self.pending_compressed is not None:self.decode_path(self.pending_compressed)
        else:self.run_compression()
    def run_compression(self):
        if self.busy or self.board_shutdown:return
        channels=self.selected();credentials=self.credentials();destination=str(self.history_root);group=2 if self.group.currentIndex()==0 else 1;info=dict(self.provenance,selected_channels=sorted(channels));self.report=None;self.restored={};self.folder=None;self.history_mode=False;self.update_metrics();self.redraw()
        if not destination:self.on_error('请选择结果保存位置。',False);return
        self.start('compress',lambda notify,cancel:Backend(credentials,notify,cancel).compress(channels,destination,group,info))
    def select_compressed(self,path):
        if self.busy:return
        # Selection has no decoding side effects; the blue button performs work.
        path=Path(path).resolve()
        if not path.is_file() or path.suffix.lower()!='.ucf':
            self.on_error('请选择已保存的 .ucf 压缩文件。',False);return
        self.set_data({},dict(name=path.name))
        self.action_mode='decode';self.pending_compressed=path;self.phase.setText('已选择压缩文件，请点击“开始解压”');self.log_line('已选择：'+str(path));self.update_metrics();self.update_buttons()
    def decode_path(self,path):
        root=self.history_root
        def action(notify,cancel):
            notify('phase',text='电脑正在完整解码并检查 CRC');return decode_to_result(path,root)
        self.start('decode',action)
    def choose_decode(self):
        path,_=W.QFileDialog.getOpenFileName(self,'选择历史结果中的 compressed.ucf，选中后点击“开始解压”',str(self.history_root),'统一压缩文件 (*.ucf)')
        if path:self.select_compressed(path)
    def export_to(self,parent):
        source=Path(self.folder)
        def action(notify,cancel):
            dataio.history(source);target=Path(parent)/(source.name+'-导出-'+uuid.uuid4().hex[:6]);shutil.copytree(source,target)
            if any(dataio.digest(source/n)!=dataio.digest(target/n) for n in ('input.npz','compressed.ucf','restored.npz','report.json')):raise ValueError('导出文件哈希不一致。')
            return dict(folder=str(target))
        self.start('export',action)
    def export_result(self):
        parent=W.QFileDialog.getExistingDirectory(self,'选择导出副本的父文件夹')
        if parent:self.export_to(parent)
    def open_folder(self):
        if self.folder:G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(Path(self.folder).resolve())))
    def closeEvent(self,event):
        if self.busy:
            self.closing=True
            if self.last_operation=='compress':self.stop()
            else:self.phase.setText('等待当前操作结束后关闭')
            event.ignore()
        else:event.accept()

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--history',help='打开并重新完整校验已有结果文件夹');args=parser.parse_args()
    app=W.QApplication(sys.argv[:1]);app.setApplicationName('多通道无损压缩工作台');app.setOrganizationName('FPGACompression');window=Window();window.show()
    if args.history:C.QTimer.singleShot(0,lambda:window.open_history(args.history))
    return app.exec()
if __name__=='__main__':sys.exit(main())
