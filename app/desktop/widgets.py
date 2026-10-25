import os
import sys

from PySide6.QtCore import Qt, QEasingCurve, QPointF, QPropertyAnimation, QTimer
from PySide6.QtGui import QColor,QPainter,QPen,QWheelEvent
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QComboBox, QDoubleSpinBox, QFrame,
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QSpinBox, QStyle, QToolButton, QVBoxLayout, QWidget)


def animations_enabled():
    """Read the user's motion preference without changing Windows settings."""
    if os.environ.get('SOBESEDNIK_REDUCED_MOTION', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        return False
    if sys.platform == 'win32':
        try:
            import ctypes
            enabled = ctypes.c_int()
            # SPI_GETCLIENTAREAANIMATION is a read-only query (not SPI_SET...).
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        except (AttributeError, OSError):
            pass
    app = QApplication.instance()
    return bool(app and app.style().styleHint(QStyle.SH_Widget_Animate))


def _scroll_enclosing_area(widget, event):
    """Route wheel input explicitly; Qt does not always propagate ignored wheels."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            viewport = parent.viewport()
            forwarded = QWheelEvent(
                QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                event.buttons(), event.modifiers(), event.phase(), event.inverted(),
                event.source(), event.pointingDevice())
            QApplication.sendEvent(viewport, forwarded)
            if forwarded.isAccepted():
                event.accept()
                return
        parent = parent.parentWidget()
    event.ignore()


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        # The popup has its own item view and keeps normal wheel navigation.
        _scroll_enclosing_area(self, event)


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        _scroll_enclosing_area(self, event)


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event):
        _scroll_enclosing_area(self, event)


class NoticeBanner(QFrame):
    """An inline notice whose latest message owns its timer and transition."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('notice')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)
        self.message = label('')
        self.close_button = QPushButton('Закрыть')
        self.close_button.setAccessibleName('Закрыть уведомление')
        self.close_button.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.close_button, 0, Qt.AlignTop)
        self.close_button.clicked.connect(self.hide_message)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_message)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._animation = QPropertyAnimation(self._opacity, b'opacity', self)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._finish_transition)
        self._dismissing = False
        self.hide()

    def show_message(self, text, timeout_ms=6000):
        was_hidden = self.isHidden()
        self._timer.stop()
        self._animation.stop()
        self._dismissing = False
        self.message.setText(str(text))
        self.show()
        if self.isVisible() and animations_enabled():
            if was_hidden:
                self._opacity.setOpacity(0.0)
            self._animate_opacity(1.0, 170)
        else:
            self._opacity.setOpacity(1.0)
        if timeout_ms > 0:
            self._timer.start(int(timeout_ms))

    def hide_message(self):
        self._timer.stop()
        self._animation.stop()
        self._dismissing = True
        if self.isVisible() and animations_enabled():
            self._animate_opacity(0.0, 150)
        else:
            self._finish_transition()

    def _animate_opacity(self, target, duration):
        self._animation.setDuration(duration)
        self._animation.setStartValue(self._opacity.opacity())
        self._animation.setEndValue(target)
        self._animation.start()

    def _finish_transition(self):
        if self._dismissing:
            self.hide()
            self._opacity.setOpacity(1.0)
            self._dismissing = False


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
        self._animation=QPropertyAnimation(self.content,b'maximumHeight',self)
        self._animation.setDuration(170);self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.finished.connect(self._finish_expansion)
        self._expanded=None
        self.toggle.toggled.connect(self.setExpanded);self.setExpanded(expanded)

    def isExpanded(self):return self.toggle.isChecked()

    def setExpanded(self,expanded,animated=True):
        expanded=bool(expanded)
        changed=expanded != self._expanded
        self._expanded=expanded
        self.toggle.blockSignals(True);self.toggle.setChecked(expanded);self.toggle.blockSignals(False)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        if not changed:
            return
        current=self.content.height() if self.content.isVisible() else 0
        self._animation.stop()
        if animated and self.isVisible() and animations_enabled():
            target=max(0,self.body.sizeHint().height()) if expanded else 0
            self.content.setMaximumHeight(current)
            self.content.setVisible(True)
            self._animation.setStartValue(current);self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._finish_expansion()

    def _finish_expansion(self):
        self.content.setMaximumHeight(16777215 if self._expanded else 0)
        self.content.setVisible(self._expanded)


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
