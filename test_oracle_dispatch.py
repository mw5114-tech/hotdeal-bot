import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from oracle_dispatch import Journal, classify, read_inbox, tick

GROUP='-5254258160'
SENDER=9233582187
def row(mid=100,text='사과 9,900원 https://link.coupang.com/a/AB12'):
    return dict(chat_id=int(GROUP),sender_id=SENDER,sender_is_bot=1,kind='live',message_id=mid,text=text,entities='[]')

class FakeGithub:
    def __init__(self):
        self.queue={'pending':[],'in_flight':None};self.history=[];self.posts=[];self.lose=False;self.reads=0
    def state(self):self.reads+=1;return self.queue
    def runs(self):self.reads+=1;return self.history
    def dispatch(self,rid,payload):
        self.posts.append((rid,json.loads(payload)))
        if self.lose:raise TimeoutError('lost response')
    def complete(self,conclusion='success'):
        rid=self.posts[-1][0]
        self.history=[dict(id=1,display_title='telegram-'+rid,head_branch='main',event='workflow_dispatch',status='completed',conclusion=conclusion,created_at='2026-11-01T00:00:00Z')]

class OracleTests(unittest.TestCase):
    def setUp(self):self.j=Journal(':memory:');self.gh=FakeGithub()
    def tearDown(self):self.j.db.close()
    def test_filter_and_hidden_link(self):
        self.assertIsNotNone(classify(row(),GROUP,SENDER))
        self.assertIsNone(classify(row(text='https://11st.co.kr/item/1'),GROUP,SENDER))
        self.assertIsNone(classify(row(),GROUP,1))
        r=row(text='상품');r['entities']=json.dumps([dict(type='text_link',url='https://link.coupang.com/a/AB12')])
        self.assertIsNotNone(classify(r,GROUP,SENDER))
    def test_duplicate_and_fifo(self):
        p=classify(row(),GROUP,SENDER);self.assertEqual(self.j.add(p),1);self.assertEqual(self.j.add(p),0)
        self.j.add(classify(row(101),GROUP,SENDER))
        self.assertEqual(tick(self.j,self.gh,True),'DISPATCH_ACCEPTED')
        self.assertEqual(self.gh.posts[0][1]['message_id'],100)
        self.assertEqual(tick(self.j,self.gh,True),'DISPATCH_VISIBILITY_REVIEW_REQUIRED')
        self.gh.complete();tick(self.j,self.gh,True)
        self.assertEqual([p[1]['message_id'] for p in self.gh.posts],[100,101])
    def test_idle(self):
        self.assertEqual(tick(self.j,self.gh,True),'IDLE');self.assertEqual(self.gh.posts,[])
    def test_capture_only(self):
        self.j.add(classify(row(),GROUP,SENDER))
        self.assertEqual(tick(self.j,self.gh,False),'CAPTURE_ONLY');self.assertEqual(self.gh.reads,0)
    def test_lost_response_with_and_without_event(self):
        for event in (False,True):
            with self.subTest(event=event):
                journal=Journal(':memory:');gh=FakeGithub();gh.queue['pending']=[dict(id=94744)];gh.lose=True
                if event:journal.add(classify(row(),GROUP,SENDER))
                with self.assertRaises(TimeoutError):tick(journal,gh,True)
                self.assertEqual(tick(journal,gh,True),'DISPATCH_UNCERTAIN_REVIEW_REQUIRED')
                self.assertEqual(len(gh.posts),1);journal.db.close()
    def test_restart_retains_uncertainty(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'journal.sqlite';j=Journal(path);self.gh.queue['pending']=[dict(id=94744)];self.gh.lose=True
            with self.assertRaises(TimeoutError):tick(j,self.gh,True)
            j.db.close();j=Journal(path)
            self.assertEqual(tick(j,self.gh,True),'DISPATCH_UNCERTAIN_REVIEW_REQUIRED');j.db.close()
    def test_failure_and_post_claim_block(self):
        self.j.add(classify(row(),GROUP,SENDER));tick(self.j,self.gh,True);self.gh.complete('failure')
        self.assertEqual(tick(self.j,self.gh,True),'FAILED_RUN_REVIEW_REQUIRED');self.assertEqual(len(self.gh.posts),1)
    def test_running_and_cap(self):
        self.gh.queue['pending']=[dict(id=94744)]
        self.gh.history=[dict(head_branch='main',event='workflow_dispatch',status='in_progress')]
        self.assertEqual(tick(self.j,self.gh,True),'WORKFLOW_IN_PROGRESS')
        self.gh.history=[dict(head_branch='main',event='workflow_dispatch',status='completed',conclusion='success',created_at='2026-11-01T00:00:00Z')]*12
        self.assertEqual(tick(self.j,self.gh,True,now=1793491201),'DAILY_CAP')
        self.assertEqual(self.gh.posts,[])
    def test_readonly_inbox_preserves_body_and_source_id(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox=Path(folder)/'inbox.sqlite';relay=Path(folder)/'relay.sqlite'
            c=sqlite3.connect(inbox)
            c.execute('CREATE TABLE inbox(chat_id,message_id,text,entities,sender_id,sender_is_bot,kind)')
            r=row();c.execute('INSERT INTO inbox VALUES(?,?,?,?,?,?,?)',tuple(r[k] for k in ('chat_id','message_id','text','entities','sender_id','sender_is_bot','kind')));c.commit();c.close()
            c=sqlite3.connect(relay);c.execute('CREATE TABLE delivery(remote_id,post_id,scope,chat,status)')
            c.execute("INSERT INTO delivery VALUES(100,94800,'live',?,'sent')",(GROUP,));c.commit();c.close()
            before=inbox.read_bytes()
            self.assertEqual(read_inbox(self.j,inbox,GROUP,SENDER,99,relay),1)
            self.assertEqual(read_inbox(self.j,inbox,GROUP,SENDER,99,relay),0)
            p=json.loads(self.j.db.execute('SELECT payload FROM events').fetchone()[0])
            self.assertEqual(p['source_id'],94800);self.assertEqual(p['text'],r['text'])
            self.assertEqual(inbox.read_bytes(),before)
    def test_post_review_blocks_backlog(self):
        self.gh.queue=dict(pending=[dict(id=94744)],in_flight=dict(status='REVIEW_REQUIRED'))
        self.assertEqual(tick(self.j,self.gh,True),'POST_REVIEW_REQUIRED');self.assertEqual(self.gh.posts,[])

if __name__=='__main__':unittest.main()
