import unittest
from unittest.mock import patch


class DesktopPackageTests(unittest.TestCase):
    def test_public_allowlist_covers_application_and_build_sources(self):
        from tools.build_release import release_files
        files={p.as_posix() for p in release_files()}
        self.assertIn('desktop_entry.py',files)
        self.assertIn('desktop/app.py',files)
        self.assertIn('requirements-desktop.txt',files)
        self.assertIn('tools/build_desktop.py',files)
        self.assertIn('packaging/installer.iss',files)
        self.assertIn('tests_desktop/test_window.py',files)
        self.assertNotIn('config.json',files)

    def test_build_does_not_resolve_qt_dlls_from_unrelated_path(self):
        from tools.build_desktop import build_environment
        with patch.dict('os.environ',{'PATH':'C:/unrelated/poppler/Library/bin','QT_PLUGIN_PATH':'C:/unrelated/plugins'}):
            env=build_environment()
        self.assertNotIn('poppler',env['PATH'])
        self.assertNotIn('QT_PLUGIN_PATH',env)
