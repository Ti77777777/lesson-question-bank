"""Generate only the 12 fictional examples shipped in examples/demo.json.
Default destination: data/demo.db. Existing nonempty databases are never replaced.
"""
from __future__ import annotations
import argparse,json,os,sys,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def seed(destination):
    destination=Path(destination).resolve()
    if destination.exists():
        raise ValueError('目标数据库已存在；请使用新的演示库路径，避免覆盖个人资料。')
    destination.parent.mkdir(parents=True,exist_ok=True)
    os.environ['QBANK_DB']=str(destination)
    os.environ['QBANK_AI_WORKER']='0'
    from app import db
    if db.DB_PATH.resolve()!=destination:raise RuntimeError('数据库模块已绑定其他路径，请在独立进程执行seed。')
    from app.ai import worker
    from app.api.content import _create_question
    db.init_db();worker.init()
    fixture=json.loads((ROOT/'examples/demo.json').read_text(encoding='utf-8'))
    groups=[('演示试卷A（虚构基础测试）','monthly','senior1',{1,3,4,5,8,9}),('演示试卷B（虚构综合测试）','mock','senior3',{2,6,7,10,11,12})]
    with db.transaction() as conn:
        tags={};collections=[]
        for name,category,grade,indexes in groups:
            sid=db.next_id(conn,'SRC');cid=db.next_id(conn,'COL');collections.append(cid)
            conn.execute('INSERT INTO source(id,name,kind,exam_type,grade,usage_scope,license_basis,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(sid,name,'exam',category,grade,'功能演示','本项目MIT许可演示数据','虚构来源，无真实学校或原卷。',db.now()))
            conn.execute('INSERT INTO collection(id,title,topic,purpose,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(cid,name,'功能演示','handout','draft','全部题目仅用于软件功能演示。',db.now(),db.now()))
            sec=db.next_id(conn,'SEC');conn.execute('INSERT INTO collection_section(id,collection_id,title,order_no) VALUES(?,?,?,?)',(sec,cid,'演示题目',1))
            for order,q in enumerate((q for q in fixture['questions'] if q['demo_index'] in indexes),1):
                payload={k:v for k,v in q.items() if k not in ('knowledge','method','demo_index')}
                payload.update(created_by='演示数据生成器',note='虚构演示题，不是真实试卷或学生数据。',teaching={'demo':True,'review_note':'演示审校记录仅用于展示流程，不代表真实教师审校。'},solutions=[{'label':'演示解法','steps':q['analysis'],'is_primary':1}],occurrence={'source_id':sid,'qno':str(order),'pending':0,'note':'虚构演示来源'})
                qid=_create_question(payload,conn);vid=conn.execute('SELECT current_version_id FROM question WHERE id=?',(qid,)).fetchone()[0]
                for dimension,key in [('knowledge','knowledge'),('method','method')]:
                    name=q[key];tagkey=(dimension,name)
                    if tagkey not in tags:
                        tid=db.next_id(conn,'TAG');tags[tagkey]=tid;conn.execute('INSERT INTO tag(id,dimension,name,status,note,created_at) VALUES(?,?,?,?,?,?)',(tid,dimension,name,'active','演示标签',db.now()))
                    conn.execute('INSERT INTO question_tag(question_id,tag_id,role,source,created_at) VALUES(?,?,?,?,?)',(qid,tags[tagkey],'primary' if dimension=='knowledge' else 'secondary','manual',db.now()))
                conn.execute('INSERT INTO collection_item(id,collection_id,section_id,question_id,question_version_id,order_no,role,points,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.next_id(conn,'ITM'),cid,sec,qid,vid,order,'practice',q['total_points'],db.now()))
                if category=='monthly':
                    for field in ('stem','answer','classification','boundary','method_scope'):
                        conn.execute('INSERT INTO review(id,target_type,target_id,item,result,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'REV'),'question_version',vid,field,'pass','演示流程','虚构演示状态，不是真实教学审校。',db.now()))
                    for sol in conn.execute('SELECT id FROM solution WHERE question_version_id=?',(vid,)).fetchall():
                        conn.execute('INSERT INTO review(id,target_type,target_id,item,result,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.next_id(conn,'REV'),'solution',sol['id'],'solution','pass','演示流程','虚构演示状态，不是真实教学审校。',db.now()))
        conn.execute("INSERT INTO meta(key,value) VALUES('synthetic_demo','12 questions; no personal data')")
    db.db().execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
    return {'questions':12,'collections':collections,'database':str(destination)}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',type=Path,default=ROOT/'data/demo.db');args=parser.parse_args()
    try:print(json.dumps(seed(args.database),ensure_ascii=False))
    except (ValueError,RuntimeError) as exc:parser.error(str(exc))
