"""模型连接失败的诊断与计费边界；全部使用本地模拟响应。"""
import io
import json
import os
import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
os.environ['QBANK_DB']=str(ROOT/'build/_test_provider.db')
os.environ['QBANK_AI_WORKER']='0'
for suffix in ('','-wal','-shm'):
    path=Path(os.environ['QBANK_DB']+suffix)
    if path.exists():path.unlink()
from app import db
from app.server import create_app
from app.ai import provider


class ProviderTest(unittest.TestCase):
    def test_display_boundary_repair_is_conservative_and_idempotent(self):
        from app.ai import contracts
        cases=[(r'\[$a+b$\]',r'\[a+b\]'),(r'\[$a+b\]',r'\[a+b\]'),
               (r'\[a+b$\]',r'\[a+b\]'),(r'\[ \$5 \]',r'\[ \$5 \]'),
               (r'$A、B$',r'$A\text{、}B$')]
        for original,expected in cases:
            result=provider.normalise(original)
            self.assertEqual(result,expected)
            self.assertEqual(provider.normalise(result),result)
            contracts.text(result,'math')
        ambiguous=r'\[$a$+$b$\]'
        self.assertEqual(provider.normalise(ambiguous),ambiguous)
        with self.assertRaises(provider.ProviderError):contracts.text(provider.normalise(ambiguous),'math')

    @classmethod
    def setUpClass(cls):create_app()
    @classmethod
    def tearDownClass(cls):db.close()
    def setUp(self):
        with db.transaction() as conn:
            batch=db.next_id(conn,'AIB');self.job=db.next_id(conn,'AIJ')
            conn.execute('INSERT INTO ai_batch(id,state,config_json,max_cost_micro,max_requests,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(batch,'active','{}',1000000,100,db.now(),db.now()))
            conn.execute('INSERT INTO ai_job(id,batch_id,title,state,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(self.job,batch,'连接测试','running','recognize',db.now(),db.now()))
        self.key=patch.object(provider,'get_key',return_value='TEST_API_KEY_SENTINEL');self.key.start();self.addCleanup(self.key.stop)

    def failed_call(self,reason):
        with patch.object(provider.urllib.request,'urlopen',side_effect=urllib.error.URLError(reason)):
            with self.assertRaises(provider.ProviderError) as failure:
                provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100,thinking=False)
        return failure.exception,db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))

    def test_refused_connection_is_not_sent_and_diagnostics_are_redacted(self):
        with patch.object(provider.urllib.request,'getproxies',return_value={'https':'http://private-user:private-password@localhost:9'}):
            exc,row=self.failed_call(ConnectionRefusedError(10061,'TEST_API_KEY_SENTINEL private-password'))
        self.assertEqual(exc.code,'connection_refused')
        self.assertEqual(row['billing_state'],'not_sent');self.assertEqual(row['actual_cost_micro'],0)
        self.assertEqual(json.loads(row['error_json'])['detail']['transport'],'ConnectionRefusedError')
        combined=str(exc)+row['error_json']
        for secret in ('TEST_API_KEY_SENTINEL','private-password','private-user'):
            self.assertNotIn(secret,combined)

    def test_dns_failure_is_not_sent(self):
        exc,row=self.failed_call(socket.gaierror(11001,'lookup failed'))
        self.assertEqual(exc.code,'dns_error');self.assertEqual(row['actual_cost_micro'],0)

    def test_missing_key_never_sends_and_does_not_consume_budget(self):
        with patch.object(provider,'get_key',return_value=None),patch.object(provider.urllib.request,'urlopen') as request:
            with self.assertRaises(provider.ProviderError) as failure:
                provider.call(self.job,None,'missing-key','system','JSON',max_tokens=100)
        self.assertEqual(failure.exception.code,'key_missing')
        request.assert_not_called()
        row=db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))
        self.assertEqual(row['actual_cost_micro'],0)
        self.assertEqual(row['billing_state'],'not_sent')

    def test_timeout_keeps_unknown_billing_reservation(self):
        exc,row=self.failed_call(socket.timeout('timeout'))
        self.assertEqual(exc.code,'network_timeout');self.assertIsNone(row['actual_cost_micro'])
        self.assertEqual(row['billing_state'],'outcome_unknown')
        self.assertGreater(row['reserved_cost_micro'],0)

    def test_invalid_http_response_is_distinct_from_connection_failure(self):
        with patch.object(provider.urllib.request,'urlopen',return_value=io.BytesIO(b'<html>private response body</html>')):
            with self.assertRaises(provider.ProviderError) as failure:
                provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100)
        self.assertEqual(failure.exception.code,'invalid_response')
        row=db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))
        self.assertEqual(row['billing_state'],'outcome_unknown')
        self.assertNotIn('private response body',row['error_json'])

    def test_valid_model_response_and_cache_still_work(self):
        payload={'model':'test','choices':[{'finish_reason':'stop','message':{'content':'{"questions":[]}'}}],'usage':{'prompt_tokens':2,'completion_tokens':3}}
        with patch.object(provider.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as request:
            self.assertEqual(provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100),{'questions':[]})
            self.assertEqual(provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100),{'questions':[]})
        self.assertEqual(request.call_count,1)
        row=db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))
        self.assertEqual(row['actual_cost_micro'],28)

    def test_empty_choices_close_request_with_known_usage(self):
        payload={'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':3}}
        with patch.object(provider,'_request',return_value=payload):
            with self.assertRaises(provider.ProviderError):
                provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100)
        row=db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))
        self.assertEqual(row['state'],'failed')
        self.assertEqual(row['actual_cost_micro'],28)

    def test_missing_usage_keeps_reservation_after_success(self):
        payload={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}
        with patch.object(provider,'_request',return_value=payload):
            self.assertTrue(provider.call(self.job,None,'recognize:1','system','JSON',max_tokens=100)['ok'])
        row=db.one('SELECT * FROM ai_step_run WHERE job_id=?',(self.job,))
        self.assertEqual(row['state'],'completed')
        self.assertIsNone(row['actual_cost_micro'])
        self.assertEqual(row['billing_state'],'outcome_unknown')

    def test_image_preparation_failure_does_not_reserve_or_send(self):
        image=ROOT/'build/_provider-image.png';image.write_bytes(b'fixture')
        try:
            with patch.object(provider,'image_content',side_effect=OSError('unreadable local image')),patch.object(provider,'_request') as request:
                with self.assertRaises(provider.ProviderError):
                    provider.call(self.job,None,'recognize:1','system','JSON',[image],max_tokens=100)
            request.assert_not_called()
            self.assertEqual(db.scalar('SELECT count(*) FROM ai_step_run WHERE job_id=?',(self.job,)),0)
        finally:image.unlink()

    def test_malformed_envelopes_and_usage_never_stick_or_claim_zero(self):
        for i,payload in enumerate([
            {'choices':None}, {'choices':[None]}, {'choices':[{'message':None}]},
            {'choices':[{'message':{'content':42}}]},
            {'choices':[{'message':{'content':'[]'}}],'usage':None},
            {'choices':[],'usage':{'prompt_tokens':-1,'completion_tokens':0}},
        ]):
            with self.subTest(payload=payload),patch.object(provider,'_request',return_value=payload):
                with self.assertRaises(provider.ProviderError):
                    provider.call(self.job,None,'bad:'+str(i),'system','bad JSON '+str(i),max_tokens=100)
                row=db.one('SELECT * FROM ai_step_run WHERE job_id=? AND stage=?',(self.job,'bad:'+str(i)))
                self.assertEqual(row['state'],'failed')
                self.assertIsNone(row['actual_cost_micro'])
                self.assertEqual(row['billing_state'],'outcome_unknown')

    def test_jpeg_uses_its_actual_mime_and_receipt_keeps_raw_response(self):
        path=ROOT/'build/_provider-image.jpg';path.write_bytes(b'jpeg fixture')
        raw='{"ok":true}'
        response={'choices':[{'finish_reason':'stop','message':{'content':raw}}],
                  'usage':{'prompt_tokens':2,'completion_tokens':3}}
        try:
            with patch.object(provider,'_request',return_value=response) as request:
                provider.call(self.job,None,'image','system','JSON',[path],max_tokens=100)
            url=request.call_args.args[1]['messages'][1]['content'][1]['image_url']['url']
            self.assertTrue(url.startswith('data:image/jpeg;base64,'))
            receipt=json.loads(db.one('SELECT artifact_json FROM ai_step_run WHERE job_id=?',(self.job,))['artifact_json'])
            self.assertEqual(receipt['raw_content'],raw)
        finally:path.unlink()


if __name__=='__main__':unittest.main()
