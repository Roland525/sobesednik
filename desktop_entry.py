if __name__=='__main__':
    try:
        from desktop.app import main
        raise SystemExit(main())
    except Exception:
        import os,sys,traceback
        from pathlib import Path
        from desktop.storage import redact
        folder=Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Sobesednik'
        if '--data-dir' in sys.argv:
            folder=Path(sys.argv[sys.argv.index('--data-dir')+1])
        folder.mkdir(parents=True,exist_ok=True)
        report=folder/'desktop-startup-error.log'
        report.write_text(redact(traceback.format_exc()),encoding='utf-8')
        if '--capture-dir' not in sys.argv:
            from PySide6.QtWidgets import QApplication,QMessageBox
            app=QApplication.instance() or QApplication([])
            QMessageBox.critical(None,'Собеседник','Не удалось открыть панель. Подробности сохранены в '+str(report))
        raise SystemExit(1)
