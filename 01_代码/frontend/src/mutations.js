// Keep the original request until the server gives a definite result. A retry must
// survive navigation, refresh and another request timeout without becoming a new act.
const PREFIX = 'wenxu.mutations.v2.';
const inFlight = new Map();
const signature = value => JSON.stringify(value);
const changed = () => globalThis.window?.dispatchEvent(new Event('wenxu:mutations'));

export function pendingMutations(actorId, storage = globalThis.localStorage) {
  const prefix = PREFIX + actorId + '.';
  const entries = [];
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (key?.startsWith(prefix)) entries.push(JSON.parse(storage.getItem(key)));
  }
  return entries.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

function storageKey(entry) {return PREFIX + entry.actorId + '.' + entry.key;}
function store(entry, storage) {
  // One key per request prevents a second tab from overwriting unrelated pending acts.
  storage.setItem(storageKey(entry), JSON.stringify(entry));
  changed();
}
function remove(entry, storage) {
  storage.removeItem(storageKey(entry));
  changed();
}

export function prepareMutation(actorId, slot, method, path, data, {storage = globalThis.localStorage, uuid = () => crypto.randomUUID(), label = '管理操作'} = {}) {
  if (!actorId) throw new Error('请先登录后继续操作。');
  const entries = pendingMutations(actorId, storage);
  const original = entries.find(entry => entry.slot === slot);
  const request = {method, path, ...(data !== undefined ? {data} : {})};
  if (original) {
    if (signature(original.request) !== signature(request)) {
      const error = new Error('这项操作的上次结果尚未确认。请先在上方确认原操作，再保存新的修改。');
      error.code = 'PENDING_MUTATION';
      throw error;
    }
    return original;
  }
  const entry = {key: uuid(), slot, label, actorId, request: structuredClone(request), createdAt: new Date().toISOString()};
  // If storage cannot persist the key, fail before sending any business request.
  store(entry, storage);
  return entry;
}

export function isUncertainMutation(error) {
  return !error.status || error.status >= 500 || error.status === 401 ||
    ['INVALID_RESPONSE', 'IDEMPOTENCY_IN_PROGRESS', 'IDEMPOTENCY_RESULT_UNKNOWN', 'IDEMPOTENCY_RESULT_UNAVAILABLE'].includes(error.code);
}

export function executeMutation(entry, send, storage = globalThis.localStorage) {
  const id = `${entry.actorId}/${entry.key}`;
  if (inFlight.has(id)) return inFlight.get(id);
  const run = (async () => {
    try {
      const result = await send(entry.request.method, entry.request.path, entry.request.data, {'Idempotency-Key': entry.key});
      remove(entry, storage);
      globalThis.window?.dispatchEvent(new CustomEvent('wenxu:mutation-complete', {detail: {entry, result}}));
      return result;
    } catch (error) {
      if (!isUncertainMutation(error)) remove(entry, storage);
      throw error;
    } finally { inFlight.delete(id); }
  })();
  inFlight.set(id, run);
  return run;
}
