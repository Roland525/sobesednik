import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from desktop import widgets


class InteractionWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def window(self, widget):
        self.windows.append(widget)
        widget.show()
        self.app.processEvents()
        return widget

    def banner(self):
        self.assertTrue(hasattr(widgets, 'NoticeBanner'), 'Dismissible notice widget is missing')
        return self.window(widgets.NoticeBanner())

    def test_wheel_scrolls_page_without_changing_focused_or_unfocused_controls(self):
        for name in ('NoWheelComboBox', 'NoWheelSpinBox', 'NoWheelDoubleSpinBox'):
            with self.subTest(control=name):
                self.assertTrue(hasattr(widgets, name), f'{name} is missing')
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                content = QWidget()
                layout = QVBoxLayout(content)
                control = getattr(widgets, name)()
                if name == 'NoWheelComboBox':
                    control.addItems(['First', 'Second', 'Third'])
                    control.setCurrentIndex(1)
                    read_value = control.currentIndex
                else:
                    control.setRange(0, 100)
                    control.setValue(50)
                    read_value = control.value
                layout.addWidget(control)
                filler = QLabel('Long settings page')
                filler.setFixedHeight(1600)
                layout.addWidget(filler)
                scroll.setWidget(content)
                scroll.resize(400, 250)
                self.window(scroll)
                for focused in (False, True):
                    control.setFocus() if focused else control.clearFocus()
                    scroll.verticalScrollBar().setValue(0)
                    before = read_value()
                    position = control.rect().center()
                    event = QWheelEvent(QPointF(position), QPointF(control.mapToGlobal(position)),
                                        QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                                        Qt.ScrollUpdate, False)
                    QApplication.sendEvent(control, event)
                    self.app.processEvents()
                    self.assertEqual(read_value(), before)
                    self.assertGreater(scroll.verticalScrollBar().value(), 0)
                control.setFocus()
                QTest.keyClick(control, Qt.Key_Up)
                self.assertNotEqual(read_value(), before, 'Keyboard adjustments must stay available')

    def test_notice_can_be_closed_and_automatically_expires(self):
        with patch.dict(os.environ, SOBESEDNIK_REDUCED_MOTION='1'):
            banner = self.banner()
            banner.show_message('Saved', timeout_ms=50)
            self.assertTrue(banner.isVisible())
            self.assertEqual(banner.message.text(), 'Saved')
            QTest.qWait(90)
            self.assertFalse(banner.isVisible())
            banner.show_message('Again', timeout_ms=0)
            QTest.mouseClick(banner.close_button, Qt.LeftButton)
            self.assertFalse(banner.isVisible())

    def test_repeated_notice_restarts_expiry_and_can_reopen_after_dismissal(self):
        with patch.dict(os.environ, SOBESEDNIK_REDUCED_MOTION='1'):
            banner = self.banner()
            banner.show_message('Old', timeout_ms=100)
            QTest.qWait(60)
            banner.show_message('New', timeout_ms=150)
            QTest.qWait(80)
            self.assertTrue(banner.isVisible())
            self.assertEqual(banner.message.text(), 'New')
            QTest.qWait(110)
            self.assertFalse(banner.isVisible())
            banner.show_message('Persistent', timeout_ms=0)
            QTest.qWait(220)
            self.assertTrue(banner.isVisible())

    def test_notice_reopening_during_exit_does_not_hide_new_message(self):
        with patch.object(widgets, 'animations_enabled', return_value=True, create=True):
            banner = self.banner()
            banner.show_message('First', timeout_ms=0)
            QTest.qWait(210)
            banner.hide_message()
            QTest.qWait(40)
            banner.show_message('Ready', timeout_ms=0)
            QTest.qWait(220)
            self.assertTrue(banner.isVisible())
            self.assertEqual(banner.message.text(), 'Ready')
            self.assertAlmostEqual(banner.graphicsEffect().opacity(), 1.0)

    def test_notice_animates_between_real_opacity_values(self):
        with patch.object(widgets, 'animations_enabled', return_value=True, create=True):
            banner = self.banner()
            banner.hide()
            banner.show_message('Working', timeout_ms=0)
            self.assertEqual(banner._animation.state(), QAbstractAnimation.Running)
            self.assertLessEqual(banner._animation.duration(), 200)
            QTest.qWait(45)
            self.assertGreater(banner.graphicsEffect().opacity(), 0.0)
            self.assertLess(banner.graphicsEffect().opacity(), 1.0)
            QTest.qWait(180)
            self.assertAlmostEqual(banner.graphicsEffect().opacity(), 1.0)
            banner.hide_message()
            QTest.qWait(210)
            self.assertFalse(banner.isVisible())

    def test_disclosure_interruptions_settle_without_clipping_expanded_content(self):
        with patch.object(widgets, 'animations_enabled', return_value=True, create=True):
            disclosure = widgets.Disclosure('Details')
            content = QLabel('Long detail')
            content.setFixedHeight(180)
            disclosure.body.addWidget(content)
            self.window(disclosure)
            QTest.mouseClick(disclosure.toggle, Qt.LeftButton)
            self.assertEqual(disclosure._animation.state(), QAbstractAnimation.Running)
            self.assertLessEqual(disclosure._animation.duration(), 200)
            QTest.qWait(45)
            self.assertGreater(disclosure.content.maximumHeight(), 0)
            self.assertLess(disclosure.content.maximumHeight(), 180)
            QTest.mouseClick(disclosure.toggle, Qt.LeftButton)
            QTest.qWait(30)
            QTest.mouseClick(disclosure.toggle, Qt.LeftButton)
            QTest.qWait(240)
            self.assertTrue(disclosure.isExpanded())
            self.assertTrue(disclosure.content.isVisible())
            self.assertGreater(disclosure.content.maximumHeight(), 1000000)
            self.assertGreaterEqual(disclosure.content.height(), content.height())
            QTest.mouseClick(disclosure.toggle, Qt.LeftButton)
            QTest.qWait(240)
            self.assertFalse(disclosure.content.isVisible())

    def test_reduced_motion_and_initial_disclosure_render_are_immediate(self):
        with patch.dict(os.environ, SOBESEDNIK_REDUCED_MOTION='1'):
            self.assertTrue(hasattr(widgets, 'animations_enabled'), 'Motion preference reader is missing')
            self.assertFalse(widgets.animations_enabled())
            disclosure = widgets.Disclosure('Details', expanded=True)
            disclosure.body.addWidget(QLabel('Detail'))
            self.window(disclosure)
            self.assertTrue(disclosure.content.isVisible())
            disclosure.setExpanded(False)
            self.assertFalse(disclosure.content.isVisible())
            disclosure.setExpanded(True)
            self.assertTrue(disclosure.content.isVisible())
            banner = self.banner()
            banner.show_message('Saved', timeout_ms=0)
            self.assertTrue(banner.isVisible())
            banner.hide_message()
            self.assertFalse(banner.isVisible())


if __name__ == '__main__':
    unittest.main()
