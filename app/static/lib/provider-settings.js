import { api } from './api.js';
import { esc } from './dom.js';
import { flash } from './ui.js';

const PROTOCOLS = { chat: 'OpenAI兼容（Chat Completions）', responses: 'OpenAI Responses', anthropic: 'Anthropic Messages', gemini: 'Google Gemini 原生' };

export async function mountProviderSettings(root, onChanged = () => {}) {
  const state = await api('/api/ai/settings');
  let cfg = state.config, configured = state.configured;
  const options = (values, selected) => Object.entries(values).map(([v,t]) => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(t)}</option>`).join('');
  function render() {
    root.innerHTML = `<div class="provider-grid">
      <label class="field"><span>服务商预设</span><select data-config="preset">${options(Object.fromEntries(state.presets.map(p => [p.id,p.label])), cfg.preset)}</select></label>
      <label class="field"><span>接口协议</span><select data-config="protocol">${options(PROTOCOLS,cfg.protocol)}</select></label>
      <label class="field provider-wide"><span>服务地址（Base URL，可修改为中转或私有部署地址）</span><input data-config="base_url" value="${esc(cfg.base_url)}" placeholder="https://…/v1" autocomplete="off"></label>
      <label class="field"><span>模型名称</span><input data-config="model" value="${esc(cfg.model)}" list="provider-models" placeholder="按服务商控制台填写，或读取模型列表" autocomplete="off"><datalist id="provider-models"></datalist></label>
      <label class="field"><span data-key-label>API密钥：${cfg.auth_mode === 'none' ? '无需密钥' : configured ? '当前地址已有密钥' : '未配置'}</span><input data-key type="password" autocomplete="new-password" placeholder="留空保留当前地址的密钥；不同地址分别保存"></label>
      <label class="field"><span>输入单价（元 / 百万tokens）</span><input data-config="input_price" type="number" min="0" step="0.01" value="${cfg.input_price ?? ''}" placeholder="按服务商价格换算成人民币"></label>
      <label class="field"><span>输出单价（元 / 百万tokens）</span><input data-config="output_price" type="number" min="0" step="0.01" value="${cfg.output_price ?? ''}" placeholder="用于任务预算预留，非账单"></label>
    </div><div class="provider-switches"><label><input type="checkbox" data-config="vision" ${cfg.vision ? 'checked' : ''}>模型支持图片输入</label><label><input type="checkbox" data-config="json_mode" ${cfg.json_mode ? 'checked' : ''}>接口支持JSON输出参数</label></div>
    <details class="provider-advanced"><summary>高级设置（认证、推理、输出参数）</summary><div class="provider-grid">
      <label class="field"><span>密钥环境变量名（跨平台）</span><input data-config="key_env" value="${esc(cfg.key_env)}"></label>
      <label class="field"><span>认证方式</span><select data-config="auth_mode">${options({ bearer: '标准密钥（自动适配各协议）', 'api-key': 'api-key请求头（如Azure）', none: '不需要密钥（本地服务）' }, cfg.auth_mode)}</select></label>
      <label class="field"><span>推理参数</span><select data-config="reasoning">${options({ none: '使用模型默认行为', deepseek: 'DeepSeek thinking参数', high: 'OpenAI reasoning_effort / reasoning' }, cfg.reasoning)}</select></label>
      <label class="field"><span>Chat输出长度字段</span><select data-config="output_token_field">${options({ max_tokens: 'max_tokens（通用）', max_completion_tokens: 'max_completion_tokens（部分OpenAI模型）' }, cfg.output_token_field)}</select></label>
      <label class="field"><span>请求超时（秒）</span><input data-config="timeout" type="number" min="10" max="600" value="${cfg.timeout}"></label>
    </div></details>
    <div class="provider-actions"><button class="btn btn-primary" data-provider-save>保存接口与密钥</button><button class="btn" data-provider-test="models">读取模型列表</button><button class="btn" data-provider-test="text">验证文字与JSON</button><button class="btn" data-provider-test="vision">验证图片接口</button></div>
    <p class="hint">文字和图片验证可能计费。</p><details class="compact-fold"><summary>连接与费用说明</summary><div class="compact-fold-body"><p class="hint">读取模型列表不生成内容。文字和图片验证会发送合成测试内容，可能计费，按所填单价预留不超过1元；不发送你的试卷。每个服务地址的密钥分别加密保存，页面不会显示已存密钥。</p>
    <p class="hint">自定义兼容接口可用于其他服务商、Azure OpenAI、局域网网关或本地模型；协议与模型能力以该服务的实际支持为准。只读模型不代表具备识图能力，请分别验证。</p></div></details><div data-provider-result role="status"></div>`;
    root.querySelector('[data-config="preset"]').onchange = e => {
      cfg = structuredClone(state.presets.find(p => p.id === e.target.value).config); configured = false; render();
      result('已切换预设，请填写模型和单价后保存。原密钥仍保存在原服务地址下。');
    };
    root.querySelector('[data-provider-save]').onclick = async () => { try { await save(); result('接口设置已保存。新任务使用这些设置；已有任务保留原接口配置。'); } catch(e) { result(e.message,true); } };
    root.querySelectorAll('[data-provider-test]').forEach(button => button.onclick = async () => {
      const buttons = root.querySelectorAll('button'); buttons.forEach(b => b.disabled = true);
      try {
        await save(); result(button.dataset.providerTest === 'models' ? '正在读取模型…' : '正在发送合成测试内容…');
        const data = await api('/api/ai/probe', { method: 'POST', body: { mode: button.dataset.providerTest } });
        if (data.models) {
          root.querySelector('datalist').innerHTML = data.models.map(m => `<option value="${esc(m)}"></option>`).join('');
          result(`连接成功，读取${data.models.length}个模型。可在模型输入框中选择；列表不代表模型已通过文字或图片测试。`);
        } else result(`${data.mode === 'vision' ? data.vision : '文字与JSON验证通过'}，${data.selected_model}${data.estimated_cny != null ? `，本次估算¥${data.estimated_cny.toFixed(4)}` : '，本次费用未知，以服务商账单为准'}`);
      } catch(e) { result(e.message + (button.dataset.providerTest === 'models' ? '；若服务不提供模型列表，可手填模型后验证。' : ''),true); }
      finally { buttons.forEach(b => b.disabled = false); }
    });
  }
  function result(text, error = false) { const el = root.querySelector('[data-provider-result]'); el.textContent = text; el.className = error ? 'modal-error' : 'provider-result'; }
  async function save() {
    root.querySelectorAll('[data-config]').forEach(input => {
      cfg[input.dataset.config] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value.trim() ? Number(input.value) : null) : input.value.trim();
    });
    if (['anthropic','gemini'].includes(cfg.protocol) || (cfg.protocol !== 'chat' && cfg.reasoning === 'deepseek')) cfg.reasoning = 'none';
    const keyInput = root.querySelector('[data-key]');
    const res = await api('/api/ai/settings', { method: 'POST', body: { config: cfg, key: keyInput.value } });
    cfg = res.config; configured = res.configured; keyInput.value = '';
    root.querySelector('[data-key-label]').textContent = 'API密钥：' + (cfg.auth_mode === 'none' ? '无需密钥' : configured ? '当前地址已有密钥' : '未配置');
    await onChanged();
  }
  render();
}
