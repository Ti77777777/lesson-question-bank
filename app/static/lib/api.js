/* 接口调用：统一解析 {error:{code,message}}，错误信息原样抛给页面。 */

export class ApiError extends Error {
  constructor(message, code = 'error', status = 0, detail = null) {
    super(message || '请求失败');
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

function buildQuery(query) {
  if (!query) return '';
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) {
      const list = value.filter((v) => v !== undefined && v !== null && v !== '');
      if (list.length) params.set(key, list.join(','));
      return;
    }
    params.set(key, String(value));
  });
  const text = params.toString();
  return text ? `?${text}` : '';
}

export async function api(path, options = {}) {
  const { method = 'GET', body, query, headers = {} } = options;
  const opts = {
    method,
    headers: { Accept: 'application/json', ...headers },
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json; charset=utf-8';
    opts.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path + buildQuery(query), opts);
  } catch (err) {
    throw new ApiError(`无法连接后端服务：${err.message}`, 'network', 0);
  }

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok || (data && data.error)) {
    const message = data?.error?.message
      || (text && text.length <= 400 ? text : '')
      || `请求失败（HTTP ${response.status}）`;
    throw new ApiError(message, data?.error?.code || `http_${response.status}`, response.status, data?.error?.detail);
  }

  if (data === null) {
    throw new ApiError('后端返回了无法解析的内容', 'bad_response', response.status);
  }
  return data;
}

/** 次要数据（标签、资料等）读取失败时可降级，不阻断整页。 */
export async function apiOptional(path, options = {}) {
  try {
    return { data: await api(path, options), error: null };
  } catch (err) {
    return { data: null, error: err };
  }
}

export function fileUrl(relPath) {
  if (!relPath) return '';
  const clean = String(relPath).replace(/\\/g, '/').replace(/^\/+/, '');
  return `/files/${clean.split('/').map(encodeURIComponent).join('/')}`;
}

/** 给 PDF 链接补页码：/files/a.pdf#page=3 */
export function pdfUrl(relPath, page) {
  const base = fileUrl(relPath);
  if (!base) return '';
  const n = Number(page);
  return Number.isFinite(n) && n > 0 ? `${base}#page=${n}` : base;
}
