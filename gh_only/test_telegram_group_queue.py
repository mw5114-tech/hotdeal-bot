import unittest
from gh_only.test_jirum_manual_queue import baseline, deal, FakeStore
from gh_only.jirum_manual_queue import ingest_telegram, ingest, scan_telegram, finish

def payload(mid=10, url="https://link.coupang.com/a/AB12"):
    return dict(chat_id="-100321", message_id=mid, source_id=None,
                text="사과\n특가 9,900원", url=url)

class GroupQueueTests(unittest.TestCase):
    def test_group_only_persisted_without_public_watermark_change(self):
        state=ingest_telegram(baseline(),payload())
        self.assertEqual(state['last_scanned_id'],94743)
        self.assertEqual(state['pending'][0]['id'],-10)
        self.assertEqual(state['pending'][0]['sale_price_krw'],9900)
        self.assertEqual(ingest_telegram(state,payload()),state)

    def test_backlog_then_group_fifo(self):
        state,_,_=ingest(baseline(),[deal(94744)])
        state=ingest_telegram(state,payload())
        state=ingest_telegram(state,payload(11,'https://link.coupang.com/a/CD34'))
        self.assertEqual([r['id'] for r in state['pending']],[94744,-10,-11])

    def test_replayed_request_cannot_publish_next_head(self):
        store=FakeStore(baseline()); rid='12345678-1234-1234-1234-123456789abc'
        scan_telegram(store,payload(),rid)
        with self.assertRaisesRegex(RuntimeError,'ALREADY_CONSUMED'):
            scan_telegram(store,payload(),rid)
        self.assertEqual(len(store.writes),1)

    def test_public_channel_later_finds_same_product(self):
        p=payload();p['source_id']=94744
        state=ingest_telegram(baseline(),p)
        state,n,_=ingest(state,[deal(94744)])
        self.assertEqual(n,0)
        self.assertEqual([r['id'] for r in state['pending']],[-10])

    def test_published_group_product_not_requeued(self):
        store=FakeStore(ingest_telegram(baseline(),payload()))
        store.value['in_flight']=dict(id=-10,status='CLAIMED',run_id='run')
        finish(store,-10,'run',dict(status='POST_PUBLISHED_AND_FORMAT_VERIFIED',post_url='https://cafe.daangn.com/example'))
        self.assertEqual(ingest_telegram(store.value,payload(11))['pending'],[])

    def test_wrong_group_and_external_link_rejected(self):
        state=ingest_telegram(baseline(),payload())
        p=payload(11);p['chat_id']='-100999'
        with self.assertRaisesRegex(ValueError,'GROUP_CHANGED'):ingest_telegram(state,p)
        with self.assertRaisesRegex(ValueError,'COUPANG_URL'):ingest_telegram(baseline(),payload(url='https://11st.co.kr/item/1'))

    def test_group_uncertain_post_blocks_next(self):
        state=ingest_telegram(baseline(),payload())
        state['in_flight']=dict(id=-10,status='REVIEW_REQUIRED',run_id='run')
        with self.assertRaisesRegex(RuntimeError,'MANUAL_REVIEW'):ingest_telegram(state,payload(11))

if __name__=='__main__': unittest.main()
