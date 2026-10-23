import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QThread,Signal,QTimer,QUrl,QLockFile,Qt
from PySide6.QtGui import QDesktopServices,QIcon,QPixmap
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,
    QFrame,QListWidget,QStackedWidget,QComboBox,QMessageBox,QFileDialog,QTableWidgetItem)

from .credentials import save_token,load_token
from .discord_api import PORTAL,bot_account,invite_url
from .install import Installer
from .protocol import Protocol
from .runtime import Runtime,run_step,sample_resources
from .storage import Profile,default_root,install_payload,atomic_json,redact
from .style import STYLE
from .views import Views
from .widgets import label,row,WaveMark

REPOSITORY='https://github.com/Roland525/sobesednik'


def context_text(messages):
    return '\n\n'.join(('Человек: ' if m['role']=='user' else 'Собеседник: ')+m['content'] for m in messages)


def parse_context(text):
    result=[]
    for line in text.splitlines():
        if line.startswith(('Человек:','Собеседник:')):
            role='user' if line.startswith('Человек:') else 'assistant'
            result.append({'role':role,'content':line.split(':',1)[1].lstrip()})
        elif result: result[-1]['content']+='\n'+line
        elif line.strip(): raise ValueError('Начните реплику с «Человек:» или «Собеседник:»')
    for item in result: item['content']=item['content'].rstrip()
    if len(text)>12000: raise ValueError('Контекст не должен превышать 12000 символов')
    return result


def payload_path():
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))
    bundled=base/'assets/backend.zip'
    return bundled if bundled.exists() else base/'.desktop-build/backend.zip'


class Task(QThread):
    result=Signal(object); error=Signal(str); progress=Signal(int,str,str)
    def __init__(self,function,token='',parent=None):
        super().__init__(parent); self.function=function; self.token=token
    def run(self):
        try: self.result.emit(self.function(self))
        except Exception as error: self.error.emit(redact(str(error),self.token))


class MainWindow(QMainWindow,Views):
    def __init__(self,root,start_timers=True):
        super().__init__(); self.profile=Profile(root)
        self.profile_lock=self.acquire_lock(self.profile.root)
        self.runtime=Runtime(root)
        self.protocol=self.runtime.protocol; self.payload=payload_path()
        icon=self.payload.parent/'icon.png'
        if icon.exists(): self.setWindowIcon(QIcon(str(icon)))
        self.jobs=[]; self.busy=False; self.cancel=threading.Event(); self.force_close=False
        self.close_requested=False; self.resource_pending=False; self.memory_pending=False
        self.memory_reload=False; self.loaded_memory_key=None; self.last_memory_guild=None
        self.state={}; self.guilds=[]; self.memory_data=[]; self.token=''; self.account=None
        self.setWindowTitle('Собеседник — панель управления'); self.resize(1220,870); self.setMinimumSize(1040,720)
        self.setStyleSheet(STYLE)
        if self.payload.exists() and not self.runtime.running(): install_payload(self.payload,self.profile.root)
        try: self.token=load_token(self.profile.root)
        except Exception: pass
        central=QWidget(); outer=QHBoxLayout(central); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        sidebar=QFrame(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(222)
        side=QVBoxLayout(sidebar); side.setContentsMargins(18,26,18,22); side.setSpacing(16)
        side.addWidget(WaveMark()); side.addWidget(label('Собеседник','brand'))
        side.addWidget(label('ВАШ ГОЛОСОВОЙ ИИ','eyebrow'))
        self.navigation=QListWidget(); self.navigation.setObjectName('navigation')
        self.navigation.addItems(['Обзор','Discord','Голос и ответы','Персона','Память','Журнал'])
        side.addWidget(self.navigation,1)
        side.addWidget(label('Локальные модели\nВаш компьютер. Ваши данные.','subtitle'))
        side.addWidget(label('Windows · версия 1.0','eyebrow')); outer.addWidget(sidebar)
        content=QWidget(); content_layout=QVBoxLayout(content); content_layout.setContentsMargins(30,22,26,20); content_layout.setSpacing(17)
        self.guild_box=QComboBox(); self.guild_box.setMinimumWidth(230); self.guild_box.addItem('Сервер появится после запуска','')
        self.status_label=label('Остановлен','status',False)
        content_layout.addWidget(row(label('ПАНЕЛЬ УПРАВЛЕНИЯ','eyebrow'),None,self.guild_box,self.status_label))
        self.notice_label=label('','notice'); self.notice_label.hide(); content_layout.addWidget(self.notice_label)
        self.pages=QStackedWidget()
        for build in [self.overview_view,self.discord_view,self.settings_view,self.persona_view,self.memory_view,self.logs_view]:
            self.pages.addWidget(build())
        content_layout.addWidget(self.pages,1); outer.addWidget(content,1); self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex); self.navigation.setCurrentRow(0)
        self.guild_box.currentIndexChanged.connect(self.guild_changed)
        self.navigation.currentRowChanged.connect(self.page_changed)
        cache=self.profile.root/'temp/desktop/guilds.json'
        if cache.exists():
            try: self.update_guilds(json.loads(cache.read_text(encoding='utf-8')))
            except (OSError,ValueError,KeyError): pass
        self.reload_forms()
        self.remember_channel.toggled.connect(self.remember_changed)
        self.refresh_timer=QTimer(self); self.refresh_timer.timeout.connect(self.refresh)
        self.resource_timer=QTimer(self); self.resource_timer.timeout.connect(self.refresh_resources)
        if start_timers: self.refresh_timer.start(750); self.resource_timer.start(3500)
        self.refresh()
        if start_timers: QTimer.singleShot(100,self.refresh_resources)

    def notice(self,text):
        self.notice_label.setText(redact(text,self.token)); self.notice_label.setVisible(bool(text))

    def run_job(self,function,done=None,operation=True):
        if operation and self.busy:
            self.notice('Дождитесь завершения текущей операции.'); return
        if operation:
            self.busy=True; self.cancel=threading.Event()
        task=Task(function,self.token,self); self.jobs.append(task)
        task.progress.connect(self.install_update)
        def failed(message):
            self.notice(message)
            if self.close_requested:
                self.close_requested=False; self.refresh_timer.start(750); self.resource_timer.start(3500)
        task.error.connect(failed)
        if done: task.result.connect(done)
        def finished():
            if operation: self.busy=False; self.cancel_install.setEnabled(False)
            if task in self.jobs: self.jobs.remove(task)
            task.deleteLater()
            self.refresh()
            if self.close_requested: QTimer.singleShot(50,self.close)
        task.finished.connect(finished); task.start(); self.refresh()

    def reload_forms(self):
        try:
            cfg=self.profile.settings()
            self.wake_input.setText(cfg['wake_word']); self.voice_choice.setCurrentText(cfg['tts_supertonic_voice'])
            self.speed.setValue(cfg['tts_speed']); self.volume.setValue(cfg['tts_volume'])
            self.length.setValue(cfg['max_response_chars']); self.history.setValue(cfg['history_turns'])
            index=self.mode_choice.findData(cfg['conversation_mode']); self.mode_choice.setCurrentIndex(max(0,index))
            self.reasoning.setChecked(cfg['ollama_reasoning']); self.dota.setChecked(cfg['dota_patch_source_enabled'])
            self.remember_channel.blockSignals(True)
            self.remember_channel.setChecked(bool(cfg['auto_join_channel_id']))
            self.remember_channel.blockSignals(False)
            self.persona_edit.setPlainText(self.profile.persona()); self.folder_label.setText(str(self.profile.root))
            self.token_status.setText(('Защищённый токен сохранён' if (self.profile.root/'discord-token.bin').exists()
                else 'Найден токен прежней установки. Можно проверить и защитить его здесь.') if self.token else 'Токен ещё не сохранён')
            self.learning_label.setText('Постоянная память включена. Сбор примеров: '+('включён' if cfg['learning_capture_enabled'] else 'выключен')+
                '. Автоматическое LoRA-обучение: '+('включено' if cfg['learning_auto_enabled'] else 'выключено')+
                '. Кандидат применяется только после проверки качества ответов и задержки.')
        except Exception as error: self.notice('Не удалось открыть настройки: '+str(error))

    def guild_id(self):
        return int(self.guild_box.currentData() or 0)

    def update_guilds(self,guilds):
        previous=self.guild_box.currentData()
        signature=[(g['id'],g['name']) for g in guilds]
        if signature!=[(self.guild_box.itemData(i),self.guild_box.itemText(i)) for i in range(self.guild_box.count())]:
            if guilds: atomic_json(self.profile.root/'temp/desktop/guilds.json',guilds)
            self.guild_box.blockSignals(True); self.guild_box.clear()
            for guild in guilds: self.guild_box.addItem(guild['name'],guild['id'])
            if not guilds: self.guild_box.addItem('Сервер появится после запуска','')
            index=self.guild_box.findData(previous); self.guild_box.setCurrentIndex(max(0,index))
            self.guild_box.blockSignals(False)
        self.guilds=guilds; self.guild_changed()

    def page_changed(self,index):
        if index==4: self.refresh_memory()
        elif index==5: self.refresh_logs()

    def guild_changed(self):
        gid=str(self.guild_box.currentData() or '')
        guild=next((g for g in self.guilds if g['id']==gid),{})
        channels=guild.get('channels',[]); previous=self.channel_box.currentData()
        values=[(c['id'],c['name']) for c in channels]
        old=[(self.channel_box.itemData(i),self.channel_box.itemText(i)) for i in range(self.channel_box.count())]
        if values!=old:
            self.channel_box.clear()
            for channel in channels: self.channel_box.addItem(channel['name'],channel['id'])
            index=self.channel_box.findData(previous); self.channel_box.setCurrentIndex(max(0,index))
        connected=guild.get('connected_channel','')
        channel=next((c['name'] for c in channels if c['id']==connected),'')
        self.channel_status.setText('Сейчас в канале: '+channel if channel else 'Выберите канал и нажмите «Присоединиться».' if channels else 'Запустите бота; затем здесь появятся доступные каналы.')
        self.join_button.setEnabled(bool(channels) and bool(self.state) and not self.busy)
        self.leave_button.setEnabled(bool(connected) and not self.busy)
        if gid!=self.last_memory_guild:
            self.last_memory_guild=gid; self.refresh_memory()

    def refresh(self):
        self.state=self.protocol.status(); running=self.runtime.running()
        self.status_label.setText('В Discord' if self.state.get('ready') else 'Подключается…' if running else 'Остановлен')
        self.start_button.setEnabled(not running and not self.busy and self.runtime.installed())
        self.stop_button.setEnabled(running and not self.busy)
        self.restart_button.setEnabled(running and not self.busy)
        self.hush_button.setEnabled(bool(self.state) and not self.busy)
        self.install_button.setEnabled(not running and not self.busy)
        self.learning_install.setEnabled(not running and not self.busy and self.runtime.installed())
        if self.state: self.update_guilds(self.state.get('guilds',[]))
        else: self.guild_changed()
        if self.runtime.installed():
            self.install_button.setText('Проверить / доустановить')
            if not self.busy: self.install_stage.setText('Основные компоненты установлены')
            self.hero_title.setText('Собеседник на связи' if self.state else 'Можно запускать')
            self.hero_detail.setText('Выберите канал в разделе Discord. Обращайтесь по имени — или продолжайте уже начатую тему.' if self.state else 'Модели готовы. Укажите токен в разделе Discord и запустите бота.')
        metrics=self.state.get('sessions',{}).get(str(self.guild_id()),{}).get('metrics',{})
        latency=metrics.get('first_playback_ms')
        if isinstance(latency,(int,float)): self.latency_card.update_value(f'{latency/1000:.1f} с','Последний ответ · не среднее')

    def refresh_resources(self):
        if self.resource_pending or self.close_requested: return
        self.resource_pending=True
        def done(data):
            self.cpu_card.update_value(f'{data["cpu"]:.0f}%','Общая загрузка CPU',data['cpu'])
            self.ram_card.update_value(f'{data["ram_percent"]:.0f}%',f'{data["ram_gb"]:.1f} / {data["ram_total_gb"]:.0f} ГБ',data['ram_percent'])
            if data['gpu'] is not None:
                self.gpu_card.update_value(f'{data["gpu"]:.0f}%',f'VRAM {data["vram_gb"]:.1f} / {data["vram_total_gb"]:.1f} ГБ',data['gpu'])
            else: self.gpu_card.update_value('—','NVIDIA не обнаружена')
        task=Task(lambda _:sample_resources(),parent=self); self.jobs.append(task)
        task.result.connect(done)
        def finish():
            self.resource_pending=False; self.jobs.remove(task); task.deleteLater()
            if self.close_requested: QTimer.singleShot(50,self.close)
        task.finished.connect(finish); task.start()

    def start_bot(self): self.run_job(lambda _:self.runtime.start(),lambda _:self.notice('Запуск начался. Первичная загрузка моделей может занять до минуты.'))
    def stop_bot(self): self.run_job(lambda _:self.runtime.stop(),lambda _:self.notice('Бот остановлен.'))
    def restart_bot(self):
        def restart(_): self.runtime.stop(); self.runtime.start()
        self.run_job(restart,lambda _:self.notice('Бот перезапускается с сохранёнными настройками.'))

    def install_update(self,percent,title,line):
        self.install_progress.setValue(percent); self.install_stage.setText(title)
        if line: self.install_log.appendPlainText(redact(line,self.token))

    def install_components(self):
        if not self.payload.exists(): self.notice('Архив компонентов не найден. Используйте установочный EXE из GitHub Releases.'); return
        self.navigation.setCurrentRow(0)
        stt_model=self.stt_choice.currentData()
        def install(task):
            return Installer(self.profile.root,self.payload,task.progress.emit,self.cancel,self.runtime).run(stt_model)
        self.run_job(install,lambda _:(self.reload_forms(),self.notice('Установка завершена. Теперь откройте раздел Discord.')))
        self.cancel_install.setEnabled(True)

    def cancel_operation(self): self.cancel.set(); self.notice('Отмена запрошена. Завершаем текущую загрузку или дочерний процесс. Данные сохраняются.')

    def save_settings(self,restart=False):
        try:
            self.profile.save_settings({'wake_word':self.wake_input.text().strip(),
                'tts_supertonic_voice':self.voice_choice.currentText(),'tts_speed':self.speed.value(),
                'tts_volume':self.volume.value(),'conversation_mode':self.mode_choice.currentData(),
                'max_response_chars':self.length.value(),'history_turns':self.history.value(),
                'ollama_reasoning':self.reasoning.isChecked(),'dota_patch_source_enabled':self.dota.isChecked()})
            self.notice('Настройки сохранены. Для применения перезапустите бота.')
            if restart and self.runtime.running(): self.restart_bot()
        except Exception as error: self.notice(str(error))

    def save_persona(self):
        try: self.profile.save_persona(self.persona_edit.toPlainText()); self.notice('Персона сохранена. Новые вопросы используют обновлённое описание.')
        except Exception as error: self.notice(str(error))

    def check_token(self):
        token=self.token_input.text().strip() or self.token
        def check(_):
            account=bot_account(token); save_token(self.profile.root,token); return account
        def done(account):
            self.account=account; self.token=token; self.token_input.clear()
            self.token_status.setText('Токен сохранён · бот '+account.get('username',''))
            self.notice('Токен проверен. Пригласите бота на сервер; если бот уже запущен, перезапустите его.')
        self.run_job(check,done)

    def invite_bot(self):
        def done(account):
            self.account=account; QDesktopServices.openUrl(QUrl(invite_url(account['id'])))
        if self.account: done(self.account)
        else: self.run_job(lambda _:bot_account(self.token),done)

    def open_portal(self): QDesktopServices.openUrl(QUrl(PORTAL))
    def open_instructions(self): QDesktopServices.openUrl(QUrl(REPOSITORY+'#как-получить-токен-бота'))
    def open_learning_docs(self): QDesktopServices.openUrl(QUrl(REPOSITORY+'/blob/main/docs/LOCAL_LEARNING.md'))
    def open_folder(self): QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile.root)))
    def open_logs(self):
        path=self.profile.root/'logs'; path.mkdir(exist_ok=True); QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def choose_root(self):
        if self.runtime.running() or self.busy: self.notice('Сначала остановите бота и завершите текущую операцию.'); return
        folder=QFileDialog.getExistingDirectory(self,'Папка для моделей и памяти',str(self.profile.root))
        if not folder: return
        try:
            self.switch_profile(Path(folder))
            if not getattr(self,'custom_root',False): atomic_json(default_root().parent/'desktop.json',{'root':str(self.profile.root)})
            self.reload_forms(); self.notice('Папка переключена. Данные из прежней папки не удалены.'); self.refresh_memory()
        except Exception as error: self.notice(str(error))

    @staticmethod
    def acquire_lock(root):
        lock=QLockFile(str(Path(root)/'.desktop-window.lock')); lock.setStaleLockTime(0)
        if not lock.tryLock(100): raise RuntimeError('Панель для этой папки данных уже открыта.')
        return lock

    def switch_profile(self,folder):
        folder=Path(folder).resolve()
        if folder==self.profile.root: return
        if self.runtime.running() or self.busy or self.memory_pending:
            raise RuntimeError('Сначала остановите бота и дождитесь завершения операций.')
        folder.mkdir(parents=True,exist_ok=True)
        new_lock=self.acquire_lock(folder)
        try:
            profile=Profile(folder); runtime=Runtime(folder)
            if self.payload.exists() and not runtime.running(): install_payload(self.payload,folder)
            token=load_token(folder)
            self.runtime.close()
        except BaseException:
            new_lock.unlock(); raise
        self.profile_lock.unlock(); self.profile_lock=new_lock
        self.profile=profile; self.runtime=runtime; self.protocol=runtime.protocol
        self.token=token; self.state={}; self.guilds=[]; self.last_memory_guild=None
        self.memory_data=[]; self.loaded_memory_key=None; self.update_guilds([])

    def command(self,operation,payload=None):
        if not self.state: self.notice('Сначала запустите бота и дождитесь подключения.'); return
        body={'guild':self.guild_id(),**(payload or {})}
        def send(_):
            rid=self.protocol.request(operation,body); deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                result=self.protocol.response(rid)
                if result:
                    if not result['ok']: raise RuntimeError(result['message'])
                    return result['message']
                if self.cancel.is_set(): raise InterruptedError('Ожидание отменено')
                time.sleep(.15)
            raise RuntimeError('Нет подтверждения команды. Проверьте состояние бота перед повтором.')
        self.run_job(send,lambda message:(self.notice(message),self.refresh_memory()))

    def join_channel(self):
        channel=self.channel_box.currentData()
        if not channel: self.notice('Выберите голосовой канал'); return
        try:
            self.profile.save_settings({'auto_join_channel_id':int(channel) if self.remember_channel.isChecked() else 0})
            self.command('join',{'channel':int(channel)})
        except Exception as error: self.notice(str(error))

    def remember_changed(self,checked):
        try:
            channel=self.channel_box.currentData()
            if checked and not channel:
                self.notice('Сначала дождитесь списка каналов и выберите канал для автоматического подключения.')
                self.remember_channel.blockSignals(True); self.remember_channel.setChecked(False); self.remember_channel.blockSignals(False)
                return
            self.profile.save_settings({'auto_join_channel_id':int(channel) if checked else 0})
        except Exception as error: self.notice(str(error))

    def load_context(self):
        messages=self.state.get('sessions',{}).get(str(self.guild_id()),{}).get('context',[])
        self.context_edit.setPlainText(context_text(messages)); self.notice('Загружен текущий контекст выбранного сервера.')

    def save_context(self):
        try: self.command('replace_context',{'messages':parse_context(self.context_edit.toPlainText())})
        except Exception as error: self.notice(str(error))

    def refresh_memory(self,*args):
        if not hasattr(self,'memory_table'): return
        self.loaded_memory_key=None; self.memory_data=[]; self.memory_table.setRowCount(0)
        if self.memory_pending:
            self.memory_reload=True; return
        kind=self.memory_kind.currentData(); guild=self.guild_id(); self.memory_pending=True
        self.memory_reload=False
        def done(rows):
            if not self.memory_reload and (kind,guild)==(self.memory_kind.currentData(),self.guild_id()):
                self.memory_data=rows; self.loaded_memory_key=(kind,guild); self.fill_memory(kind)
        task=Task(lambda _:self.profile.memory_rows(kind,guild),self.token,self); self.jobs.append(task)
        task.result.connect(done); task.error.connect(self.notice)
        def finish():
            self.memory_pending=False; self.jobs.remove(task); task.deleteLater()
            if self.memory_reload and not self.close_requested: self.refresh_memory()
            if self.close_requested: QTimer.singleShot(50,self.close)
        task.finished.connect(finish); task.start()

    def fill_memory(self,kind=None):
        kind=kind or self.memory_kind.currentData(); self.memory_table.setRowCount(0)
        for record in self.memory_data:
            if kind=='notes':
                date=time.strftime('%d.%m.%Y %H:%M',time.localtime(record['updated']))
                values=[record['target_name'],record['content'],record['author_name'],date,record['kind']]
            elif kind=='people': values=[record['nick'],record['username']+
                ('\nИмена: '+record['remembered_names'] if record.get('remembered_names') else ''),str(record['uid']),'', 'Участник']
            else:
                date=time.strftime('%d.%m.%Y %H:%M',time.localtime(record['created']))
                values=['Вопрос / ответ',record['prompt']+' → '+record['target'],str(record['author']),date,record['kind']]
            index=self.memory_table.rowCount(); self.memory_table.insertRow(index)
            for column,value in enumerate(values):
                item=QTableWidgetItem(redact(str(value),self.token)); item.setToolTip(item.text()); self.memory_table.setItem(index,column,item)
        self.memory_table.resizeRowsToContents(); self.memory_count.setText(f'Показано записей: {len(self.memory_data)} · максимум 2000')
        self.filter_memory()

    def filter_memory(self,*args):
        value=self.search.text().casefold()
        for r in range(self.memory_table.rowCount()):
            self.memory_table.setRowHidden(r,not any(value in self.memory_table.item(r,c).text().casefold() for c in range(self.memory_table.columnCount())))

    def selected_record(self):
        if self.memory_pending or self.loaded_memory_key!=(self.memory_kind.currentData(),self.guild_id()):
            raise ValueError('Дождитесь загрузки выбранного списка памяти')
        row=self.memory_table.currentRow()
        if row<0 or row>=len(self.memory_data): raise ValueError('Сначала выберите запись в таблице')
        return self.memory_data[row]

    def confirm(self,text):
        return QMessageBox.question(self,'Подтвердите удаление',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)==QMessageBox.Yes

    def mutation(self,operation,payload):
        if self.state: self.command(operation,payload)
        elif self.runtime.running(): self.notice('Дождитесь подключения бота или остановите его перед изменением памяти.')
        else: self.run_job(lambda _:self.profile.mutate_memory(operation,payload),lambda _:(self.notice('Записи удалены.'),self.refresh_memory()))

    def delete_memory(self):
        try:
            record=self.selected_record(); kind=self.memory_kind.currentData()
            if kind=='people': self.forget_person(); return
            if self.confirm('Удалить выбранную запись? Вернуть её из этой панели нельзя.'):
                self.mutation('learning_delete' if kind=='examples' else 'memory_delete',
                    {'guild':record['guild'],'record_id':record['record_id']})
        except Exception as error: self.notice(str(error))

    def forget_person(self):
        try:
            record=self.selected_record()
            if self.memory_kind.currentData()!='people': raise ValueError('Выберите участника в списке «Участники»')
            if self.confirm('Забыть участника '+record['nick']+' на этом сервере? Его сведения, имена и учебные примеры будут удалены.'):
                self.mutation('memory_forget',{'guild':record['guild'],'uid':record['uid']})
        except Exception as error: self.notice(str(error))

    def clear_memory(self):
        if not self.guild_id(): self.notice('Выберите сервер. Для памяти без запущенного бота сначала выберите запись участника или сведения.'); return
        if self.confirm('Удалить всю постоянную память и учебные примеры выбранного сервера? Другие серверы сохранятся.'):
            self.mutation('memory_clear',{'guild':self.guild_id()})

    def clear_learning(self):
        if not self.guild_id(): self.notice('Сначала выберите сервер'); return
        if self.confirm('Удалить все учебные примеры выбранного сервера? Это не изменит уже обученные веса.'):
            self.mutation('learning_clear',{'guild':self.guild_id()})

    def setup_learning(self):
        if not self.confirm('Установить экспериментальный тренер? Дополнительная загрузка около 20 ГБ; нужно минимум 55 ГБ свободного места и NVIDIA GPU. Бот должен быть остановлен.'): return
        def install(task):
            run_step(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File','tools/setup_learning.ps1'],
                self.profile.root,self.runtime.python,lambda line:task.progress.emit(0,'Обучение',line),self.cancel,14400)
        self.navigation.setCurrentRow(0); self.run_job(install,lambda _:(self.reload_forms(),self.notice('Тренер установлен.')))
        self.cancel_install.setEnabled(True)

    def refresh_logs(self): self.log_edit.setPlainText(self.profile.logs(self.token))
    def copy_logs(self): self.refresh_logs(); QApplication.clipboard().setText(self.log_edit.toPlainText()); self.notice('Диагностический журнал скопирован; токен скрыт.')

    def closeEvent(self,event):
        if self.force_close: self.profile_lock.unlock(); event.accept(); return
        self.refresh_timer.stop(); self.resource_timer.stop()
        if self.jobs:
            self.close_requested=True; self.cancel.set(); self.notice('Завершаем фоновые операции перед закрытием…'); event.ignore(); return
        if self.runtime.running() and not self.close_requested:
            if QMessageBox.question(self,'Закрыть Собеседника?','Остановить бота и закрыть приложение?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                self.refresh_timer.start(750); self.resource_timer.start(3500); event.ignore(); return
        if self.runtime.running() or self.runtime.server:
            self.close_requested=True; self.run_job(lambda _:self.runtime.close()); event.ignore(); return
        self.runtime.stop(); self.profile_lock.unlock(); event.accept()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--capture-dir',type=Path)
    parser.add_argument('--verify-runtime',action='store_true',help='Проверка установленного backend без входа в Discord')
    args=parser.parse_args()
    root=args.data_dir or default_root()
    prefs=default_root().parent/'desktop.json'
    if not args.data_dir and prefs.exists():
        try: root=Path(json.loads(prefs.read_text(encoding='utf-8'))['root'])
        except (OSError,ValueError,KeyError): pass
    app=QApplication(sys.argv[:1]); app.setApplicationName('Собеседник'); app.setOrganizationName('Sobesednik')
    root.mkdir(parents=True,exist_ok=True)
    if args.verify_runtime:
        lines=[]; result={'frozen':bool(getattr(sys,'frozen',False))}
        try:
            runtime=Runtime(root)
            run_step([runtime.python,'main.py','--check'],root,runtime.python,lines.append,threading.Event(),180)
            result.update(ok=True,output=redact('\n'.join(lines)))
        except Exception as error: result.update(ok=False,error=redact(str(error)),output=redact('\n'.join(lines)))
        atomic_json(root/'reports/desktop-runtime-check.json',result)
        return 0 if result['ok'] else 1
    try: window=MainWindow(root,start_timers=not bool(args.capture_dir))
    except Exception as error:
        QMessageBox.critical(None,'Собеседник',str(error)); return 1
    window.custom_root=bool(args.data_dir); window.show()
    if args.capture_dir:
        def capture():
            args.capture_dir.mkdir(parents=True,exist_ok=True)
            for i in range(6):
                window.navigation.setCurrentRow(i); app.processEvents()
                window.grab().save(str(args.capture_dir/f'screen-{i}.png'))
            def finish():
                if window.jobs: QTimer.singleShot(50,finish); return
                window.force_close=True; window.close(); app.quit()
            finish()
        QTimer.singleShot(250,capture)
    return app.exec()
