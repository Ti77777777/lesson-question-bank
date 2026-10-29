import contextlib,shutil,uuid
import hashlib,json,os,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

@contextlib.contextmanager
def test_directory():
    base=Path(__file__).resolve().parents[1]/'build'/'_public_tests'
    base.mkdir(parents=True,exist_ok=True)
    folder=base/uuid.uuid4().hex;folder.mkdir()
    try:yield str(folder)
    finally:
        if folder.resolve().is_relative_to(base.resolve()):shutil.rmtree(folder)


class SyntheticDemoTest(unittest.TestCase):
    def test_seed_is_small_anonymous_and_does_not_overwrite(self):
        (ROOT/'build').mkdir(exist_ok=True)
        with test_directory() as tmp:
            target=Path(tmp)/'demo.db';env=os.environ.copy();env.pop('DEEPSEEK_API_KEY',None);env.pop('QBANK_DB',None)
            command=[sys.executable,'-X','utf8',str(ROOT/'data/seed.py'),'--database',str(target)]
            first=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
            self.assertEqual(first.returncode,0,first.stderr.decode('utf-8','replace'))
            with contextlib.closing(sqlite3.connect(target)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM question').fetchone()[0],12)
                self.assertEqual(dict(conn.execute('SELECT qtype,count(*) FROM question_version GROUP BY qtype')),{'single':3,'multi':3,'blank':3,'solve':3})
                self.assertEqual(conn.execute('SELECT count(*) FROM source').fetchone()[0],2)
                self.assertEqual(conn.execute('SELECT count(*) FROM collection').fetchone()[0],2)
                for table in ('ai_job','ai_step_run','source_file','asset','audience','release'):
                    self.assertEqual(conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(set(r[0] for r in conn.execute('SELECT DISTINCT reviewer FROM review')),{'演示流程'})
            before=hashlib.sha256(target.read_bytes()).hexdigest()
            second=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),before)

    def test_fixture_text_has_no_hidden_control_characters(self):
        text=(ROOT/'examples/demo.json').read_text(encoding='utf-8');fixture=json.loads(text)
        self.assertEqual(len(fixture['questions']),12)
        for q in fixture['questions']:
            def values(value):
                if isinstance(value,str):yield value
                elif isinstance(value,list):
                    for item in value:yield from values(item)
                elif isinstance(value,dict):
                    for item in value.values():yield from values(item)
            self.assertFalse(any(ord(c)<32 and c not in '\n\t\r' for value in values(q) for c in value))
            self.assertTrue(set(q.get('answer_keys',[]))<=set(o['key'] for o in q.get('options',[])))
