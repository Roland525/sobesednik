STYLE='''
QWidget { background:#10151e; color:#e8edf5; font-family:"Segoe UI"; font-size:14px; }
QLabel, QWidget#row { background:transparent; }
QMainWindow { background:#10151e; }
QFrame#sidebar { background:#151c28; border-right:1px solid #273142; }
QLabel#brand { font-size:24px; font-weight:700; color:#eafff8; }
QLabel#eyebrow { color:#86a09a; font-size:11px; font-weight:700; }
QLabel#title { font-size:29px; font-weight:700; }
QLabel#heroTitle { font-size:26px; font-weight:700; background:transparent; }
QLabel#subtitle { color:#9aa9bd; background:transparent; }
QLabel#sectionTitle { font-size:17px; font-weight:600; background:transparent; }
QLabel#metric { font-size:27px; font-weight:600; background:transparent; }
QLabel#status { color:#c6d1e0; background:#202b3b; border:1px solid #495b73; border-radius:10px; padding:7px 13px; }
QLabel#status[state="ready"] { color:#a5f1d2; background:#1b302c; border-color:#487d65; }
QLabel#status[state="starting"] { color:#ffe2a4; background:#392e1c; border-color:#8b713d; }
QLabel#status[state="error"] { color:#ffc0c9; background:#3d222d; border-color:#a26070; }
QLabel#notice { background:#202c3d; color:#c1d2e9; border:1px solid #34475e; padding:12px; border-radius:8px; }
QFrame#notice { background:#202c3d; color:#c1d2e9; border:1px solid #34475e; border-radius:8px; }
QFrame#notice QLabel { background:transparent; border:none; }
QFrame#card { background:#192230; border:1px solid #2b374a; border-radius:13px; }
QFrame#hero { background:#172435; border:1px solid #3b5270; border-radius:15px; }
QLabel#actionHint { color:#c5d4e8; background:transparent; }
QFrame#card QLabel, QFrame#hero QLabel { background:transparent; }
QListWidget#navigation { background:transparent; border:none; outline:none; padding:4px; }
QListWidget#navigation::item { padding:13px 15px; border-radius:9px; margin:3px 0; color:#aab7cb; }
QListWidget#navigation::item:selected { background:#243c59; color:#d5eaff; border:1px solid #567ca4; }
QListWidget#navigation::item:hover { background:#202d3d; }
QPushButton { background:#293c55; color:#f1f6ff; border:2px solid #647e9e; border-radius:8px; padding:9px 16px; min-height:20px; font-weight:600; }
QPushButton:enabled:hover { background:#3b5473; border-color:#afd4ff; }
QPushButton:enabled:pressed { background:#182b42; border-color:#77b9ff; }
QPushButton#primary { background:#77b9ff; color:#081e38; border-color:#77b9ff; }
QPushButton#primary:enabled:hover { background:#a2d0ff; border-color:#c6e2ff; }
QPushButton#primary:enabled:pressed { background:#4c96e4; border-color:#b8dcff; }
QPushButton#danger { color:#ffd1d7; background:#462833; border-color:#b06c7f; }
QPushButton#danger:enabled:hover { background:#673445; border-color:#ffa6ba; }
QPushButton#danger:enabled:pressed { background:#351c27; border-color:#ffbac9; }
QPushButton:enabled:focus, QPushButton#primary:enabled:focus, QPushButton#danger:enabled:focus { border-color:#ffd27a; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled { background:#222831; color:#8f9bad; border:2px solid #3a4556; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background:#111a27; border:1px solid #3a4a61; border-radius:7px; padding:8px; selection-background-color:#345a86; selection-color:#ffffff; }
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus { border-color:#8fc5ff; }
QComboBox::drop-down { border:none; width:25px; }
QComboBox QAbstractItemView { background:#1b2739; selection-background-color:#345a86; selection-color:#ffffff; }
QPlainTextEdit { font-size:14px; }
QTableWidget { background:#131d2b; alternate-background-color:#192638; border:1px solid #2e3f56; border-radius:8px; gridline-color:#2c3b50; selection-background-color:#345a86; selection-color:#ffffff; }
QHeaderView::section { background:#202d40; color:#aebdd1; padding:10px; border:none; font-weight:600; }
QProgressBar { background:#101925; border:none; border-radius:4px; height:8px; text-align:center; }
QProgressBar::chunk { background:#77b9ff; border-radius:4px; }
QScrollArea { border:none; }
QScrollBar:vertical { background:#111a27; width:8px; }
QScrollBar::handle:vertical { background:#42556d; border-radius:4px; min-height:30px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QCheckBox { spacing:10px; background:transparent; }
QCheckBox::indicator { width:18px; height:18px; border:1px solid #5c728a; border-radius:4px; background:#131e2e; }
QCheckBox::indicator:checked { background:#77b9ff; border-color:#b3d9ff; }
QToolTip { background:#26354b; color:#e6f5ed; border:1px solid #617991; padding:5px; }
QToolButton#disclosure { background:#192230; border:1px solid #2b374a; border-radius:8px; padding:10px 15px; color:#b9c8dc; font-weight:600; }
QToolButton#disclosure:enabled:hover { background:#2c405b; border-color:#93b9e5; }
QToolButton#disclosure:enabled:focus { border-color:#ffd27a; }
QToolButton#disclosure:enabled:pressed { background:#15263c; }
QTabWidget::pane { border:none; }
QTabBar::tab { background:#192230; color:#9aa9bd; padding:12px 24px; margin:0 7px 10px 0; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#d5eaff; background:#243c59; border-bottom:2px solid #77b9ff; }
'''
