import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QVBoxLayout,QWidget
from PySide6.QtGui import QPalette
from desktop.widgets import button
from desktop.style import STYLE

class ButtonStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.window=QWidget();self.window.setStyleSheet(STYLE)
        layout=QVBoxLayout(self.window)
        self.primary=button('Запустить бота',lambda:None,True)
        self.secondary=button('Перезапустить',lambda:None)
        layout.addWidget(self.primary);layout.addWidget(self.secondary)
        self.window.resize(320,150);self.window.show();self.app.processEvents()

    def tearDown(self):self.window.close();self.window.deleteLater();self.app.processEvents()

    def surface(self,widget):
        self.app.processEvents()
        return widget.grab().toImage().pixelColor(6,widget.height()//2)

    def test_primary_disabled_is_neutral_and_pressed_has_its_own_color(self):
        normal=self.surface(self.primary)
        self.primary.setDown(True);pressed=self.surface(self.primary)
        self.assertNotEqual(normal.name(),pressed.name(),'Pressing must visibly change the primary button')
        self.primary.setDown(False);self.primary.setEnabled(False)
        disabled=self.surface(self.primary)
        self.assertLess(disabled.saturation(),90,'Disabled buttons must not look like colored actions')
        self.assertGreater(normal.blue(),disabled.blue()+70)

    def test_disabled_button_cursor_is_not_an_action_pointer(self):
        self.primary.setEnabled(False)
        self.assertEqual(self.primary.cursor().shape(),Qt.ArrowCursor)
        self.primary.setEnabled(True)
        self.assertEqual(self.primary.cursor().shape(),Qt.PointingHandCursor)

    def test_button_text_is_readable_in_enabled_and_disabled_states(self):
        def luminance(color):
            values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
                    for v in (color.redF(),color.greenF(),color.blueF())]
            return sum(a*b for a,b in zip(values,(.2126,.7152,.0722)))
        for control in (self.primary,self.secondary):
            for enabled in (True,False):
                control.setEnabled(enabled)
                background=self.surface(control)
                group=QPalette.Active if enabled else QPalette.Disabled
                foreground=control.palette().color(group,QPalette.ButtonText)
                light,dark=sorted((luminance(background),luminance(foreground)),reverse=True)
                with self.subTest(control=control.text(),enabled=enabled):
                    self.assertGreaterEqual((light+.05)/(dark+.05),4.5)

    def test_keyboard_focus_has_visible_outline(self):
        self.primary.clearFocus();self.secondary.setFocus();self.app.processEvents()
        normal=self.primary.grab().toImage().pixelColor(1,self.primary.height()//2)
        self.primary.setFocus(Qt.TabFocusReason);self.app.processEvents()
        focused=self.primary.grab().toImage().pixelColor(1,self.primary.height()//2)
        self.assertTrue(self.primary.hasFocus())
        self.assertNotEqual(normal.name(),focused.name())

    def test_hover_highlights_available_buttons_without_reenabling_disabled_color(self):
        for control in (self.primary,self.secondary):
            QTest.mouseMove(self.window,QPoint(2,self.window.height()-2))
            normal=self.surface(control)
            QTest.mouseMove(control,control.rect().center())
            hover=self.surface(control)
            self.assertNotEqual(normal.name(),hover.name())
            control.setEnabled(False)
            disabled_hover=self.surface(control)
            QTest.mouseMove(self.window,QPoint(2,self.window.height()-2))
            self.assertEqual(disabled_hover.name(),self.surface(control).name())
            control.setEnabled(True)

