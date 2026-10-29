"""Create teaching-material sources from candidates without adopting them."""
import copy
import re
from app import db,provenance
from app.ai import identity,provider,contracts
from app.latex import exbook

def normalized_content(content):
    result=provider.normalise(content)
    for candidate in result:
        contracts.solved(candidate['solve'],candidate['question'])
        contracts.reviewed(candidate['review'])
    return result

def snapshot(job, content):
    content=normalized_content(content)
    title='AI候选题预览（待审校）'
    paper=identity.for_job(job)
    result={'snapshot_schema':2,'collection':{'id':job['id'],'title':title},'sections':[],
      'items':[],'questions':{},'question_versions':{},'options':exbook.normalise_options({'title':title,'subtitle':job['title'],'creator':'题库系统','show_sources':True})}
    content=sorted(content,key=lambda c:int(c['question']['qno']) if c['question']['qno'].isdigit() else 999999)
    for n,c in enumerate(content,1):
        qid='PREVIEW'+str(n);vid=qid+'V';q=copy.deepcopy(c['question']);s=c['solve'];r=c['review']
        answers={s['key']:s['answer'] for s in s.get('subanswers',[])}
        q.update(id=vid,question_id=qid,answer=s.get('answer',''),answer_keys=s.get('answer_keys',[]),
          teaching={**r.get('teaching',{}),'asset_placement':'inline','source_label':provenance.label(paper.get('name') or job['title'],q['qno'])+'；AI候选，待教师审校'})
        q['subquestions']=[{**x,'answer':answers.get(x['key'],'')} for x in q.get('subquestions') or []]
        entry={'question':{'id':qid},'version':q,'solutions':s.get('solutions',[]),'assets':[]}
        result['questions'][qid]=entry;result['question_versions'][vid]=entry
        result['items'].append({'id':'PI'+str(n),'question_id':qid,'question_version_id':vid,'order_no':n,'required':True,'role':'practice','space_mm':10 if q['qtype']=='solve' else 0})
    return result

def tex_files(job,content):
    snap=snapshot(job,content);opts=snap['options']
    files={'config.tex':exbook.render_config(opts),'body.tex':exbook.render_body(snap,opts,'student')}
    for version in ('student','answer','teacher'):files[version+'.tex']=exbook.render_document(snap,opts,version)
    return files
