import base64
import math
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
from app.ai import settings, protocols

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

def _request(endpoint, payload=None, config=None):
    cfg = config or settings.current()
    try: key = '' if cfg['auth_mode'] == 'none' else get_key(cfg)
    except (ValueError, OSError):
        raise ProviderError('key_missing', '当前地址的已存密钥无法读取，请重新配置或使用对应环境变量') from None
    if not key and cfg['auth_mode'] != 'none':
        raise ProviderError('key_missing', '请先为当前服务地址配置密钥，或设置 ' + cfg['key_env'])
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(cfg['base_url'] + endpoint, data=data, headers={
        'Content-Type': 'application/json', 'User-Agent': 'QBank/AI-2.0'})
    if cfg['protocol'] == 'anthropic': request.add_header('anthropic-version', '2023-06-01')
    if key and cfg['auth_mode'] != 'none':
        name = 'x-api-key' if cfg['protocol'] == 'anthropic' else 'x-goog-api-key' if cfg['protocol'] == 'gemini' else 'api-key' if cfg['auth_mode'] == 'api-key' else 'Authorization'
        value = 'Bearer ' + key if name == 'Authorization' else key
        # Credentials must never be copied by urllib to a redirected host.
        request.add_unredirected_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=cfg['timeout']) as response:
            raw = response.read()
            try: result = json.loads(raw)
            except (ValueError, TypeError):
                raise ProviderError('invalid_response', '接口未返回有效JSON，费用结果未知；请检查地址与服务状态', {'response_bytes': len(raw)}) from None
            if not isinstance(result, dict): raise ProviderError('invalid_response', '模型接口返回格式异常，费用结果未知')
            return result
    except urllib.error.HTTPError as exc:
        messages = {400: '当前模型不接受所选参数：请检查协议、JSON模式、输出长度参数与推理设置',
                    401: '当前接口密钥无效，请检查服务地址、地区和密钥', 402: '当前接口余额不足',
                    403: '当前接口拒绝访问，请检查模型权限', 404: '当前地址、模型或接口路径不存在',
                    429: '当前接口请求受限，请稍后继续；不会自动重复发送'}
        raise ProviderError('http_' + str(exc.code), messages.get(exc.code, '模型服务返回错误（' + str(exc.code) + '）')) from None
    except urllib.error.URLError as exc: raise _connection_error(exc.reason) from None
    except (OSError, http.client.HTTPException) as exc: raise _connection_error(exc) from None


def probe(mode='models'):
    cfg = settings.current()
    if mode == 'models':
        result = _request('/models', config=cfg)
        rows = result.get('models' if cfg['protocol'] == 'gemini' else 'data')
        key = 'name' if cfg['protocol'] == 'gemini' else 'id'
        if not isinstance(rows, list) or any(not isinstance(m, dict) or not isinstance(m.get(key), str) for m in rows):
            raise ProviderError('invalid_response', '接口未返回标准模型列表；可手动填写模型，再运行文字或图片验证')
        names = [m[key].removeprefix('models/') for m in rows]
        return {'connected': True, 'models': names, 'selected_model': cfg['model'],
                'model_listed': cfg['model'] in names, 'vision': '尚未验证图片调用'}
    if mode not in ('text', 'vision'): raise ProviderError('probe_mode', '验证类型无效')
    if not cfg['model']: raise ProviderError('model_missing', '请先填写或选择模型，再验证生成能力')
    if mode == 'vision' and not cfg['vision']: raise ProviderError('vision_unsupported', '当前接口设置为纯文字模型')
    try: settings.require_prices(cfg)
    except ValueError as exc: raise ProviderError('pricing_missing', str(exc)) from None
    content = [{'type': 'text', 'text': 'Return JSON exactly: {"ok":true}.'}]
    if mode == 'vision':
        content[0]['text'] = 'Calculate the arithmetic expression in the image. Return only a JSON object with the integer field result.'
        content.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(probe_image()).decode()}})
    reserve = (4096 if mode == 'text' else 8192) * cfg['input_price'] + 512 * cfg['output_price']
    if reserve > 1000000: raise ProviderError('probe_budget', '该模型的测试预留超过1元，请降低测试单价配置或直接使用受预算控制的任务')
    endpoint, payload = protocols.request(cfg, 'You validate API connectivity. Return JSON only.', content, 512, False)
    response = _request(endpoint, payload, cfg)
    try: raw, truncated = protocols.output(response, cfg['protocol'])
    except (ValueError, TypeError, KeyError, AttributeError): raise ProviderError('invalid_response', '接口没有返回可解析的文字，请检查协议与输出设置') from None
    if truncated: raise ProviderError('output_truncated', '接口测试输出被截断；请检查模型与推理配置')
    result = parse_output(raw)
    if (mode == 'text' and result.get('ok') is not True) or (mode == 'vision' and result.get('result') != 5):
        raise ProviderError('invalid_output', '接口能返回内容，但未通过合成测试的JSON或图片算式验证')
    usage = protocols.usage(response, cfg['protocol'])
    estimate = math.ceil(usage['prompt_tokens']*cfg['input_price'] + usage['completion_tokens']*cfg['output_price']) / 1000000 if usage else None
    return {'connected': True, 'selected_model': cfg['model'], 'mode': mode, 'json_valid': True,
            'usage': usage, 'estimated_cny': estimate, 'vision': '合成图片算式识别正确，真实试卷效果仍需核对' if mode == 'vision' else '尚未验证图片调用'}


def probe_image():
    """A valid, synthetic PNG spelling 2+3. No user document or image library needed."""
    import struct
    import zlib
    glyphs = [('11111','00001','00001','11111','10000','10000','11111'),
              ('00000','00100','00100','11111','00100','00100','00000'),
              ('11111','00001','00001','11111','00001','00001','11111')]
    width, height, scale = 160, 64, 5
    pixels = [bytearray([255] * (width * 3)) for _ in range(height)]
    for index, glyph in enumerate(glyphs):
        for y, line in enumerate(glyph):
            for x, point in enumerate(line):
                if point == '1':
                    for yy in range(scale):
                        row = pixels[12 + y*scale + yy]
                        for xx in range(scale):
                            at = (18 + index*40 + x*scale + xx) * 3
                            row[at:at+3] = b'\0\0\0'
    def chunk(name, raw):
        return struct.pack('>I', len(raw)) + name + raw + struct.pack('>I', zlib.crc32(name + raw))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b''.join(b'\0'+row for row in pixels))) + chunk(b'IEND', b'')


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
    cfg = settings.for_job(job_id)
    try: settings.require_prices(cfg)
    except ValueError as exc: raise ProviderError('pricing_missing', str(exc)) from None
    if images and not cfg['vision']: raise ProviderError('vision_unsupported', '当前模型设置为纯文字，无法处理题图；请选择支持图片的模型')
    try:snapshots=[(p,Path(p).read_bytes()) for p in images]
    except OSError:raise ProviderError('image_unavailable','无法读取题目配图，请检查原图后重试；本次请求未发出') from None
    cache_input={'system':system,'prompt':prompt,'images':[hashlib.sha256(raw).hexdigest() for _,raw in snapshots],
        'model':cfg['model'],'connection':cfg,'version':PROMPT_VERSION,'thinking':thinking,'max_tokens':max_tokens}
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
    # Reserve conservatively at user-configured CNY/M-token rates; never claim invoice accuracy.
    estimated_input=(len(system.encode())+len(prompt.encode()))*3+len(images)*4096+4096
    reservation=math.ceil(estimated_input*cfg['input_price']+max_tokens*cfg['output_price'])
    with db.transaction() as conn:
        job=conn.execute('SELECT * FROM ai_job WHERE id=?',(job_id,)).fetchone()
        batch=conn.execute('SELECT * FROM ai_batch WHERE id=?',(job['batch_id'],)).fetchone()
        used=conn.execute('SELECT COALESCE(SUM(COALESCE(actual_cost_micro,reserved_cost_micro)),0),count(*) FROM ai_step_run r JOIN ai_job j ON j.id=r.job_id WHERE j.batch_id=?',(job['batch_id'],)).fetchone()
        if used[0]+reservation>batch['max_cost_micro'] or used[1]>=batch['max_requests']:
            raise BudgetPause('budget_paused','达到本批费用或调用上限，已保存进度；可增加预算后继续')
        run_id=db.next_id(conn,'AIR');attempt=conn.execute('SELECT count(*) FROM ai_step_run WHERE job_id=? AND stage=? AND ai_question_id IS ?',(job_id,stage,question_id)).fetchone()[0]+1
        conn.execute('INSERT INTO ai_step_run(id,job_id,ai_question_id,stage,attempt,idempotency_key,state,lease_owner,lease_until,input_hash,model,prompt_version,reserved_cost_micro,billing_state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (run_id,job_id,question_id,stage,attempt,fingerprint+':'+run_id,'running',str(os.getpid()),time.time()+900,fingerprint,cfg['model'],PROMPT_VERSION,reservation,'reserved',db.now()))
    endpoint, payload = protocols.request(cfg, system, content, max_tokens, thinking)
    usage=actual=raw=None
    try:
        response=_request(endpoint,payload,cfg)
        if not isinstance(response,dict):raise ProviderError('invalid_response','模型接口返回的结果结构异常')
        usage=protocols.usage(response,cfg['protocol'])
        if usage:
            actual=math.ceil(usage['prompt_tokens']*cfg['input_price']+usage['completion_tokens']*cfg['output_price'])
        try: raw,truncated=protocols.output(response,cfg['protocol'])
        except (ValueError,TypeError,KeyError,AttributeError):
            raise ProviderError('invalid_response','模型接口没有返回有效正文，已保留进度与费用记录') from None
        if truncated:raise ProviderError('output_truncated','模型输出达到长度上限，请重试或缩小题目范围')
        result=parse_output(raw)
        receipt={'result':result,'raw_content':raw,'model':response.get('model',cfg['model']),'connection':cfg,'usage':usage,
                 'price_basis':'按任务保存的人民币/百万tokens单价估算；缓存、图像与推理计费以服务商账单为准'}
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
