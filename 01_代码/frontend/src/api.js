const BASE = '/api';

export class ApiError extends Error {
  constructor(message, {status = 0, code = 'NETWORK_ERROR', details = null} = {}) {
    super(message);
    this.name = 'ApiError';
    Object.assign(this, {status, code, details});
  }
}

async function request(method, path, data) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  const headers = {Accept: 'application/json'};
  const token = sessionStorage.getItem('wenxu.access');
  if (token) headers.Authorization = `Bearer ${token}`;
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  try {
    const response = await fetch(BASE + path, {
      method, headers, signal: controller.signal,
      ...(data !== undefined ? {body: JSON.stringify(data)} : {}),
    });
    let result;
    try { result = await response.json(); }
    catch { throw new ApiError('服务暂时没有返回有效数据，请确认后端已启动。', {status: response.status, code: 'INVALID_RESPONSE'}); }
    if (!response.ok) {
      if (response.status === 401 && result.error?.code !== 'INVALID_SUBMISSION_TOKEN' && !path.startsWith('/auth/login') && !path.startsWith('/auth/register')) {
        window.dispatchEvent(new Event('wenxu:unauthorized'));
      }
      throw new ApiError(result.error?.message || '操作未完成，请重试。', {
        status: response.status, code: result.error?.code, details: result.error?.details,
      });
    }
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(error.name === 'AbortError'
      ? '请求超时。提交问卷时请保留当前页面，并使用原提交重试。'
      : '暂时无法连接服务，请检查网络及后端运行状态。');
  } finally { clearTimeout(timer); }
}

export const api = {
  get: path => request('GET', path),
  post: (path, data) => request('POST', path, data),
  patch: (path, data) => request('PATCH', path, data),
};

export const dateTime = value => value ? new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
}).format(new Date(value)) : '未设置';
export const typeNames = {single: '单选题', multiple: '多选题', text: '文本填空', number: '数字填空'};
