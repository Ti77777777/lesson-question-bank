"""Wire adapters; canonical input images use OpenAI data URLs internally."""
from urllib.parse import quote


def _image(block):
    url = block['image_url']['url']
    prefix, encoded = url.split(',', 1)
    return prefix[5:].split(';')[0], encoded


def request(cfg, system, content, max_tokens, thinking):
    protocol = cfg['protocol']
    instruction = system + '\nReturn exactly one JSON object, without Markdown fences.'
    if protocol == 'chat':
        payload = {'model': cfg['model'], 'messages': [{'role': 'system', 'content': instruction}, {'role': 'user', 'content': content}], cfg['output_token_field']: max_tokens, 'stream': False}
        if cfg['json_mode']: payload['response_format'] = {'type': 'json_object'}
        if cfg['reasoning'] == 'deepseek':
            payload['thinking'] = {'type': 'enabled' if thinking else 'disabled'}
            if thinking: payload['reasoning_effort'] = 'high'
        elif cfg['reasoning'] == 'high' and thinking: payload['reasoning_effort'] = 'high'
        return '/chat/completions', payload
    if protocol == 'responses':
        blocks = [{'type': 'input_text', 'text': c['text']} if c['type'] == 'text' else {'type': 'input_image', 'image_url': c['image_url']['url']} for c in content]
        payload = {'model': cfg['model'], 'instructions': instruction, 'input': [{'role': 'user', 'content': blocks}], 'max_output_tokens': max_tokens, 'store': False, 'stream': False}
        if cfg['json_mode']: payload['text'] = {'format': {'type': 'json_object'}}
        if cfg['reasoning'] == 'high' and thinking: payload['reasoning'] = {'effort': 'high'}
        return '/responses', payload
    if protocol == 'anthropic':
        blocks = []
        for c in content:
            if c['type'] == 'text': blocks.append(c)
            else:
                mime, data = _image(c)
                blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': data}})
        return '/messages', {'model': cfg['model'], 'system': instruction, 'messages': [{'role': 'user', 'content': blocks}], 'max_tokens': max_tokens, 'stream': False}
    if protocol == 'gemini':
        blocks = []
        for c in content:
            if c['type'] == 'text': blocks.append({'text': c['text']})
            else:
                mime, data = _image(c)
                blocks.append({'inlineData': {'mimeType': mime, 'data': data}})
        generation = {'maxOutputTokens': max_tokens}
        if cfg['json_mode']: generation['responseMimeType'] = 'application/json'
        return '/models/' + quote(cfg['model'].removeprefix('models/'), safe='') + ':generateContent', {
            'systemInstruction': {'parts': [{'text': instruction}]}, 'contents': [{'role': 'user', 'parts': blocks}], 'generationConfig': generation}
    raise ValueError('Unknown protocol')


def usage(response, protocol):
    source = response.get('usageMetadata' if protocol == 'gemini' else 'usage')
    if not isinstance(source, dict): return None
    if protocol == 'chat': keys = ('prompt_tokens', 'completion_tokens')
    elif protocol == 'gemini': keys = ('promptTokenCount', 'candidatesTokenCount')
    else: keys = ('input_tokens', 'output_tokens')
    values = [source.get(k) for k in keys]
    if any(type(v) is not int or v < 0 for v in values): return None
    if protocol == 'gemini':
        extra = source.get('thoughtsTokenCount', 0)
        if type(extra) is not int or extra < 0: return None
        values[1] += extra
    if protocol == 'anthropic':
        for k in ('cache_read_input_tokens', 'cache_creation_input_tokens'):
            extra = source.get(k, 0)
            if type(extra) is not int or extra < 0: return None
            values[0] += extra
    return {'prompt_tokens': values[0], 'completion_tokens': values[1], 'original': source}


def output(response, protocol):
    truncated = False
    if protocol == 'chat':
        choices = response.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): raise ValueError()
        item = choices[0]; truncated = item.get('finish_reason') == 'length'
        body = item.get('message')
        raw = body.get('content') if isinstance(body, dict) else None
        if isinstance(raw, list): raw = ''.join(b.get('text', '') for b in raw if isinstance(b, dict) and b.get('type') == 'text')
    elif protocol == 'responses':
        blocks = []
        for item in response.get('output', []):
            if not isinstance(item, dict) or item.get('type') != 'message': continue
            blocks += [b.get('text', '') for b in item.get('content', []) if isinstance(b, dict) and b.get('type') == 'output_text']
        raw = ''.join(blocks)
        truncated = response.get('status') == 'incomplete'
    elif protocol == 'anthropic':
        raw = ''.join(b.get('text', '') for b in response.get('content', []) if isinstance(b, dict) and b.get('type') == 'text')
        truncated = response.get('stop_reason') == 'max_tokens'
    elif protocol == 'gemini':
        candidates = response.get('candidates')
        if not isinstance(candidates, list) or not candidates: raise ValueError()
        item = candidates[0]
        raw = ''.join(b.get('text', '') for b in item.get('content', {}).get('parts', []) if isinstance(b, dict) and not b.get('thought'))
        truncated = item.get('finishReason') == 'MAX_TOKENS'
    else: raise ValueError()
    if not isinstance(raw, str) or not raw.strip(): raise ValueError()
    return raw, truncated
