"""Local quality controls, separate from application lifecycle and setup."""
import json
import time
from PySide6.QtWidgets import QTableWidget,QTableWidgetItem,QPlainTextEdit,QAbstractItemView,QHeaderView
from .widgets import card,label

REASONS={'named':'Обращение по имени','followup':'Продолжение темы тем же участником',
         'clarification':'Уточнение команды или памяти','background':'Беседа канала — ответ не запрошен',
         'hush':'Команда остановить речь','echo':'Похоже на эхо бота','duplicate':'Повтор той же фразы',
         'muted':'Речь остановлена','ambient':'Кандидат на редкое дополнение',
         'text':'Текстовый вопрос','voice':'Голосовой вопрос','say':'Служебная реплика'}


class QualityControls:
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
        self.turn_rows=[];self.turn_signature=None

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
