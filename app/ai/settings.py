"""Non-secret connection settings. Keys live in the credential store, never here."""
import math
import re
from urllib.parse import urlsplit
from app import db

PRESETS = {
    'deepseek': {'label': 'DeepSeek', 'base_url': 'https://api.deepseek.com', 'protocol': 'chat', 'model': 'deepseek-flash', 'key_env': 'DEEPSEEK_API_KEY', 'input_price': 2, 'output_price': 8, 'reasoning': 'deepseek'},
    'openai': {'label': 'OpenAI', 'base_url': 'https://api.openai.com/v1', 'protocol': 'responses', 'key_env': 'OPENAI_API_KEY'},
    'anthropic': {'label': 'Anthropic / Claude', 'base_url': 'https://api.anthropic.com/v1', 'protocol': 'anthropic', 'key_env': 'ANTHROPIC_API_KEY'},
    'gemini': {'label': 'Google Gemini', 'base_url': 'https://generativelanguage.googleapis.com/v1beta', 'protocol': 'gemini', 'key_env': 'GEMINI_API_KEY'},
    'qwen': {'label': '通义千问 / 百炼', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'protocol': 'chat', 'key_env': 'DASHSCOPE_API_KEY', 'json_mode': False},
    'siliconflow': {'label': '硅基流动', 'base_url': 'https://api.siliconflow.cn/v1', 'protocol': 'chat', 'key_env': 'SILICONFLOW_API_KEY'},
    'ollama': {'label': 'Ollama（本地）', 'base_url': 'http://localhost:11434/v1', 'protocol': 'chat', 'key_env': 'QBANK_AI_KEY', 'auth_mode': 'none', 'input_price': 0, 'output_price': 0, 'json_mode': False},
    'lmstudio': {'label': 'LM Studio（本地）', 'base_url': 'http://localhost:1234/v1', 'protocol': 'chat', 'key_env': 'QBANK_AI_KEY', 'auth_mode': 'none', 'input_price': 0, 'output_price': 0, 'json_mode': False},
    'custom': {'label': '自定义 / OpenAI兼容 / 中转服务', 'base_url': '', 'protocol': 'chat', 'key_env': 'QBANK_AI_KEY', 'json_mode': False},
}
DEFAULTS = {'preset': 'deepseek', 'protocol': 'chat', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
            'key_env': 'DEEPSEEK_API_KEY', 'auth_mode': 'bearer', 'vision': True, 'json_mode': True,
            'reasoning': 'none', 'output_token_field': 'max_tokens', 'input_price': None, 'output_price': None, 'timeout': 240}


def preset(name):
    if not isinstance(name, str) or name not in PRESETS: raise ValueError('不支持的服务商预设')
    return {**DEFAULTS, 'model': '', **{k: v for k, v in PRESETS[name].items() if k != 'label'}, 'preset': name}


def validate(value, require_model=True):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS): raise ValueError('接口设置包含不支持的字段')
    cfg = {**preset(value.get('preset', 'deepseek')), **value}
    if cfg['protocol'] not in ('chat', 'responses', 'anthropic', 'gemini'): raise ValueError('接口协议无效')
    if cfg['auth_mode'] not in ('bearer', 'api-key', 'none'): raise ValueError('密钥认证方式无效')
    if cfg['reasoning'] not in ('none', 'deepseek', 'high'): raise ValueError('推理参数无效')
    if (cfg['reasoning'] == 'deepseek' and cfg['protocol'] != 'chat') or (cfg['reasoning'] == 'high' and cfg['protocol'] not in ('chat', 'responses')):
        raise ValueError('推理参数与协议不匹配，请选择使用模型默认行为')
    if cfg['output_token_field'] not in ('max_tokens', 'max_completion_tokens'): raise ValueError('输出长度参数无效')
    url = cfg['base_url']
    if not isinstance(url, str) or len(url) > 500: raise ValueError('接口地址无效')
    try:
        parts = urlsplit(url.strip()); port = parts.port
    except ValueError: raise ValueError('接口地址无效') from None
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in url):
        raise ValueError('接口地址须为不含账号、密钥、查询参数或片段的 Base URL')
    local = parts.hostname in ('localhost', '127.0.0.1', '::1')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        raise ValueError('云端接口须使用 HTTPS；本机模型可使用 HTTP')
    if any(parts.path.rstrip('/').endswith(s) for s in ('/chat/completions', '/responses', '/messages', ':generateContent')):
        raise ValueError('请填写 Base URL，不要包含 chat/completions、responses 或 messages 路径')
    cfg['base_url'] = url.strip().rstrip('/')
    for k in ('model', 'key_env'):
        if not isinstance(cfg[k], str) or len(cfg[k]) > 180 or any(c.isspace() or ord(c) < 32 for c in cfg[k]): raise ValueError('模型名或环境变量名无效')
    if require_model and not cfg['model']: raise ValueError('请填写或选择模型名称')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', cfg['key_env']): raise ValueError('密钥环境变量名须为大写英文、数字或下划线')
    for k in ('vision', 'json_mode'):
        if type(cfg[k]) is not bool: raise ValueError('模型能力开关须为布尔值')
    for k in ('input_price', 'output_price'):
        v = cfg[k]
        if v is not None and (type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 100000):
            raise ValueError('单价须为非负的人民币/百万tokens数值')
    if type(cfg['timeout']) is not int or not 10 <= cfg['timeout'] <= 600: raise ValueError('超时须为10至600秒')
    return cfg


def current():
    stored = db.one("SELECT value FROM meta WHERE key='ai_connection'")
    import json
    return validate(json.loads(stored['value']), require_model=False) if stored else preset('deepseek')


def save(value):
    cfg = validate(value, require_model=False)
    with db.transaction() as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES('ai_connection',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (db.dumps(cfg),))
    return cfg


def for_job(job_id):
    import json
    row = db.one('SELECT b.config_json FROM ai_batch b JOIN ai_job j ON j.batch_id=b.id WHERE j.id=?', (job_id,))
    config = json.loads(row['config_json']) if row else {}
    return validate(config['connection'] if config.get('connection') else current())


def require_prices(cfg):
    if cfg['input_price'] is None or cfg['output_price'] is None:
        raise ValueError('请先填写当前模型的输入与输出单价（人民币/百万tokens），再开始处理，以便控制预算')
