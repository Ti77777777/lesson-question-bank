"""Native protocol envelopes, key isolation and billing, entirely mocked."""
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
(ROOT/'build').mkdir(exist_ok=True)
os.environ['QBANK_DB']=str(ROOT/'build/_test_protocols.db');os.environ['QBANK_AI_WORKER']='0'
for suffix in ('','-wal','-shm'):Path(os.environ['QBANK_DB']+suffix).unlink(missing_ok=True)
from app import db
from app.ai import settings,provider,protocols,credentials
from app.server import create_app


class ProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):create_app()
    @classmethod
    def tearDownClass(cls):db.close()
    def setUp(self):
        settings.save(settings.preset('deepseek'))
        with db.transaction() as c:
            bid=db.next_id(c,'AIB');self.jid=db.next_id(c,'AIJ')
            c.execute('INSERT INTO ai_batch VALUES(?,?,?,?,?,?,?)',(bid,'active','{}',100000000,100,db.now(),db.now()))
            c.execute('INSERT INTO ai_job(id,batch_id,title,state,stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(self.jid,bid,'合成接口验证','running','solve',db.now(),db.now()))
        self.key=patch.object(provider,'get_key',return_value='TEST_PROVIDER_KEY');self.key.start();self.addCleanup(self.key.stop)

    def config(self,preset):
        cfg=settings.preset(preset);cfg.update(model='synthetic-model',input_price=3,output_price=7)
        if preset=='custom':cfg['base_url']='https://gateway.example/v1'
        return settings.validate(cfg)

    def test_all_four_native_protocols_generate_parse_and_bill(self):
        responses={
          'deepseek':{'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':5}},
          'openai':{'status':'completed','output':[{'type':'reasoning','summary':[]},{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':{'input_tokens':10,'output_tokens':5}},
          'anthropic':{'content':[{'type':'thinking','thinking':'ignored'},{'type':'text','text':'{"ok":true}'}],'stop_reason':'end_turn','usage':{'input_tokens':10,'output_tokens':5}},
          'gemini':{'candidates':[{'content':{'parts':[{'text':'hidden','thought':True},{'text':'{"ok":true}'}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':3,'thoughtsTokenCount':2}},
        }
        suffixes={'deepseek':'/chat/completions','openai':'/responses','anthropic':'/messages','gemini':':generateContent'}
        for name,envelope in responses.items():
            cfg=self.config(name);settings.save(cfg)
            with self.subTest(protocol=name),patch.object(provider.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(envelope).encode())) as send:
                result=provider.call(self.jid,None,name,'system','JSON',max_tokens=100)
                request=send.call_args.args[0];payload=json.loads(request.data)
                self.assertTrue(result['ok']);self.assertTrue(request.full_url.endswith(suffixes[name]))
                self.assertEqual(payload.get('model','synthetic-model'),'synthetic-model')
                self.assertEqual(db.one('SELECT actual_cost_micro FROM ai_step_run WHERE job_id=? AND stage=?',(self.jid,name))['actual_cost_micro'],65)
                if name=='anthropic':self.assertEqual(request.get_header('X-api-key'),'TEST_PROVIDER_KEY')
                elif name=='gemini':self.assertEqual(request.get_header('X-goog-api-key'),'TEST_PROVIDER_KEY')
                else:self.assertEqual(request.get_header('Authorization'),'Bearer TEST_PROVIDER_KEY')
                self.assertNotIn('TEST_PROVIDER_KEY',json.dumps(payload))

    def test_images_translate_to_native_content_blocks(self):
        image={'type':'image_url','image_url':{'url':'data:image/jpeg;base64,cGl4ZWw='}}
        for name in ('deepseek','openai','anthropic','gemini'):
            cfg=self.config(name);endpoint,payload=protocols.request(cfg,'system',[{'type':'text','text':'JSON'},image],256,False)
            encoded=json.dumps(payload)
            self.assertIn('cGl4ZWw=',encoded);self.assertNotIn('TEST_PROVIDER_KEY',encoded)
            if name=='anthropic':self.assertIn('media_type',encoded)
            if name=='gemini':self.assertIn('inlineData',encoded)
            if name=='openai':self.assertIn('input_image',encoded);self.assertFalse(payload['store'])

    def test_compatible_gateway_can_omit_optional_parameters(self):
        cfg=self.config('custom');cfg.update(base_url='https://gateway.example/v1',json_mode=False,reasoning='none',output_token_field='max_completion_tokens')
        _,body=protocols.request(cfg,'sys',[{'type':'text','text':'JSON'}],100,True)
        self.assertNotIn('response_format',body);self.assertNotIn('thinking',body);self.assertNotIn('reasoning_effort',body)
        self.assertEqual(body['max_completion_tokens'],100)

    def test_unsafe_urls_and_embedded_secrets_rejected(self):
        for url in ('http://remote.example/v1','https://name:password@'+'gateway.example/v1','https://gateway.example/v1?key=secret','https://gateway.example/v1#secret','https://gateway.example/v1/chat/completions'):
            cfg=self.config('custom');cfg['base_url']=url
            with self.subTest(url=url),self.assertRaises(ValueError):settings.validate(cfg)

    def test_local_unauthenticated_service_needs_no_key(self):
        cfg=self.config('ollama');settings.save(cfg)
        with patch.object(provider,'get_key',return_value=''),patch.object(provider.urllib.request,'urlopen',return_value=io.BytesIO(b'{"data":[]}')) as send:
            provider.probe()
        self.assertIsNone(send.call_args.args[0].get_header('Authorization'))

    def test_key_paths_isolate_endpoints(self):
        a=self.config('openai');b={**a,'base_url':'https://gateway.example/v1'}
        self.assertNotEqual(credentials.credential_path(a),credentials.credential_path(b))
        self.assertEqual(credentials.credential_path(self.config('deepseek')).name,'deepseek.dpapi')

    def test_job_snapshot_survives_global_provider_change(self):
        cfg=self.config('openai')
        db.db().execute('UPDATE ai_batch SET config_json=? WHERE id=(SELECT batch_id FROM ai_job WHERE id=?)',(db.dumps({'connection':cfg}),self.jid));db.db().commit()
        settings.save(self.config('anthropic'))
        self.assertEqual(settings.for_job(self.jid)['protocol'],'responses')

    def test_model_and_endpoint_changes_invalidate_cache(self):
        envelope={'choices':[{'message':{'content':'{"ok":true}'}}],'usage':{'prompt_tokens':1,'completion_tokens':1}}
        cfg=self.config('deepseek');settings.save(cfg)
        with patch.object(provider,'_request',return_value=envelope) as send:
            provider.call(self.jid,None,'cache','sys','JSON',max_tokens=50)
            provider.call(self.jid,None,'cache','sys','JSON',max_tokens=50)
            cfg['model']='another-model';settings.save(cfg)
            provider.call(self.jid,None,'cache','sys','JSON',max_tokens=50)
            cfg['base_url']='https://gateway.example/v1';settings.save(cfg)
            provider.call(self.jid,None,'cache','sys','JSON',max_tokens=50)
        self.assertEqual(send.call_count,3)

    def test_text_only_and_missing_prices_fail_before_sending(self):
        cfg=self.config('openai');cfg['vision']=False;settings.save(cfg)
        with patch.object(provider,'_request') as send,self.assertRaises(provider.ProviderError) as exc:
            provider.call(self.jid,None,'vision','sys','JSON',images=['unreadable.png'])
        self.assertEqual(exc.exception.code,'vision_unsupported');send.assert_not_called()
        cfg['input_price']=None;settings.save(cfg)
        with patch.object(provider,'_request') as send,self.assertRaises(provider.ProviderError) as exc:
            provider.call(self.jid,None,'price','sys','JSON')
        self.assertEqual(exc.exception.code,'pricing_missing');send.assert_not_called()

    def test_synthetic_vision_probe_is_a_valid_png_and_checks_the_answer(self):
        import struct,zlib
        raw=provider.probe_image();self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
        at=8;compressed=b''
        while at<len(raw):
            size=struct.unpack('>I',raw[at:at+4])[0];kind=raw[at+4:at+8];data=raw[at+8:at+8+size]
            self.assertEqual(struct.unpack('>I',raw[at+8+size:at+12+size])[0],zlib.crc32(kind+data))
            if kind==b'IDAT':compressed+=data
            at+=size+12
        self.assertEqual(len(zlib.decompress(compressed)),64*(160*3+1))
        with patch.object(provider,'_request',return_value={'choices':[{'message':{'content':'{"result":5}'}}]}):
            self.assertTrue(provider.probe('vision')['json_valid'])
        with patch.object(provider,'_request',return_value={'choices':[{'message':{'content':'{"result":4}'}}]}),self.assertRaises(provider.ProviderError):
            provider.probe('vision')

    def test_truncated_outputs_are_not_cached(self):
        for name,envelope in [('openai',{'status':'incomplete','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}]}),('anthropic',{'stop_reason':'max_tokens','content':[{'type':'text','text':'{"ok":true}'}]}),('gemini',{'candidates':[{'finishReason':'MAX_TOKENS','content':{'parts':[{'text':'{"ok":true}'}]}}]})]:
            settings.save(self.config(name))
            with self.subTest(name=name),patch.object(provider,'_request',return_value=envelope),self.assertRaises(provider.ProviderError) as exc:
                provider.call(self.jid,None,name,'sys','JSON',max_tokens=100)
            self.assertEqual(exc.exception.code,'output_truncated')


if __name__=='__main__':unittest.main()
