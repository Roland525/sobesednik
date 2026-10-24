import json
import unittest
from bot.diagnostics import TurnJournal, memory_evidence
from bot.memory import MEMORY_MARKER


class TurnDiagnosticsTests(unittest.TestCase):
    def test_recent_turns_are_bounded_redacted_and_not_shared_by_reference(self):
        journal=TurnJournal(secrets=('secret-token',),limit=2)
        old=journal.heard(1,'Tester','secret-token question','named')
        current=journal.heard(2,'Other','Second','background')
        journal.update(current,answer='secret-token answer',status='answered')
        journal.heard(3,'Third','Third','background')
        rows=journal.snapshot()
        self.assertEqual(len(rows),2);self.assertNotIn(old,[r['id'] for r in rows])
        self.assertNotIn('secret-token',str(rows))
        rows[0]['answer']='mutated'
        self.assertNotEqual(journal.snapshot()[0]['answer'],'mutated')
    def test_memory_evidence_only_contains_memory_actually_in_final_messages(self):
        data={'notes':[{'content':'Synthetic preference','source_nick':'Tester','source_id':1}], 'people':[]}
        self.assertEqual(memory_evidence([{'role':'user','content':'hello'}]),[])
        rows=memory_evidence([{'role':'user','content':MEMORY_MARKER+json.dumps(data)}])
        self.assertEqual(rows[0]['content'],'Synthetic preference')
        self.assertEqual(rows[0]['source'],'Tester')
    def test_unknown_late_update_cannot_create_a_ghost_turn(self):
        journal=TurnJournal();journal.update('missing',answer='late')
        self.assertEqual(journal.snapshot(),[])
