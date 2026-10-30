import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs
from telethon.tl.custom.message import Message
from telethon.tl.types import PeerChannel, MessageEntityTextUrl, MessageEntityBold
from raw_message import extract, format_parts
from raw_delivery import Bot, DeliveryError
from live_store import Store
from raw_runtime import deliver_one


class RawTests(unittest.TestCase):
    def message(self,text,entities=None):
        return Message(id=123,peer_id=PeerChannel(999),message=text,entities=entities)

    def test_every_character_and_spaces_preserved(self):
        text='  🔥 상품  A\n\n12,900원  (35% 할인)\nhttps://link.coupang.com/a/xyz?x=1&not=2\n\n※ 이 포스팅은 광고입니다.  \n'
        post=extract(self.message(text))
        self.assertEqual(post['text'],text)
        for test in (False,True):
            payload=format_parts(post,test=test)
            self.assertEqual(len(payload),1)
            self.assertEqual(json.loads(payload[0])['text'],text)
            self.assertNotIn('[HOTDEAL]',json.loads(payload[0])['text'])

    def test_hidden_link_utf16_offsets_copied_exactly(self):
        text='🔥 구매하기\n원문'
        entities=[MessageEntityTextUrl(3,4,'https://toss.im/p?a=1&not=2'),MessageEntityBold(3,4)]
        payload=json.loads(format_parts(extract(self.message(text,entities)))[0])
        self.assertEqual(payload['entities'],[{'type':'text_link','offset':3,'length':4,'url':'https://toss.im/p?a=1&not=2'},{'type':'bold','offset':3,'length':4}])
        self.assertEqual(payload['text'],text)

    def test_plain_announcement_without_link_is_still_forwarded(self):
        text='공지  \n광고 안내\n'
        self.assertEqual(json.loads(format_parts(extract(self.message(text)))[0])['text'],text)

    def test_media_without_caption_sends_nothing(self):
        self.assertEqual(format_parts(extract(self.message(''))),[])

    def test_single_send_api_text_and_entities_are_identical(self):
        text='🔥  A\n\n이 문구  그대로 \n'
        payload=format_parts(extract(self.message(text,[MessageEntityBold(4,1)])))[0]
        calls=[]
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args):pass
            def read(self,size):return json.dumps({'ok':True,'result':{'message_id':10,'text':text}}).encode()
        def open(req,**kwargs):calls.append(req);return Response()
        bot=Bot('FAKE_TOKEN',open)
        bot.send('123',payload);bot.send('-456',payload)
        a,b=[parse_qs(req.data.decode(),keep_blank_values=True) for req in calls]
        self.assertEqual(a['text'],[text]);self.assertEqual(a['text'],b['text'])
        self.assertEqual(a['entities'],b['entities'])
        self.assertNotIn('parse_mode',a)
        self.assertTrue(all(req.full_url.endswith('/sendMessage') for req in calls))

    def test_server_echo_mismatch_never_marked_sent(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return b'{"ok":true,"result":{"message_id":10,"text":"altered"}}'
        with self.assertRaises(DeliveryError) as e:
            Bot('FAKE',lambda *a,**k:Response()).send('123',format_parts(extract(self.message('original')))[0])
        self.assertEqual(e.exception.status,'uncertain')

    def test_long_source_is_not_silently_split_or_truncated(self):
        payload=format_parts(extract(self.message('x'*4097)))
        self.assertEqual(len(payload),1)
        with self.assertRaises(DeliveryError) as e:
            Bot('FAKE',lambda *a,**k:self.fail('must not send')).send('123',payload[0])
        self.assertEqual(e.exception.status,'blocked')


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_partial_failure_and_duplicate_event(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.sqlite3';s=Store(path)
            targets=[('personal','123'),('group','-456')]
            s.initialize(100,targets)
            text='  🔥 원문\n\n광고 문구  \n'
            post={'id':101,'text':text,'entities':[]}
            s.put(101,text,format_parts(post),targets)
            calls=[]
            class FakeBot:
                def send(self,chat,payload):
                    calls.append((chat,json.loads(payload)['text']))
                    if chat=='123':raise DeliveryError('retry','CONNECT_FAILED',30)
                    return 22
            with redirect_stdout(io.StringIO()):
                await deliver_one(s,FakeBot());await deliver_one(s,FakeBot())
            s.close();s=Store(path);s.recover()
            s.put(101,text,format_parts(post),targets)
            self.assertIsNone(s.claim(now=0))
            row=s.claim(now=10**12)
            self.assertEqual(row['destination'],'personal')
            self.assertEqual(json.loads(row['text'])['text'],text)
            s.finish(row,'sent',remote_id=23)
            self.assertIsNone(s.claim())
            self.assertEqual(calls.count(('-456',text)),1)
            s.close()


if __name__=='__main__':unittest.main()
