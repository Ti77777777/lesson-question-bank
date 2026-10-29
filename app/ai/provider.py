import base64
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from app import db
from app.ai.credentials import get_key

MODEL='deepseek-flash'
BASE='https://api.deepseek.com'
PROMPT_VERSION='qbank-ai-1.0'

class ProviderError(Exception):
    def __init__(self, code, message, detail=None):
        super().__init__(message);self.code=code;self.detail=detail or {}

class BudgetPause(ProviderError):
    pass

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


# These failures occur before an HTTPS request body reaches the model service.
NOT_SENT = frozenset(('connection_refused', 'dns_error', 'tls_verify','key_missing'))
CONNECTION_ERRORS = frozenset((*NOT_SENT, 'network_timeout', 'network_unknown', 'invalid_response'))


def _connection_error(reason):
    detail={'transport':type(reason).__name__}
    for name in ('errno','winerror'):
        value=getattr(reason,name,None)
        if isinstance(value,int):detail[name]=value
    # Never retain exception strings, proxy URLs or request headers: they may contain credentials.
    if isinstance(reason,ConnectionRefusedError):
        proxy=urllib.request.getproxies().get('https')
        return ProviderError('connection_refused',
          ('无法连接当前网络代理' if proxy else '模型服务连接被拒绝')+'，本次请求未发出。请检查连接或重新启动题库服务。',detail)
    if isinstance(reason,socket.gaierror):
        return ProviderError('dns_error','无法解析模型服务地址，本次请求未发出。请检查网络或代理设置。',detail)
    if isinstance(reason,ssl.SSLCertVerificationError):
        return ProviderError('tls_verify','模型服务的安全连接验证失败，本次请求未发出。请检查网络代理的证书配置。',detail)
    if isinstance(reason,(TimeoutError,socket.timeout)):
        return ProviderError('network_timeout','等待模型响应超时，费用结果未知；可从失败步骤继续。',detail)
    return ProviderError('network_unknown','模型连接在请求过程中中断，费用结果未知；可从失败步骤继续。',detail)

def _request(endpoint, payload=None):
    key=get_key()
    if not key:raise ProviderError('key_missing','请先配置 DeepSeek 密钥')
    data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    request=urllib.request.Request(BASE+endpoint,data=data,headers={
        'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':'QBank/AI-1.0'})
    try:
        with urllib.request.urlopen(request,timeout=240) as response:
            raw=response.read()
            try:result=json.loads(raw)
            except (ValueError,TypeError):
                raise ProviderError('invalid_response','模型接口返回的内容不是有效 JSON，费用结果未知；请检查服务或网络代理后继续。',{'response_bytes':len(raw)}) from None
            if not isinstance(result,dict):
                raise ProviderError('invalid_response','模型接口返回格式异常，费用结果未知；请检查服务或网络代理后继续。',{'response_bytes':len(raw)})
            return result
    except urllib.error.HTTPError as exc:
        # Do not echo provider response bodies, request headers, credentials or prompts.
        messages={401:'DeepSeek 密钥无效，请重新配置',402:'DeepSeek 余额不足',403:'DeepSeek 拒绝访问',
                  404:'当前账户或接口不支持所选模型',429:'DeepSeek 请求受限，请稍后重试'}
        raise ProviderError('http_'+str(exc.code),messages.get(exc.code,'DeepSeek 服务返回错误（'+str(exc.code)+'）')) from None
    except urllib.error.URLError as exc:
        raise _connection_error(exc.reason) from None
    except (OSError,http.client.HTTPException) as exc:
        raise _connection_error(exc) from None

def probe():
    result=_request('/models')
    models=result.get('data')
    if not isinstance(models,list) or any(not isinstance(m,dict) or not isinstance(m.get('id'),str) for m in models):
        raise ProviderError('invalid_response','模型接口返回的模型列表格式异常')
    names=[m['id'] for m in models]
    return {'connected':True,'models':names,'selected_model':MODEL,
            'model_listed':MODEL in names,'vision':'等待图片请求验证'}

def image_content(path, data=None):
    p=Path(path)
    mime={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}.get(p.suffix.lower())
    if not mime:raise ProviderError('image_type','该题配图格式不能直接发给模型，请先转换为 PNG、JPEG 或 WebP')
    return {'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(p.read_bytes() if data is None else data).decode(),'detail':'high'}}

def parse_output(raw):
    value=raw.strip()
    if value.startswith('```'):
        value=value.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:
        result=json.loads(value)
        if not isinstance(result,dict):raise ValueError()
        return normalise(result)
    except (ValueError,TypeError):raise ProviderError('invalid_output','AI 返回的结果不是完整 JSON，请重试该步骤') from None

def _normalise_tex_escapes(value, allowed, environments):
    """Keep actual row separators distinct from over-escaped commands."""
    stack=[]
    row_environments=environments-{'equation','equation*','enumerate','itemize'}
    def token(match):
        slashes,command=match.groups()
        protected=any(env in row_environments and not doubled for env,doubled in stack)
        environment=re.match(r'\{([A-Za-z*]+)\}',value[match.end():]) if command in ('begin','end') else None
        if environment and environment[1] in environments and len(slashes) in (1,2) and not (protected and len(slashes)==2):
            if command=='begin':stack.append((environment[1],len(slashes)==2))
            elif stack and stack[-1][0]==environment[1]:stack.pop()
            return '\\'+command
        if protected:return match[0]
        if len(slashes)==2 and (command in allowed or command in ('{','}')):return '\\'+command
        if stack and stack[-1][1] and len(slashes)>=4 and len(slashes)%2==0:
            return '\\'*(len(slashes)//2)+command
        return match[0]
    return re.sub(r'(?<!\\)(\\+)([A-Za-z]+|[{}])',token,value)


def _normalise_display_dollars(value):
    """Remove redundant boundary dollars inside an explicit display wrapper."""
    def display(match):
        body=match.group(1)
        positions=[]
        i=0
        while i<len(body):
            if body[i]=='\\' and i+1<len(body):i+=2;continue
            if body[i]=='$':positions.append(i)
            i+=1
        left=len(body)-len(body.lstrip())
        right=len(body.rstrip())-1
        # Internal dollars may imply ambiguous mixed content. Leave those for
        # validation, and retain escaped currency dollars verbatim.
        if positions and len(positions)<=2 and set(positions)<={left,right}:
            body=''.join(ch for i,ch in enumerate(body) if i not in positions)
        return r'\['+body+r'\]'
    return re.sub(r'(?<!\\)\\\[(.*?)(?<!\\)\\\]',display,value,flags=re.S)


def normalise(value):
    if isinstance(value,list):return [normalise(x) for x in value]
    if isinstance(value,dict):return {k:normalise(v) for k,v in value.items()}
    if isinstance(value,str):
        # Repair over-escaped commands, never a genuine TeX row separator such
        # as \\y or \\\frac inside cases/matrices. Import lazily to avoid the
        # contract's ProviderError import cycle.
        from app.ai.contracts import ALLOWED,ENVIRONMENTS,normalise_math_punctuation
        value=_normalise_tex_escapes(value,ALLOWED,ENVIRONMENTS)
        value=re.sub(r'(?<!\\)\\n(?=[^a-z]|$)',lambda m:'\n',value)
        return normalise_math_punctuation(_normalise_display_dollars(value))
    return value

def call(job_id, question_id, stage, system, prompt, images=(), max_tokens=10000, thinking=True):
    try:snapshots=[(p,Path(p).read_bytes()) for p in images]
    except OSError:raise ProviderError('image_unavailable','无法读取题目配图，请检查原图后重试；本次请求未发出') from None
    cache_input={'system':system,'prompt':prompt,'images':[hashlib.sha256(raw).hexdigest() for _,raw in snapshots],
        'model':MODEL,'version':PROMPT_VERSION,'thinking':thinking,'max_tokens':max_tokens}
    if question_id:
        question=db.one('SELECT request_generation FROM ai_question WHERE id=?',(question_id,))
        if not question:raise ProviderError('question_missing','AI 题目记录已不存在')
        # Generation zero keeps all existing cache keys valid. Only an explicit
        # per-question retry starts a fresh generation; ordinary resume reuses it.
        if question['request_generation']:
            cache_input['retry']=[question_id,question['request_generation']]
    fingerprint=digest(cache_input)
    previous=db.one("SELECT * FROM ai_step_run WHERE job_id=? AND idempotency_key LIKE ? AND state='completed' ORDER BY completed_at DESC LIMIT 1",(job_id,fingerprint+'%'))
    if previous:
        return normalise(json.loads(previous['artifact_json'])['result'])
    try:content=[{'type':'text','text':prompt}]+[image_content(p,raw) for p,raw in snapshots]
    except OSError:raise ProviderError('image_unavailable','无法准备题目配图；本次请求未发出，请检查原图后重试') from None
    # Upper-bound estimate at published peak rates (2 CNY/M input, 8 CNY/M output).
    # Reserve byte-length*3 plus image allowance. Invoice may be lower off peak/cache.
    estimated_input=(len(system.encode())+len(prompt.encode()))*3+len(images)*4096+4096
    reservation=estimated_input*2+max_tokens*8
    with db.transaction() as conn:
        job=conn.execute('SELECT * FROM ai_job WHERE id=?',(job_id,)).fetchone()
        batch=conn.execute('SELECT * FROM ai_batch WHERE id=?',(job['batch_id'],)).fetchone()
        used=conn.execute('SELECT COALESCE(SUM(COALESCE(actual_cost_micro,reserved_cost_micro)),0),count(*) FROM ai_step_run r JOIN ai_job j ON j.id=r.job_id WHERE j.batch_id=?',(job['batch_id'],)).fetchone()
        if used[0]+reservation>batch['max_cost_micro'] or used[1]>=batch['max_requests']:
            raise BudgetPause('budget_paused','达到本批费用或调用上限，已保存进度；可增加预算后继续')
        run_id=db.next_id(conn,'AIR');attempt=conn.execute('SELECT count(*) FROM ai_step_run WHERE job_id=? AND stage=? AND ai_question_id IS ?',(job_id,stage,question_id)).fetchone()[0]+1
        conn.execute('INSERT INTO ai_step_run(id,job_id,ai_question_id,stage,attempt,idempotency_key,state,lease_owner,lease_until,input_hash,model,prompt_version,reserved_cost_micro,billing_state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (run_id,job_id,question_id,stage,attempt,fingerprint+':'+run_id,'running',str(os.getpid()),time.time()+900,fingerprint,MODEL,PROMPT_VERSION,reservation,'reserved',db.now()))
    payload={'model':MODEL,'messages':[{'role':'system','content':system},{'role':'user','content':content}],
             'response_format':{'type':'json_object'},'max_tokens':max_tokens,
             'thinking':{'type':'enabled' if thinking else 'disabled'}}
    if thinking:payload['reasoning_effort']='high'
    usage=actual=raw=None
    try:
        response=_request('/chat/completions',payload)
        if not isinstance(response,dict):raise ProviderError('invalid_response','模型接口返回的结果结构异常')
        usage=response.get('usage')
        if not isinstance(usage,dict):usage=None
        if usage and all(type(usage.get(k))==int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):
            actual=usage['prompt_tokens']*2+usage['completion_tokens']*8
        choices=response.get('choices')
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
            raise ProviderError('invalid_response','模型接口没有返回有效结果，已保留进度与费用记录')
        message=choices[0]
        body=message.get('message')
        raw=body.get('content') if isinstance(body,dict) else None
        if message.get('finish_reason')=='length':raise ProviderError('output_truncated','模型输出达到长度上限，请重试或缩小题目范围')
        if not isinstance(raw,str):raise ProviderError('invalid_response','模型接口没有返回有效正文，已保留进度与费用记录')
        result=parse_output(raw)
        receipt={'result':result,'raw_content':raw,'model':response.get('model',MODEL),'usage':usage,
                 'price_basis':'2026-10-27 官方 Flash 高峰价格上限估算，非账单：输入2元/M，输出8元/M'}
        with db.transaction() as conn:
            conn.execute("UPDATE ai_step_run SET state='completed',usage_json=?,actual_cost_micro=?,billing_state=?,output_hash=?,artifact_json=?,completed_at=? WHERE id=?",(db.dumps(usage),actual,'estimated_from_usage' if actual is not None else 'outcome_unknown',digest(result),db.dumps(receipt),db.now(),run_id))
        return result
    except Exception as exc:
        known=usage;cost=actual
        if isinstance(exc,ProviderError) and exc.code in ('http_401','http_402','http_403','http_404','http_429'):cost=0
        not_sent=isinstance(exc,ProviderError) and exc.code in NOT_SENT
        if not_sent:cost=0
        error={'code':getattr(exc,'code','invalid_output'),'message':str(exc) if isinstance(exc,ProviderError) else '模型结果处理失败'}
        if isinstance(exc,ProviderError) and exc.detail:error['detail']=exc.detail
        with db.transaction() as conn:
            conn.execute("UPDATE ai_step_run SET state='failed',usage_json=?,actual_cost_micro=?,billing_state=?,error_json=?,artifact_json=?,completed_at=? WHERE id=?",(db.dumps(known),cost,'not_sent' if not_sent else 'known_usage' if cost is not None else 'outcome_unknown',db.dumps(error),db.dumps({'raw_content':raw}) if isinstance(raw,str) and raw else None,db.now(),run_id))
        raise
