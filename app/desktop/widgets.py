from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import QFrame,QVBoxLayout,QLabel,QPushButton,QHBoxLayout,QWidget,QProgressBar,QToolButton


def label(text, name=None, wrap=True):
    item=QLabel(text)
    if name: item.setObjectName(name)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.PlainText)
    return item


def button(text, callback, primary=False, danger=False):
    item=QPushButton(text); item.setCursor(Qt.PointingHandCursor)
    if primary or danger: item.setObjectName('primary' if primary else 'danger')
    item.clicked.connect(callback)
    return item


def card(title=None, hero=False):
    item=QFrame(); item.setObjectName('hero' if hero else 'card')
    layout=QVBoxLayout(item); layout.setContentsMargins(23,20,23,20); layout.setSpacing(13)
    if title: layout.addWidget(label(title,'sectionTitle'))
    return item,layout


def row(*items):
    widget=QWidget(); widget.setObjectName('row'); layout=QHBoxLayout(widget); layout.setContentsMargins(0,0,0,0); layout.setSpacing(10)
    for item in items:
        if item is None: layout.addStretch()
        else: layout.addWidget(item)
    return widget


class Disclosure(QWidget):
    def __init__(self,title,expanded=False):
        super().__init__()
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
        self.toggle=QToolButton();self.toggle.setObjectName('disclosure');self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon);self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.content=QWidget();self.body=QVBoxLayout(self.content);self.body.setContentsMargins(0,0,0,0);self.body.setSpacing(12)
        layout.addWidget(self.toggle);layout.addWidget(self.content)
        self.toggle.toggled.connect(self.setExpanded);self.setExpanded(expanded)

    def isExpanded(self):return self.toggle.isChecked()

    def setExpanded(self,expanded):
        self.toggle.blockSignals(True);self.toggle.setChecked(expanded);self.toggle.blockSignals(False)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow);self.content.setVisible(expanded)


class WaveMark(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent); self.setFixedSize(54,54)
    def paintEvent(self,event):
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen); painter.setBrush(QColor('#284e42')); painter.drawRoundedRect(0,0,54,54,15,15)
        painter.setPen(QPen(QColor('#aaffd8'),4,Qt.SolidLine,Qt.RoundCap))
        for x,h in [(15,10),(23,23),(31,33),(39,16)]: painter.drawLine(x,int(27-h/2),x,int(27+h/2))


class MetricCard(QFrame):
    def __init__(self,title):
        super().__init__(); self.setObjectName('card')
        layout=QVBoxLayout(self); layout.setContentsMargins(18,16,18,16); layout.setSpacing(8)
        layout.addWidget(label(title,'subtitle'))
        self.value=label('—','metric'); layout.addWidget(self.value)
        self.detail=label('Ожидаем измерение','subtitle'); layout.addWidget(self.detail)
        self.bar=QProgressBar(); self.bar.setRange(0,100); self.bar.setValue(0); self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5); layout.addWidget(self.bar)
    def update_value(self,value,detail,percent=None):
        self.value.setText(value); self.detail.setText(detail)
        self.bar.setValue(int(percent or 0))
