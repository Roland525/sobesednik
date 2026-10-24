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
from .quality import QualityControls
from .widgets import label,row,WaveMark
from .version import VERSION

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


class MainWindow(QMainWindow,Views,QualityControls):
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
        self.available_update=None; self.update_check_pending=False; self.updating=False
        self.model_downloading=False;self.benchmark_running=False
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
        from .widgets import button
        side.addWidget(label('Windows · версия '+VERSION,'eyebrow'))
        self.check_update_button=button('Проверить обновления',lambda:self.check_for_updates(manual=True))
        side.addWidget(self.check_update_button); outer.addWidget(sidebar)
        content=QWidget(); content_layout=QVBoxLayout(content); content_layout.setContentsMargins(30,22,26,20); content_layout.setSpacing(17)
        self.guild_box=QComboBox(); self.guild_box.setMinimumWidth(230); self.guild_box.addItem('Сервер появится после запуска','')
        self.status_label=label('Остановлен','status',False)
        content_layout.addWidget(row(label('ПАНЕЛЬ УПРАВЛЕНИЯ','eyebrow'),None,self.guild_box,self.status_label))
        self.update_banner=QFrame(); self.update_banner.setObjectName('card')
        update_layout=QVBoxLayout(self.update_banner); update_layout.setContentsMargins(18,14,18,14)
        self.update_text=label('','subtitle'); update_layout.addWidget(self.update_text)
        self.update_button=button('Скачать обновление',self.apply_update,True)
        self.update_cancel=button('Отменить загрузку',self.cancel_operation); self.update_cancel.hide()
        update_layout.addWidget(row(self.update_button,self.update_cancel,None))
        from PySide6.QtWidgets import QProgressBar
        self.update_progress=QProgressBar(); self.update_progress.setRange(0,100); self.update_progress.hide()
        update_layout.addWidget(self.update_progress); self.update_banner.hide(); content_layout.addWidget(self.update_banner)
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
        self.memory_timer=QTimer(self);self.memory_timer.timeout.connect(self.auto_refresh_memory)
        self.update_timer=QTimer(self); self.update_timer.timeout.connect(self.check_for_updates)
        if start_timers: self.refresh_timer.start(750); self.resource_timer.start(3500);self.memory_timer.start(4000)
        self.refresh()
        if start_timers: QTimer.singleShot(100,self.refresh_resources)
        if start_timers and self.runtime.installed(): QTimer.singleShot(300,self.refresh_models)
        if start_timers:
            QTimer.singleShot(1800,self.check_for_updates); self.update_timer.start(6*60*60*1000)
        report=self.profile.root/'reports/app-update-result.json'
        if report.exists():
            try:
                result=json.loads(report.read_text(encoding='utf-8-sig'))
                if not result.get('shown'):
                    if result.get('ok') and result.get('version')==VERSION:
                        self.notice('Приложение обновлено до '+VERSION+'. Настройки, модели и память сохранены.')
                    else: self.notice('Обновление не завершено: '+str(result.get('error','версия приложения не изменилась'))+'. Подробности в logs/app-update-install.log.')
                    result['shown']=True; atomic_json(report,result)
            except (OSError,ValueError): pass

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
            if self.cancel_install.isEnabled():
                self.install_update(self.install_progress.value(),'Ошибка',message)
            else:
                try:
                    folder=self.profile.root/'logs'; folder.mkdir(exist_ok=True)
                    with (folder/'desktop-panel.log').open('a',encoding='utf-8') as log:
                        log.write(time.strftime('%Y-%m-%d %H:%M:%S')+' '+redact(message,self.token)+'\n')
                except OSError: pass
            if self.close_requested:
                self.close_requested=False; self.refresh_timer.start(750); self.resource_timer.start(3500);self.memory_timer.start(4000)
        task.error.connect(failed)
        if done: task.result.connect(done)
        def finished():
            if operation: self.busy=False; self.cancel_install.setEnabled(False)
            if task in self.jobs: self.jobs.remove(task)
            task.deleteLater()
            self.refresh()
            if self.close_requested: QTimer.singleShot(50,self.close)
        task.finished.connect(finished); task.start(); self.refresh()
        return task

    def check_for_updates(self,manual=False):
        if self.update_check_pending or self.updating: return
        self.update_check_pending=True; self.check_update_button.setEnabled(False)
        self.check_update_button.setText('Проверяем…')
        from .updates import check_update
        task=Task(lambda _:check_update(),self.token,self); self.jobs.append(task)
        def done(info):
            self.offer_update(info)
            if manual and info is None: self.notice('Установлена актуальная версия '+VERSION+'.')
        def failed(message):
            if manual: self.notice('Не удалось проверить обновления: '+message+' Можно повторить позже.')
        def finish():
            self.update_check_pending=False; self.check_update_button.setEnabled(True)
            self.check_update_button.setText('Проверить обновления')
            self.jobs.remove(task); task.deleteLater()
            if self.close_requested: QTimer.singleShot(50,self.close)
        task.result.connect(done); task.error.connect(failed); task.finished.connect(finish); task.start()

    def offer_update(self,info):
        if self.updating: return
        self.available_update=info; self.update_banner.setVisible(info is not None)
        if info:
            self.update_text.setText(f'Доступна версия {info.version} · {info.size/1024**2:.0f} МБ. После загрузки приложение обновится и откроется снова.')
            self.update_button.setText('Скачать обновление'); self.update_progress.hide(); self.update_cancel.hide()
        self.refresh()

    def apply_update(self):
        if self.busy or self.updating:
            self.notice('Дождитесь завершения текущей операции.'); return
        info=self.available_update
        if not info: self.check_for_updates(manual=True); return
        from .updates import download_update
        state=self.protocol.status(); session=state.get('guilds',[])
        channel=next((int(g['connected_channel']) for g in session if g.get('connected_channel')),0)
        resume=channel if self.runtime.running() else -1
        self.updating=True; self.update_progress.setValue(0); self.update_progress.show(); self.update_cancel.show()
        self.update_text.setText('Скачиваем обновление. Бот продолжает работать до начала установки.')
        def prepare(task):
            installer=download_update(info,self.profile.root,task.progress.emit,self.cancel)
            if self.cancel.is_set(): raise InterruptedError('Загрузка обновления отменена.')
            task.progress.emit(100,'Установка обновления','Файл проверен. Останавливаем бота перед установкой…')
            self.runtime.close()
            return installer
        def failed(message):
            self.updating=False; self.update_cancel.hide(); self.update_progress.hide()
            self.update_text.setText('Обновление не установлено: '+message)
            self.update_button.setText('Повторить обновление'); self.refresh()
        task=self.run_job(prepare,lambda path:self.queue_update_handoff(info,path,resume))
        if task: task.error.connect(failed)

    def queue_update_handoff(self,info,path,resume):
        def finish():
            if self.jobs: QTimer.singleShot(50,finish); return
            from .updates import start_installer
            try:
                app=Path(sys.executable) if getattr(sys,'frozen',False) else Path(os.environ.get('LOCALAPPDATA',''))/'Programs/Sobesednik/Sobesednik.exe'
                if not app.is_file(): raise RuntimeError('Для установки обновления откройте установленное приложение EXE.')
                start_installer(info,path,self.profile.root,app,os.getpid(),resume)
            except Exception as error:
                self.updating=False; self.update_cancel.hide(); self.notice('Не удалось запустить установщик: '+str(error)); self.refresh(); return
            self.force_close=True; self.close(); QApplication.instance().quit()
        QTimer.singleShot(0,finish)

    def resume_updated_bot(self,channel):
        if self.busy: QTimer.singleShot(250,lambda:self.resume_updated_bot(channel)); return
        if not self.runtime.running():
            self.run_job(lambda _:self.runtime.start(resume_channel=channel),lambda _:self.notice('Обновление завершено. Бот запускается с прежними настройками.'))

    def reload_forms(self):
        try:
            cfg=self.profile.settings()
            self.populate_models(getattr(self,'installed_model_names',[]),cfg['ollama_model'])
            self.resource_slider.setValue(cfg.get('resource_level',70));self.resource_level_changed()
            self.wake_input.setText(cfg['wake_word']); self.voice_choice.setCurrentText(cfg['tts_supertonic_voice'])
            self.speed.setValue(cfg['tts_speed']); self.volume.setValue(cfg['tts_volume'])
            self.length.setValue(cfg['max_response_chars']); self.history.setValue(cfg['history_turns'])
            index=self.mode_choice.findData(cfg['conversation_mode']); self.mode_choice.setCurrentIndex(max(0,index))
            self.reasoning.setChecked(cfg['ollama_reasoning']); self.dota.setChecked(cfg['dota_patch_source_enabled'])
            self.followup.setChecked(cfg['followup_enabled']);self.interrupt.setChecked(cfg['interrupt_enabled'])
            self.remember_channel.blockSignals(True)
            self.remember_channel.setChecked(bool(cfg['auto_join_channel_id']))
            self.remember_channel.blockSignals(False)
            self.persona_edit.setPlainText(self.profile.persona()); self.folder_label.setText(str(self.profile.root))
            self.token_status.setText(('Защищённый токен сохранён' if (self.profile.root/'discord-token.bin').exists()
                else 'Найден токен прежней установки. Можно проверить и защитить его здесь.') if self.token else 'Токен ещё не сохранён')
            self.listen_conversation.setChecked(cfg.get('listen_conversation',True))
            self.memory_auto_enabled.setChecked(cfg.get('memory_auto_enabled',True))
            self.learning_capture_enabled.setChecked(cfg['learning_capture_enabled'])
            self.learning_auto_enabled.setChecked(cfg['learning_auto_enabled'])
            self.learning_capture_changed(cfg['learning_capture_enabled']);self.refresh_learning_status()
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
        self.refresh_errand()
        if self.pages.currentIndex()==5:self.refresh_diagnostics()
        self.status_label.setText('В Discord' if self.state.get('ready') else 'Подключается…' if running else 'Остановлен')
        self.start_button.setEnabled(not running and not self.busy and self.runtime.installed())
        self.stop_button.setEnabled(running and not self.busy)
        self.restart_button.setEnabled(running and not self.busy)
        self.hush_button.setEnabled(bool(self.state) and not self.busy)
        self.install_button.setEnabled(not running and not self.busy)
        self.learning_install.setEnabled(not running and not self.busy and self.runtime.installed())
        self.model_cancel.setEnabled(self.model_downloading)
        self.benchmark_cancel.setEnabled(self.benchmark_running)
        self.update_model_actions()
        self.update_button.setEnabled(bool(self.available_update) and not self.busy and not self.updating)
        if self.state: self.update_guilds(self.state.get('guilds',[]))
        else: self.guild_changed()
        if self.runtime.installed():
            self.install_button.setText('Проверить / доустановить')
            if not self.busy: self.install_stage.setText('Основные компоненты установлены')
            self.hero_title.setText('Собеседник на связи' if self.state else 'Можно запускать')
            self.hero_detail.setText('Выберите канал в разделе Discord. Обращайтесь по имени — или продолжайте уже начатую тему.' if self.state else ('Модели и токен готовы. Нажмите «Запустить бота».' if self.token else 'Модели готовы. Укажите токен в разделе Discord и запустите бота.'))
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
        if title in ('Обновление приложения','Установка обновления'):
            self.update_progress.setValue(percent); self.update_text.setText(line)
            if title=='Установка обновления': self.update_cancel.hide()
            return
        if title=='Проверка модели':self.benchmark_result.setPlainText(line);return
        self.install_progress.setValue(percent); self.install_stage.setText(title)
        if title=='Загрузка модели':
            self.model_progress.setValue(percent); self.model_status.setText(line)
        if line:
            clean=redact(line,self.token)
            self.install_log.appendPlainText(clean)
            try:
                folder=self.profile.root/'logs'; folder.mkdir(exist_ok=True)
                with (folder/'desktop-install.log').open('a',encoding='utf-8') as log:
                    log.write(f'[{percent}% {title}] {clean}\n')
            except OSError: pass

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
            model=self.model_choice.currentData()
            # Existing selection can be saved offline; changing it requires a
            # confirmed local model, never an arbitrary cloud endpoint.
            if model!=self.profile.settings()['ollama_model'] and model.casefold() not in {
                    item.casefold() for item in getattr(self,'installed_model_names',[])}:
                raise ValueError('Сначала скачайте модель и обновите список.')
            self.profile.save_settings({'ollama_model':model,'response_profile':'custom','resource_level':self.resource_slider.value(),
                'wake_word':self.wake_input.text().strip(),
                'tts_supertonic_voice':self.voice_choice.currentText(),'tts_speed':self.speed.value(),
                'tts_volume':self.volume.value(),'conversation_mode':self.mode_choice.currentData(),
                'max_response_chars':self.length.value(),'history_turns':self.history.value(),
                'ollama_reasoning':self.reasoning.isChecked(),'dota_patch_source_enabled':self.dota.isChecked(),
                'followup_enabled':self.followup.isChecked(),'interrupt_enabled':self.interrupt.isChecked()})
            self.notice('Настройки сохранены. Для применения перезапустите бота.')
            if restart and self.runtime.running(): self.restart_bot()
        except Exception as error: self.notice(str(error))

    def populate_models(self,names,selected=None):
        from .models import model_title
        selected=selected or self.model_choice.currentData() or self.profile.settings()['ollama_model']
        self.model_choice.blockSignals(True);self.model_choice.clear()
        for name in dict.fromkeys([*names,selected]):
            size=getattr(self,'installed_model_sizes',{}).get(name.casefold())
            installed=any(n.casefold()==name.casefold() for n in names)
            status='установлена'+(f' {size/1024**3:.2f} ГиБ' if size else '') if installed else 'текущая'
            self.model_choice.addItem(model_title(name)+' · '+status,name)
            self.model_choice.setItemData(self.model_choice.count()-1,name,Qt.ToolTipRole)
        self.model_choice.setCurrentIndex(max(0,self.model_choice.findData(selected)))
        self.model_choice.blockSignals(False);self.model_selection_changed()

    def model_selection_changed(self,*args):
        self.update_model_actions()
        if not hasattr(self,'model_detail'):return
        from .models import model_detail
        name=self.model_choice.currentData() or ''
        details=getattr(self,'installed_model_metadata',{}).get(name.casefold(),{})
        self.model_detail.setText(model_detail(name,getattr(self,'installed_model_sizes',{}).get(name.casefold()),details))
        self.model_detail.setToolTip(name);self.model_choice.setToolTip(name)
        self.load_saved_benchmark()

    def load_saved_benchmark(self):
        if not hasattr(self,'benchmark_result') or self.benchmark_running:return
        from .model_benchmark import format_report,report_key
        model=self.model_choice.currentData() or '';level=self.resource_slider.value()
        paths=[self.profile.root/f'reports/model-benchmark-{report_key(model,level)}.json',self.profile.root/'reports/model-benchmark.json']
        for path in paths:
            try:
                report=json.loads(path.read_text(encoding='utf-8-sig'))
                if (report.get('model','').casefold()!=model.casefold() or report.get('resource_level')!=level
                        or report.get('suite')!='synthetic-exact-v1'):continue
                self.benchmark_result.setPlainText('Сохранённый локальный тест · '+str(report.get('created_utc','дата неизвестна'))+'\n'+format_report(report))
                return
            except (OSError,ValueError,KeyError,TypeError,AttributeError):continue
        self.benchmark_result.setPlainText('Для этой модели при выбранных ресурсах сохранённого теста пока нет.')

    def resource_level_changed(self,*args):
        from bot.config import resource_settings
        value=self.resource_slider.value();settings=resource_settings(value)
        layers=settings['ollama_num_gpu']
        gpu='CPU' if layers==0 else 'Все слои на GPU, если помещаются' if layers==-1 else f'До {layers} слоёв модели на GPU'
        self.resource_description.setText(f"{value}/100 · модель: {settings['ollama_num_thread']} потоков CPU · распознавание: {settings['whisper_cpu_threads']} · синтез: {settings['tts_cpu_threads']} · {gpu}")
        self.load_saved_benchmark()

    def update_model_actions(self,*args):
        if not hasattr(self,'model_download_button'):return
        installed={name.casefold() for name in getattr(self,'installed_model_names',[])}
        already=(self.model_download.currentData() or '').casefold() in installed
        self.model_download.setToolTip(self.model_download.currentData() or '')
        self.model_download_button.setText('Уже установлена' if already else 'Скачать модель')
        self.model_download_button.setEnabled(not already and not self.busy)
        self.model_download.setEnabled(not self.busy)
        if hasattr(self,'benchmark_button'):
            self.model_choice.setEnabled(not self.benchmark_running);self.resource_slider.setEnabled(not self.benchmark_running)
            self.benchmark_button.setEnabled(not self.busy and not self.runtime.running() and
                (self.model_choice.currentData() or '').casefold() in installed)

    def accept_model_inventory(self,items,selected=None):
        names=[item['name'] for item in items]
        self.installed_model_names=names
        self.installed_model_sizes={item['name'].casefold():item['size'] for item in items}
        self.installed_model_metadata={item['name'].casefold():item.get('details',{}) for item in items}
        self.populate_models(names,selected)

    def refresh_models(self):
        def fetch(_):
            from .models import installed_model_info
            self.runtime.ensure_ollama(self.cancel)
            return installed_model_info('http://127.0.0.1:11434')
        def done(items):
            self.accept_model_inventory(items)
            self.model_status.setText(f'Локально установлено моделей: {len(items)}. Выбор применяется после сохранения и перезапуска.')
        self.run_job(fetch,done)

    def download_model(self):
        name=self.model_download.currentData()
        if name.casefold() in {item.casefold() for item in getattr(self,'installed_model_names',[])}:
            self.update_model_actions();self.notice('Эта модель уже установлена. Выберите её в списке разговорных моделей.');return
        if self.busy:self.notice('Дождитесь завершения текущей операции.');return
        def download(task):
            from .models import pull_model,installed_model_info
            self.runtime.ensure_ollama(self.cancel)
            pull_model('http://127.0.0.1:11434',name,task.progress.emit,self.cancel)
            return installed_model_info('http://127.0.0.1:11434')
        def done(items):
            self.accept_model_inventory(items,name)
            self.model_progress.setValue(100); self.model_status.setText('Модель скачана. Нажмите «Сохранить и перезапустить», чтобы использовать её.')
        self.model_downloading=True
        task=self.run_job(download,done)
        if task:task.finished.connect(lambda:(setattr(self,'model_downloading',False),self.refresh()))
        self.cancel_install.setEnabled(self.busy)

    def benchmark_model(self):
        if self.runtime.running() or self.busy:
            self.notice('Сначала остановите бота и дождитесь завершения операций.');return
        model=self.model_choice.currentData()
        if (model or '').casefold() not in {name.casefold() for name in getattr(self,'installed_model_names',[])}:
            self.notice('Сначала обновите список и выберите установленную модель.');return
        from .model_benchmark import run_benchmark,format_report,report_key
        cfg=self.profile.settings();cfg.update(ollama_model=model,resource_level=self.resource_slider.value(),
            ollama_reasoning=self.reasoning.isChecked())
        self.benchmark_result.setPlainText('Проверка запускается… Используются текущие значения ползунка и выбранная модель.')
        self.benchmark_running=True
        def measure(task):
            self.runtime.ensure_ollama(self.cancel)
            return run_benchmark(cfg,self.cancel,lambda text:task.progress.emit(0,'Проверка модели',text))
        def done(report):
            key=report_key(model,cfg['resource_level'])
            atomic_json(self.profile.root/f'reports/model-benchmark-{key}.json',report)
            atomic_json(self.profile.root/'reports/model-benchmark.json',report)
            self.benchmark_result.setPlainText(format_report(report))
            self.notice('Локальный тест завершён. Полный отчёт: reports/model-benchmark.json.')
        task=self.run_job(measure,done)
        if task:
            task.error.connect(lambda text:self.benchmark_result.setPlainText(text))
            task.finished.connect(lambda:(setattr(self,'benchmark_running',False),self.refresh()))

    def learning_capture_changed(self,enabled):
        self.learning_auto_enabled.setEnabled(enabled)
        if not enabled:self.learning_auto_enabled.setChecked(False)

    def save_memory_settings(self):
        try:
            capture=self.learning_capture_enabled.isChecked()
            self.profile.save_settings({'listen_conversation':self.listen_conversation.isChecked(),
                'memory_auto_enabled':self.memory_auto_enabled.isChecked(),'learning_capture_enabled':capture,
                'learning_auto_enabled':capture and self.learning_auto_enabled.isChecked()})
            self.refresh_learning_status();self.notice('Параметры памяти и обучения сохранены. Перезапустите бота для применения.')
        except Exception as error:self.notice(str(error))

    def refresh_learning_status(self):
        from bot.learning_cycle import compatible_training_model
        cfg=self.profile.settings()
        text=('Постоянная память: '+('включена' if cfg['memory_enabled'] else 'выключена')+
              '. Автосохранение фактов: '+('включено' if cfg.get('memory_auto_enabled',True) else 'выключено')+
              '. Сбор примеров: '+('включён' if cfg['learning_capture_enabled'] else 'выключен')+
              '. Авто-LoRA: '+('включено' if cfg['learning_auto_enabled'] else 'выключено')+'. ')
        status=None
        for name in ('cycle-state.json','state.json'):
            path=self.profile.root/'data/training'/name
            if not path.exists():continue
            try:status=json.loads(path.read_text(encoding='utf-8-sig')).get('status')
            except (OSError,ValueError,AttributeError):status='unreadable'
            break
        labels={'running':'В журнале тренера: цикл выполняется.','accepted':'В журнале тренера: кандидат принят после проверки.',
                'rejected':'В журнале тренера: кандидат отклонён.','failed':'В журнале тренера: цикл завершился ошибкой.',
                'skipped':'В журнале тренера: цикл пропущен; проверьте данные и зависимости.'}
        self.learning_label.setText(text+labels.get(status,'Подтверждённого результата обучения весов в журнале нет.')+
            ' Среда тренера: '+('Python найден' if (self.profile.root/'.venv-training/Scripts/python.exe').is_file() else 'Python не найден')+
            ' (это не проверка готовности всех зависимостей). Текущая модель: '+cfg['ollama_model']+
            '. Авто-LoRA пока поддерживает только Qwen 3 · 8B и его обученные версии. '+
            ('Текущая модель совместима.' if compatible_training_model(cfg['ollama_model']) else 'Для текущей модели обучение весов недоступно; память и примеры продолжают работать.')+
            ' Включённый сбор примеров сам по себе не меняет веса.')

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
            self.notice('Токен проверен. Перезапустите бота для применения.' if self.runtime.running()
                else ('Токен проверен. Пригласите бота на сервер и нажмите «Запустить бота» в Обзоре.'
                      if self.runtime.installed() else 'Токен сохранён. Завершите установку компонентов в Обзоре — после этого появится кнопка запуска.'))
        self.run_job(check,done)

    def invite_bot(self):
        def done(account):
            self.account=account; QDesktopServices.openUrl(QUrl(invite_url(account['id'])))
        if self.account: done(self.account)
        else: self.run_job(lambda _:bot_account(self.token),done)

    def open_portal(self): QDesktopServices.openUrl(QUrl(PORTAL))
    def open_instructions(self): QDesktopServices.openUrl(QUrl(REPOSITORY+'/blob/main/docs/GUIDE.md#как-получить-токен-бота'))
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
        current=self.protocol.status()
        session=current.get('sessions',{}).get(str(self.guild_id()))
        if session is None:
            self.notice('Нет активного разговора на выбранном сервере. Существующий текст оставлен в редакторе.'); return
        messages=session.get('context',[])
        self.context_edit.setPlainText(context_text(messages)); self.notice('Загружен текущий контекст выбранного сервера.')

    def save_context(self):
        try: self.command('replace_context',{'messages':parse_context(self.context_edit.toPlainText())})
        except Exception as error: self.notice(str(error))

    def add_memory(self):
        text=self.memory_input.text().strip(); guild=self.guild_id()
        if not guild: self.notice('Сначала выберите сервер вверху окна.'); return
        def done(_):
            self.memory_input.clear(); self.notice('Заметка записана в постоянную память.'); self.refresh_memory()
        self.run_job(lambda _:self.profile.mutate_memory('memory_add',{'guild':guild,'content':text}),done)

    def auto_refresh_memory(self):
        if self.pages.currentIndex()==4 and not self.busy and not self.memory_pending and not self.close_requested:
            self.refresh_memory(automatic=True)

    def refresh_memory(self,*args,automatic=False):
        if not hasattr(self,'memory_table'): return
        self.refresh_learning_status()
        selected=None;scroll=self.memory_table.verticalScrollBar().value()
        if automatic and self.loaded_memory_key==(self.memory_kind.currentData(),self.guild_id()):
            index=self.memory_table.currentRow()
            if 0<=index<len(self.memory_data):selected=self.memory_data[index].get('record_id')
        else:self.memory_data=[];self.memory_table.setRowCount(0)
        self.loaded_memory_key=None
        if self.memory_pending:
            self.memory_reload=True; return
        kind=self.memory_kind.currentData(); guild=self.guild_id(); self.memory_pending=True
        self.memory_reload=False
        def done(rows):
            if not self.memory_reload and (kind,guild)==(self.memory_kind.currentData(),self.guild_id()):
                self.memory_data=rows; self.loaded_memory_key=(kind,guild); self.fill_memory(kind)
                if automatic:
                    for index,record in enumerate(rows):
                        if selected is not None and record.get('record_id')==selected:self.memory_table.selectRow(index);break
                    self.memory_table.verticalScrollBar().setValue(scroll)
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
        self.refresh_timer.stop(); self.resource_timer.stop();self.memory_timer.stop()
        if self.jobs:
            self.close_requested=True; self.cancel.set(); self.notice('Завершаем фоновые операции перед закрытием…'); event.ignore(); return
        if self.runtime.running() and not self.close_requested:
            if QMessageBox.question(self,'Закрыть Собеседника?','Остановить бота и закрыть приложение?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
                self.refresh_timer.start(750); self.resource_timer.start(3500);self.memory_timer.start(4000); event.ignore(); return
        if self.runtime.running() or self.runtime.server:
            self.close_requested=True; self.run_job(lambda _:self.runtime.close()); event.ignore(); return
        self.runtime.stop(); self.profile_lock.unlock(); event.accept()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--capture-dir',type=Path)
    parser.add_argument('--verify-runtime',action='store_true',help='Проверка установленного backend без входа в Discord')
    parser.add_argument('--resume-after-update',type=int,default=None,help=argparse.SUPPRESS)
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
    if args.resume_after_update is not None:
        QTimer.singleShot(1200,lambda:window.resume_updated_bot(args.resume_after_update))
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
