"""Local quality controls, separate from application lifecycle and setup."""
import json
import time
from PySide6.QtWidgets import QTableWidget,QTableWidgetItem,QPlainTextEdit,QAbstractItemView,QHeaderView,QProgressBar
from .widgets import card,label,button,row

REASONS={'named':'Обращение по имени','followup':'Продолжение темы тем же участником',
         'public_question':'Общий вопрос — режим участника беседы',
         'no_useful_comment':'Не вступил: нет полезного дополнения',
         'ambient_cooldown':'Не вступил: выдерживает интервал после предыдущего ответа',
         'direct_priority':'Уступил прямому обращению','newer_speech':'Беседа уже продолжилась',
         'no_channel_pause':'Не вступил: нет паузы или речь остановлена',
         'repeated_comment':'Не повторяет прежнее дополнение','other_addressee':'Обращение к другому участнику',
         'mode_changed':'Прежняя необязательная реплика отменена после смены режима',
         'wake_only':'Имя услышал — жду сам вопрос',
         'continuation':'Участник договаривает ещё не озвученный вопрос',
         'clarification':'Уточнение команды или памяти','background':'Беседа канала — ответ не запрошен',
         'hush':'Команда остановить речь','echo':'Похоже на эхо бота','duplicate':'Повтор той же фразы',
         'muted':'Речь остановлена','ambient':'Кандидат на редкое дополнение',
         'stale_audio':'Истёк срок ожидания в очереди или общий срок голосового вопроса',
         'text':'Текстовый вопрос','voice':'Голосовой вопрос','say':'Служебная реплика'}
STATUSES={'heard':'Услышал','queued':'В очереди','working':'Готовлю ответ','answered':'Ответил',
          'ignored':'Пропущено','stopped':'Остановлен','waiting_for_speaker':'Ждёт окончания фразы',
          'waiting_for_question':'Ждёт вопроса','waiting_for_pause':'Ждёт паузы','error':'Ошибка'}

BEHAVIOR_NAMES={'strict':'По имени','summon':'Диалог после обращения','rare':'Очень редкие дополнения','participant':'Участник беседы'}
BEHAVIOR_DETAILS={
    'strict':'Отвечает на обращение по имени. Связанные уточнения без имени разрешаются флажком ниже.',
    'summon':'Начинает личный диалог после обращения. Продолжения без имени зависят от флажка ниже.',
    'rare':'Обращения по имени и очень редкие полезные дополнения. Общий вопрос сам по себе не гарантирует ответ.',
    'participant':'Отвечает на понятные общие вопросы без имени и иногда добавляет уместную реплику. Ждёт паузы, соблюдает интервал и не отвечает на каждое слово.'}


class QualityControls:
    def build_errand(self,body):
        block,layout=card('Голосовые поручения')
        self.errand_status=label('Поручений пока нет. Например: «Перейди в Общий, скажи: мы готовы, и вернись».','subtitle')
        layout.addWidget(self.errand_status)
        self.errand_steps=QPlainTextEdit();self.errand_steps.setReadOnly(True);self.errand_steps.setMaximumHeight(140)
        layout.addWidget(self.errand_steps);body.addWidget(block)

    def refresh_errand(self):
        if not hasattr(self,'errand_status'):return
        guild=next((g for g in self.state.get('guilds',[]) if str(g['id'])==str(self.guild_id())),{})
        task=guild.get('errand',{})
        if not task:
            self.errand_status.setText('Поручений в этом запуске ещё нет.');self.errand_steps.clear();return
        names={'found':'Канал найден','moving':'Перехожу','joined':'Перешёл','delivering':'Озвучиваю сообщение',
               'delivered':'Сообщение воспроизведено','returning':'Возвращаюсь','returned':'Вернулся',
               'completed':'Выполнено','cancelled':'Отменено','failed':'Не выполнено полностью'}
        self.errand_status.setText(task.get('origin','')+' → '+task.get('target','')+' · '+names.get(task.get('stage'),task.get('stage',''))+
            ' · сообщение: '+('передано' if task.get('delivered') else 'не подтверждено'))
        self.errand_steps.setPlainText('\n'.join(time.strftime('%H:%M:%S',time.localtime(e['at']))+' · '+names.get(e['stage'],e['stage']) for e in task.get('events',[])))

    def show_local_report(self,title,text):
        from PySide6.QtWidgets import QDialog,QVBoxLayout
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(760,470)
        layout=QVBoxLayout(dialog);edit=QPlainTextEdit();edit.setReadOnly(True);edit.setPlainText(text)
        layout.addWidget(edit);layout.addWidget(button('Закрыть',dialog.accept));dialog.exec()

    def selected_memory(self):
        index=self.memory_table.currentRow()
        return self.memory_data[index] if 0<=index<len(self.memory_data) else None

    def inspect_memory_history(self):
        record=self.selected_memory()
        if not record or self.memory_kind.currentData()!='notes':self.notice('Выберите запись в списке «Сведения».');return
        def show(rows):
            text='\n\n'.join(('Текущее' if r['replaced'] is None else 'Предыдущее')+' · '+
                time.strftime('%d.%m.%Y %H:%M',time.localtime(r['updated']))+' · '+r['source_nick']+'\n'+r['content'] for r in rows)
            self.show_local_report('История изменения сведений',text or 'Запись уже удалена.')
        self.run_job(lambda _:self.profile.memory_history(record['guild'],record['record_id']),show,operation=False)

    def check_memory_recall(self):
        from PySide6.QtWidgets import QInputDialog
        record=self.selected_memory()
        if not record or self.memory_kind.currentData() not in ('notes','people'):
            self.notice('Выберите сведения или участника, затем введите вопрос для проверки.');return
        uid=record.get('uid') or record.get('target') or record.get('source') or 0
        question,ok=QInputDialog.getText(self,'Проверить поиск в памяти','Вопрос от лица выбранного участника:')
        if not ok:return
        self.run_job(lambda _:self.profile.memory_check(record['guild'],uid,question),
            lambda report:self.show_local_report('Найденные воспоминания',json.dumps(report,ensure_ascii=False,indent=2)),operation=False)

    def build_tuning(self,body):
        block,layout=card('Подобрать настройки под компьютер')
        layout.addWidget(label('Сравнит до 6 установленных моделей в выбранном режиме; выбранная модель проверяется первой. Тест сам озвучит вопросы, распознает их и подготовит ответы. Может занять несколько минут; бот должен быть остановлен. Настройки изменятся только после «Применить рекомендацию».','subtitle'))
        self.tune_button=button('Подобрать настройки',self.start_tuning)
        self.tune_apply=button('Применить рекомендацию',self.apply_tuning,True);self.tune_apply.setEnabled(False)
        self.tune_cancel=button('Отменить подбор',self.cancel_operation);self.tune_cancel.setEnabled(False)
        layout.addWidget(row(self.tune_button,self.tune_apply,self.tune_cancel,None))
        self.tune_result=QPlainTextEdit();self.tune_result.setReadOnly(True);self.tune_result.setMaximumHeight(170)
        self.tune_progress=QProgressBar();self.tune_progress.setRange(0,1);self.tune_progress.setValue(0)
        self.tune_progress.setFormat('Проверено моделей: %v из %m')
        layout.addWidget(self.tune_progress);layout.addWidget(self.tune_result);body.addWidget(block);self.tuning_report=None

    def start_tuning(self):
        if self.runtime.running():self.notice('Для сравнения сначала остановите бота.');return
        if self.busy:return
        names=getattr(self,'installed_model_names',[])
        if not names:self.notice('Сначала обновите список установленных моделей.');return
        from .tuning import run_tuning,format_tuning,tuning_candidates
        candidates=tuning_candidates(names,self.model_choice.currentData(),self.compute_choice.currentData())
        self.tune_apply.setEnabled(False);self.tuning_report=None
        self.tuning_running=True
        self.tune_progress.setRange(0,0);self.tune_result.setPlainText('Готовим проверку распознавания, голоса и установленных моделей…')
        def work(task):
            with self.runtime.temporary_server(self.cancel):
                return run_tuning(self.profile.root,candidates,self.cancel,lambda text:task.progress.emit(0,'Автоподбор',text))
        def done(report):
            self.tuning_report=report;self.tune_result.setPlainText(format_tuning(report))
            self.tune_apply.setEnabled(bool(report.get('recommendation')))
            self.tune_progress.setRange(0,len(candidates));self.tune_progress.setValue(len(candidates))
            self.notice('Подбор завершён. Результат синтетический; настройки изменятся только после применения.')
        task=self.run_job(work,done)
        if task:
            task.error.connect(lambda text:(self.tune_progress.setRange(0,1),self.tune_progress.setValue(0),self.tune_result.setPlainText(text)))
            task.finished.connect(lambda:(setattr(self,'tuning_running',False),self.refresh()))

    def apply_tuning(self):
        if self.runtime.running() or self.busy:self.notice('Остановите бота перед применением.');return
        best=(self.tuning_report or {}).get('recommendation')
        if not best:return
        self.profile.save_settings({'ollama_model':best['model'],'compute_mode':best['compute_mode']})
        self.populate_models(getattr(self,'installed_model_names',[]),best['model'])
        self.compute_choice.setCurrentIndex(self.compute_choice.findData(best['compute_mode']))
        self.notice('Рекомендация сохранена. Запустите бота для проверки в разговоре.')

    def build_diagnostics(self,body):
        self.wake_diagnostic=label('Подключи бота и произнеси имя вместе с вопросом.','subtitle')
        body.addWidget(self.wake_diagnostic)
        block,layout=card('Последние реплики и ответы')
        layout.addWidget(label('Локальная диагностика: распознанная речь, правило ответа и сведения, переданные в модель. Последние 24 реплики; при сбросе разговора очищаются.','subtitle'))
        self.turn_table=QTableWidget(0,4);self.turn_table.setHorizontalHeaderLabels(['Время','Участник','Услышал','Результат'])
        self.turn_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.turn_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.turn_table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.turn_table.setMinimumHeight(200)
        self.turn_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.turn_table.itemSelectionChanged.connect(self.show_turn)
        layout.addWidget(self.turn_table)
        self.turn_detail=QPlainTextEdit();self.turn_detail.setReadOnly(True);self.turn_detail.setMinimumHeight(220)
        layout.addWidget(self.turn_detail);body.addWidget(block)
        layout.addWidget(row(button('Не понял вопрос',lambda:self.correct_turn('question')),
            button('Неверный ответ',lambda:self.correct_turn('answer')),button('Проверить исправления',self.check_feedback),None))
        self.feedback_result=QPlainTextEdit();self.feedback_result.setReadOnly(True);self.feedback_result.setMaximumHeight(140)
        layout.addWidget(self.feedback_result)
        self.turn_rows=[];self.turn_signature=None

    def describe_behavior(self,*args):
        if not hasattr(self,'behavior_description'):return
        mode=self.mode_choice.currentData()
        text=BEHAVIOR_DETAILS.get(mode,'Выберите режим общения.')
        active=self.state.get('behavior',{})
        if active:
            text+=' Сейчас работает: «'+BEHAVIOR_NAMES.get(active.get('conversation_mode'),str(active.get('conversation_mode')))+'».'
            selected={'conversation_mode':mode,'followup_enabled':self.followup.isChecked(),'interrupt_enabled':self.interrupt.isChecked()}
            if any(active.get(key)!=value for key,value in selected.items()):text+=' Нажмите «Сохранить», чтобы применить выбранное поведение.'
        elif self.state:text+=' Для применения поведения к прежней версии работающего бота нужен перезапуск.'
        else:text+=' После сохранения применяется при запуске бота.'
        self.behavior_description.setText(text)

    def correct_turn(self,kind):
        from PySide6.QtWidgets import QInputDialog
        turn=self.selected_turn()
        if not turn or not turn.get('user_id'):self.notice('Выберите реплику участника в диагностике.');return
        guild=self.guild_id();rid=turn['id']
        title='Какой вопрос ты произнёс?' if kind=='question' else 'Какой ответ правильный?'
        value,ok=QInputDialog.getMultiLineText(self,'Исправить реплику',title,
            turn.get('heard','') if kind=='question' else '')
        if ok and value.strip():self.command('feedback_add',{'guild':guild,'turn_id':rid,'kind':kind,'correction':value})

    def check_feedback(self):
        if self.runtime.running():self.notice('Для локальной проверки сначала остановите бота.');return
        if self.busy:return
        guild=self.guild_id()
        if not guild:self.notice('Выберите сервер.');return
        from .runtime import run_step
        model=self.profile.settings()['ollama_model'];root=self.profile.root
        output=root/'reports/feedback-check.json'
        def work(task):
            with self.runtime.temporary_server(self.cancel):
                run_step([self.runtime.python,root/'tools/check_feedback.py','--guild',str(guild),'--model',model,'--output',output],
                    root,self.runtime.python,lambda text:task.progress.emit(0,'Проверка исправлений',text),self.cancel,900)
                return json.loads(output.read_text(encoding='utf-8-sig'))
        self.run_job(work,lambda report:self.feedback_result.setPlainText(json.dumps(report,ensure_ascii=False,indent=2)))

    def selected_turn(self):
        index=self.turn_table.currentRow()
        return self.turn_rows[index] if 0<=index<len(self.turn_rows) else None

    def refresh_diagnostics(self):
        if not hasattr(self,'turn_table'):return
        saved=self.profile.settings()['wake_word']
        active=self.state.get('wake_word') if self.state else None
        if active:
            text='Запущенный бот ожидает: «'+active+'». Проверочная фраза: «'+active+', ты тут?».'
            if active.casefold()!=saved.casefold():
                text+=' В настройках сохранено «'+saved+'» — нужен перезапуск бота для применения имени.'
        else:
            text='Имя в настройках: «'+saved+'». Проверочная фраза: «'+saved+', ты тут?».'
            if self.state:text+=' Эта версия бота не передаёт имя вызова; после обновления перезапусти его.'
        self.wake_diagnostic.setText(text)
        rows=list(reversed(self.state.get('sessions',{}).get(str(self.guild_id()),{}).get('turns',[])))
        signature=json.dumps(rows,ensure_ascii=False)
        if signature==self.turn_signature:return
        selected=self.selected_turn();rid=selected.get('id') if selected else None
        self.turn_signature=signature;self.turn_rows=rows;self.turn_table.setRowCount(0)
        index=0
        for i,turn in enumerate(rows):
            self.turn_table.insertRow(i)
            values=[time.strftime('%H:%M:%S',time.localtime(turn.get('at',0))),turn.get('name',''),turn.get('heard',''),STATUSES.get(turn.get('status'),turn.get('status',''))]
            for col,value in enumerate(values):self.turn_table.setItem(i,col,QTableWidgetItem(str(value)))
            if turn.get('id')==rid:index=i
        if rows:self.turn_table.selectRow(index)
        else:self.turn_detail.setPlainText('Реплики появятся после подключения бота к голосовому каналу.')

    def show_turn(self):
        turn=self.selected_turn()
        if not turn:return
        memories='\n'.join('• '+m['content']+' — '+m['source'] for m in turn.get('memories',[])) or 'В этом запросе сведения из постоянной памяти не передавались.'
        timings=' · '.join(k+': '+str(round(v))+' мс' for k,v in turn.get('timings',{}).items() if isinstance(v,(int,float)) and k.endswith('_ms'))
        self.turn_detail.setPlainText('Услышал: '+turn.get('heard','')+'\n\nПричина: '+REASONS.get(turn.get('reason'),turn.get('reason',''))+
            '\n\nОтвет: '+(turn.get('answer') or 'Ответа пока нет / реплика оставлена без ответа.')+
            '\n\nСведения из памяти, переданные модели:\n'+memories+'\n\n'+timings)
