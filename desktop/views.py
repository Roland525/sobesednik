from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QComboBox,
    QDoubleSpinBox,QSpinBox,QCheckBox,QPlainTextEdit,QProgressBar,QTableWidget,QHeaderView,
    QAbstractItemView,QScrollArea)
from .widgets import label,button,card,row,MetricCard


def page(title,subtitle):
    outer=QWidget(); layout=QVBoxLayout(outer); layout.setContentsMargins(0,0,0,0); layout.setSpacing(16)
    layout.addWidget(label(title,'title')); layout.addWidget(label(subtitle,'subtitle'))
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    inner=QWidget(); body=QVBoxLayout(inner); body.setContentsMargins(0,5,10,8); body.setSpacing(16)
    scroll.setWidget(inner); layout.addWidget(scroll,1)
    return outer,body


class Views:
    def overview_view(self):
        page_widget,body=page('Всё под рукой','Голосовой собеседник в Discord. Модели и данные остаются на вашем компьютере.')
        hero,layout=card(hero=True)
        layout.addWidget(label('ЛОКАЛЬНЫЙ ИИ · БЕЗ ОПЛАТЫ API','eyebrow'))
        self.hero_title=label('Начнём с первой настройки','heroTitle'); layout.addWidget(self.hero_title)
        self.hero_detail=label('Установим модели, подключим Discord и выберем голосовой канал.','subtitle'); layout.addWidget(self.hero_detail)
        self.start_button=button('Запустить бота',self.start_bot,True)
        self.stop_button=button('Остановить',self.stop_bot)
        self.restart_button=button('Перезапустить',self.restart_bot)
        self.hush_button=button('Остановить речь',lambda:self.command('mute'))
        layout.addWidget(row(self.start_button,self.stop_button,self.restart_button,self.hush_button,None)); body.addWidget(hero)
        metrics=QHBoxLayout(); metrics.setSpacing(12)
        self.cpu_card=MetricCard('ПРОЦЕССОР'); self.ram_card=MetricCard('ПАМЯТЬ')
        self.gpu_card=MetricCard('ВИДЕОКАРТА'); self.latency_card=MetricCard('ДО НАЧАЛА ОТВЕТА')
        for item in [self.cpu_card,self.ram_card,self.gpu_card,self.latency_card]: metrics.addWidget(item)
        body.addLayout(metrics)
        setup,layout=card('Первая настройка и модели')
        layout.addWidget(label('Мастер скачает отдельный Python, зависимости, Ollama и локальные модели. Нужно около 20 ГБ свободного места. Первый запуск займёт время: объём загрузки — несколько гигабайт.','subtitle'))
        self.stt_choice=QComboBox(); self.stt_choice.addItem('Точное распознавание · large-v3-turbo','large-v3-turbo'); self.stt_choice.addItem('Экономное распознавание · small','small')
        self.install_button=button('Установить компоненты',self.install_components,True)
        self.cancel_install=button('Отменить',self.cancel_operation); self.cancel_install.setEnabled(False)
        layout.addWidget(row(self.stt_choice,self.install_button,self.cancel_install))
        self.install_stage=label('Компоненты ещё не установлены','subtitle'); layout.addWidget(self.install_stage)
        self.install_progress=QProgressBar(); self.install_progress.setRange(0,100); self.install_progress.setValue(0)
        layout.addWidget(self.install_progress)
        self.install_log=QPlainTextEdit(); self.install_log.setReadOnly(True); self.install_log.setMaximumBlockCount(400); self.install_log.setMaximumHeight(120)
        self.install_log.setPlaceholderText('Здесь появятся ход установки и подробности ошибок.'); layout.addWidget(self.install_log)
        body.addWidget(setup)
        folder,layout=card('Папка данных')
        self.folder_label=label(str(self.profile.root),'subtitle'); self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.folder_label); layout.addWidget(row(button('Открыть папку',self.open_folder),button('Выбрать другую папку',self.choose_root),None))
        layout.addWidget(label('Рекомендуется NVIDIA с 8 ГБ видеопамяти и 32 ГБ RAM. Работа на CPU заметно медленнее.','subtitle'))
        body.addWidget(folder); body.addStretch(); return page_widget

    def discord_view(self):
        page_widget,body=page('Подключение к Discord','Один бот-аккаунт. Без пользовательских аккаунтов и без облачной обработки голоса.')
        block,layout=card('1. Создайте бота и скопируйте токен')
        layout.addWidget(label('Откройте Developer Portal → New Application → Bot → Reset Token. Скопируйте Bot Token. В Discord нельзя выдать токен по общей прямой ссылке — он создаётся в вашем приложении.','subtitle'))
        layout.addWidget(row(button('Открыть Developer Portal',self.open_portal),button('Подробная инструкция',self.open_instructions),None))
        self.token_input=QLineEdit(); self.token_input.setEchoMode(QLineEdit.Password); self.token_input.setPlaceholderText('Вставьте Bot Token — ввод скрыт')
        self.token_input.setObjectName('discordToken'); layout.addWidget(self.token_input)
        self.token_status=label('Токен ещё не сохранён','subtitle'); layout.addWidget(self.token_status)
        layout.addWidget(row(button('Проверить и сохранить',self.check_token,True),button('Пригласить бота на сервер',self.invite_bot),None))
        layout.addWidget(label('Токен защищён Windows для вашей учётной записи. Он не отправляется модели и не включается в журнал.','subtitle')); body.addWidget(block)
        block,layout=card('2. Запустите бота и выберите канал')
        layout.addWidget(label('После запуска появятся серверы и голосовые каналы, к которым бот имеет доступ. Для подключения нужны права «Просматривать канал», «Подключаться», «Говорить».','subtitle'))
        self.channel_box=QComboBox(); self.channel_box.setMinimumWidth(270)
        self.join_button=button('Присоединиться',self.join_channel,True)
        self.leave_button=button('Выйти из канала',lambda:self.command('leave'))
        layout.addWidget(row(self.channel_box,self.join_button,self.leave_button,None))
        self.channel_status=label('Запустите бота на вкладке «Обзор».','subtitle'); layout.addWidget(self.channel_status)
        self.remember_channel=QCheckBox('Подключаться к выбранному каналу при запуске'); self.remember_channel.setChecked(True); layout.addWidget(self.remember_channel)
        layout.addWidget(label('Server Members, Presence и Message Content intents для обычных slash-команд не требуются. Полные права администратора боту не нужны.','subtitle'))
        body.addWidget(block); body.addStretch(); return page_widget

    def settings_view(self):
        page_widget,body=page('Голос и ответы','Настройте звучание и поведение. Изменения параметров применяются после перезапуска бота.')
        block,layout=card('Голос')
        form=QFormLayout(); form.setSpacing(15)
        self.wake_input=QLineEdit(); self.wake_input.setMaxLength(50); form.addRow('Имя обращения',self.wake_input)
        self.voice_choice=QComboBox(); self.voice_choice.addItems(['M1','M2','M3','M4','M5']); form.addRow('Голос Supertonic',self.voice_choice)
        self.speed=QDoubleSpinBox(); self.speed.setRange(.7,1.5); self.speed.setSingleStep(.02); self.speed.setDecimals(2); form.addRow('Темп речи · 1,00 — обычный',self.speed)
        self.volume=QDoubleSpinBox(); self.volume.setRange(.1,2); self.volume.setSingleStep(.05); self.volume.setDecimals(2); form.addRow('Громкость',self.volume)
        layout.addLayout(form); body.addWidget(block)
        block,layout=card('Поведение и контекст')
        form=QFormLayout(); form.setSpacing(15)
        self.mode_choice=QComboBox(); self.mode_choice.addItem('Только обращения по имени','strict'); self.mode_choice.addItem('Обращения и очень редкие дополнения','rare'); form.addRow('Когда отвечать',self.mode_choice)
        self.length=QSpinBox(); self.length.setRange(80,2000); self.length.setSingleStep(80); form.addRow('Максимум символов ответа',self.length)
        self.history=QSpinBox(); self.history.setRange(1,30); form.addRow('Хранить пар реплик в контексте',self.history)
        self.reasoning=QCheckBox('Дополнительное обдумывание · может увеличить задержку'); form.addRow(self.reasoning)
        self.dota=QCheckBox('Разрешить чтение официальных патчноутов Valve'); form.addRow(self.dota)
        layout.addLayout(form); layout.addWidget(label('Простые вопросы остаются краткими. Большое объяснение можно попросить словами «подробно» или «по шагам». Команда «заткнись» останавливает речь; новое обращение по имени снова разрешает ответ.','subtitle'))
        body.addWidget(block)
        body.addWidget(row(button('Сохранить настройки',self.save_settings,True),button('Сохранить и перезапустить',lambda:self.save_settings(True)),None)); body.addStretch(); return page_widget

    def persona_view(self):
        page_widget,body=page('Персона и разговор','Характер бота и текущая история — отдельные настройки. Постоянная память находится в следующем разделе.')
        block,layout=card('Как Собеседник общается')
        layout.addWidget(label('Опишите характер, манеру речи и предпочтения. При сохранении создаётся резервная копия. Новая персона используется в следующих вопросах.','subtitle'))
        self.persona_edit=QPlainTextEdit(); self.persona_edit.setMinimumHeight(200); layout.addWidget(self.persona_edit)
        layout.addWidget(row(button('Сохранить персону',self.save_persona,True),None)); body.addWidget(block)
        block,layout=card('Контекст текущего разговора')
        layout.addWidget(label('Загрузите историю выбранного сервера. Можно исправить реплики или сбросить разговор. Каждая новая реплика начинается с «Человек:» или «Собеседник:». Это не изменяет постоянную память.','subtitle'))
        self.context_edit=QPlainTextEdit(); self.context_edit.setMinimumHeight(170); self.context_edit.setPlaceholderText('Человек: О чём мы говорили?\nСобеседник: О локальных моделях.'); layout.addWidget(self.context_edit)
        layout.addWidget(row(button('Загрузить контекст',self.load_context),button('Применить',self.save_context),button('Сбросить разговор',lambda:self.command('reset'),danger=True),None)); body.addWidget(block)
        body.addStretch(); return page_widget

    def memory_view(self):
        page_widget,body=page('Память','Посмотрите, какие сведения сохранены, кто их сообщил и к кому они относятся.')
        block,layout=card()
        self.memory_kind=QComboBox(); self.memory_kind.addItem('Сведения','notes'); self.memory_kind.addItem('Участники','people'); self.memory_kind.addItem('Учебные примеры','examples')
        self.search=QLineEdit(); self.search.setPlaceholderText('Поиск по имени или содержимому'); self.search.textChanged.connect(self.filter_memory)
        self.memory_kind.currentIndexChanged.connect(self.refresh_memory)
        layout.addWidget(row(self.memory_kind,self.search,button('Обновить',self.refresh_memory)))
        self.memory_table=QTableWidget(0,5); self.memory_table.setHorizontalHeaderLabels(['Кому','Содержание','Автор','Дата','Тип'])
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.memory_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.memory_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.memory_table.setAlternatingRowColors(True)
        self.memory_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.memory_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.memory_table.verticalHeader().hide(); self.memory_table.setMinimumHeight(300); layout.addWidget(self.memory_table)
        self.memory_count=label('Сохранённых записей пока нет','subtitle'); layout.addWidget(self.memory_count)
        layout.addWidget(row(button('Удалить выбранную запись',self.delete_memory,danger=True),button('Забыть участника',self.forget_person,danger=True),None))
        layout.addWidget(row(button('Очистить память сервера',self.clear_memory,danger=True),button('Очистить учебные примеры',self.clear_learning,danger=True),None))
        body.addWidget(block)
        block,layout=card('Что означает обучение')
        self.learning_label=label('Память сохраняет сведения сразу. Изменение весов — отдельное экспериментальное LoRA-обучение. Новые веса допускаются только после проверки качества; неудачные кандидаты отклоняются.','subtitle'); layout.addWidget(self.learning_label)
        layout.addWidget(label('Удаление учебного примера удаляет его из базы. Оно не стирает влияние примера из ранее обученных весов. Удаление памяти сервера также удаляет его учебные примеры.','subtitle'))
        self.learning_install=button('Настроить экспериментальное обучение',self.setup_learning)
        layout.addWidget(row(self.learning_install,button('Открыть инструкцию',self.open_learning_docs),None))
        body.addWidget(block); body.addStretch(); return page_widget

    def logs_view(self):
        page_widget,body=page('Журнал и диагностика','Последние события с вашего компьютера. Перед копированием токен скрывается.')
        self.log_edit=QPlainTextEdit(); self.log_edit.setReadOnly(True); self.log_edit.setMinimumHeight(440); body.addWidget(self.log_edit,1)
        body.addWidget(row(button('Обновить',self.refresh_logs),button('Скопировать журнал',self.copy_logs),button('Открыть папку журналов',self.open_logs),None))
        return page_widget
