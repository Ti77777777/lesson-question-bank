"""Run nonpaid backend tests in separate processes and isolated databases."""
import os,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
modules=[p.stem for p in sorted((ROOT/'tests').glob('test_*.py')) if p.stem!='test_flow']
env=os.environ.copy();env['QBANK_AI_WORKER']='0';env.pop('DEEPSEEK_API_KEY',None);env.pop('QBANK_DB',None)
(ROOT/'build').mkdir(exist_ok=True)
for name in list(env):
    if name.endswith(('_API_KEY','_TOKEN')) or name=='QBANK_AI_KEY':env.pop(name,None)
failed=[]
for module in modules:
    print('Testing',module,flush=True)
    r=subprocess.run([sys.executable,'-X','utf8','-m','unittest','discover','-s','tests','-p',module+'.py'],cwd=ROOT,env=env)
    if r.returncode:failed.append(module)
print('Modules:',len(modules),'Failed:',len(failed))
raise SystemExit(bool(failed))
