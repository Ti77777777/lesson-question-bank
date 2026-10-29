"""Build a source-only distribution. Never include a user's teaching database."""
from __future__ import annotations
import argparse,hashlib,json,re,sys,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitignore','.env.example','README.md','AGENTS.md','CHANGELOG.md','LICENSE','NOTICE','requirements.txt','package.json','run.py','启动.bat'}
SCRIPTS={'audit_project.py','import_gaokao_tex.py','test_backend.py','test_frontend.mjs','package_public.py'}
SUFFIXES={'app':{'.py','.sql','.js','.css','.html'},'tests':{'.py','.mjs'},'docs':{'.md'},'examples':{'.json'}}
RULES={
 'api_key':re.compile(r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})'),
 'session_token':re.compile(r'(?i)xsec_token=[A-Za-z0-9_+/%=-]{8,}'),
 'personal_home':re.compile(r'(?i)(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s]+|/(?:Users|home)/[^/\s]+/)'),
 'workstation_path':re.compile(r'(?i)[A-Z]:[\\/](?:xdf|HUI|项目)[\\/]'),
 'email':re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])'),
 'mobile_phone':re.compile(r'(?<!\w)1[3-9]\d{9}(?!\w)'),
 'identity_number':re.compile(r'(?<!\w)\d{17}[\dXx](?!\w)'),
}

def allowed(name):
    p=PurePosixPath(name)
    if p.is_absolute() or any(part in ('..','.','__pycache__') for part in p.parts):return False
    if name in ROOT_FILES:return True
    if name in ('data/schema.sql','data/seed.py','templates/exbook/ExBook.cls','templates/exbook/LICENSE'):return True
    if p.parts[0]=='scripts':return len(p.parts)==2 and p.name in SCRIPTS
    if p.parts[0]=='docs' and p.suffix=='.json':return name.startswith('docs/ai/') and p.name.endswith('.schema.json')
    return len(p.parts)>=2 and p.suffix in SUFFIXES.get(p.parts[0],set())

def candidates(root):
    root=Path(root).resolve();found=[]
    for folder in ['app','tests','docs','examples','scripts','data','templates']:
        start=root/folder
        if start.is_dir():
            for p in start.rglob('*'):
                if p.is_file() and allowed(p.relative_to(root).as_posix()):found.append(p)
    found.extend(root/name for name in ROOT_FILES if (root/name).is_file())
    for p in found:
        if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('Refusing file outside source tree: '+p.name)
    return sorted(set(found))

def scan(root,files=None):
    root=Path(root).resolve();issues=[]
    for p in files if files is not None else candidates(root):
        try:content=p.read_text(encoding='utf-8')
        except UnicodeError:issues.append({'file':p.relative_to(root).as_posix(),'rule':'unexpected_binary'});continue
        for rule,pattern in RULES.items():
            if pattern.search(content):issues.append({'file':p.relative_to(root).as_posix(),'rule':rule})
    return issues

def build(root,output):
    root=Path(root).resolve();files=candidates(root);issues=[];snapshots=[]
    for p in files:
        if p.is_symlink() or not p.resolve().is_relative_to(root):raise ValueError('File changed outside source tree: '+p.name)
        raw=p.read_bytes();name=p.relative_to(root).as_posix();snapshots.append((name,raw))
        try:content=raw.decode('utf-8')
        except UnicodeError:issues.append({'file':name,'rule':'unexpected_binary'});continue
        for rule,pattern in RULES.items():
            if pattern.search(content):issues.append({'file':name,'rule':rule})
    if issues:raise ValueError('Privacy scan failed: '+json.dumps(issues,ensure_ascii=False))
    output=Path(output).resolve()
    if output.exists():raise ValueError('Output already exists; choose another filename.')
    output.parent.mkdir(parents=True,exist_ok=True)
    manifest={'format':'qbank-public-source-v1','license':'MIT','personal_database_included':False,'files':{}}
    # Exclusive mode avoids overwriting an archive another process just created.
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in snapshots:
            archive.writestr('question-bank/'+name,raw)
            manifest['files'][name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        archive.writestr('question-bank/PUBLIC_MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return {'files':len(files),'bytes':output.stat().st_size,'archive':str(output)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');parser.add_argument('--output',type=Path,default=ROOT/'dist/question-bank-source.zip');args=parser.parse_args()
    if args.check:
        issues=scan(ROOT);print(json.dumps({'files':len(candidates(ROOT)),'issues':issues},ensure_ascii=False,indent=2));raise SystemExit(bool(issues))
    try:print(json.dumps(build(ROOT,args.output),ensure_ascii=False))
    except ValueError as exc:parser.error(str(exc))
