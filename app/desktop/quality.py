"""Local quality controls, separate from application lifecycle and setup."""
import json
import time
from PySide6.QtWidgets import QTableWidget,QTableWidgetItem,QPlainTextEdit,QAbstractItemView,QHeaderView
from .widgets import card,label,button,row

REASONS={'named':'Обращение по имени','followup':'Продолжение темы тем же участником',
         'continuation':'Участник договаривает ещё не озвученный вопрос',
         'clarification':'Уточнение команды или памяти','background':'Беседа канала — ответ не запрошен',
         'hush':'Команда остановить речь','echo':'Похоже на эхо бота','duplicate':'Повтор той же фразы',
         'muted':'Речь остановлена','ambient':'Кандидат на редкое дополнение',
         'text':'Текстовый вопрос','voice':'Голосовой вопрос','say':'Служебная реплика'}


class QualityControls:
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
        layout.addWidget(label('Сравнение установленных моделей при бюджете 70 и 100. Синтетические вопросы проходят через речь, распознавание и озвучку, с Whisper в памяти. До 12 вариантов; может занять несколько минут. Бот должен быть остановлен. Настройки автоматически не меняются.','subtitle'))
        self.tune_button=button('Подобрать настройки',self.start_tuning)
        self.tune_apply=button('Применить рекомендацию',self.apply_tuning,True);self.tune_apply.setEnabled(False)
        layout.addWidget(row(self.tune_button,self.tune_apply,button('Отменить подбор',self.cancel_operation),None))
        self.tune_result=QPlainTextEdit();self.tune_result.setReadOnly(True);self.tune_result.setMaximumHeight(170)
        layout.addWidget(self.tune_result);body.addWidget(block);self.tuning_report=None

    def start_tuning(self):
        if self.runtime.running():self.notice('Для сравнения сначала остановите бота.');return
        if self.busy:return
        names=getattr(self,'installed_model_names',[])
        if not names:self.notice('Сначала обновите список установленных моделей.');return
        from .tuning import run_tuning,format_tuning
        candidates=[{'model':name,'resource_level':level} for name in names[:6] for level in (70,100)]
        self.tune_apply.setEnabled(False);self.tuning_report=None
        def work(task):
            self.runtime.ensure_ollama(self.cancel)
            return run_tuning(self.profile.root,candidates,self.cancel,lambda text:task.progress.emit(0,'Автоподбор',text))
        def done(report):
            self.tuning_report=report;self.tune_result.setPlainText(format_tuning(report))
            self.tune_apply.setEnabled(bool(report.get('recommendation')))
            self.notice('Подбор завершён. Результат синтетический; настройки изменятся только после применения.')
        self.run_job(work,done)

    def apply_tuning(self):
        if self.runtime.running() or self.busy:self.notice('Остановите бота перед применением.');return
        best=(self.tuning_report or {}).get('recommendation')
        if not best:return
        self.profile.save_settings({'ollama_model':best['model'],'resource_level':best['resource_level']})
        self.populate_models(getattr(self,'installed_model_names',[]),best['model']);self.resource_slider.setValue(best['resource_level'])
        self.notice('Рекомендация сохранена. Запустите бота для проверки в разговоре.')

    def build_diagnostics(self,body):
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
            self.runtime.ensure_ollama(self.cancel)
            run_step([self.runtime.python,root/'tools/check_feedback.py','--guild',str(guild),'--model',model,'--output',output],
                root,self.runtime.python,lambda text:task.progress.emit(0,'Проверка исправлений',text),self.cancel,900)
            return json.loads(output.read_text(encoding='utf-8-sig'))
        self.run_job(work,lambda report:self.feedback_result.setPlainText(json.dumps(report,ensure_ascii=False,indent=2)))

    def selected_turn(self):
        index=self.turn_table.currentRow()
        return self.turn_rows[index] if 0<=index<len(self.turn_rows) else None

    def refresh_diagnostics(self):
        if not hasattr(self,'turn_table'):return
        rows=list(reversed(self.state.get('sessions',{}).get(str(self.guild_id()),{}).get('turns',[])))
        signature=json.dumps(rows,ensure_ascii=False)
        if signature==self.turn_signature:return
        selected=self.selected_turn();rid=selected.get('id') if selected else None
        self.turn_signature=signature;self.turn_rows=rows;self.turn_table.setRowCount(0)
        index=0
        for i,turn in enumerate(rows):
            self.turn_table.insertRow(i)
            values=[time.strftime('%H:%M:%S',time.localtime(turn.get('at',0))),turn.get('name',''),turn.get('heard',''),turn.get('status','')]
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
            '\n\nПередано из памяти (это не доказательство использования каждого факта):\n'+memories+'\n\n'+timings)
