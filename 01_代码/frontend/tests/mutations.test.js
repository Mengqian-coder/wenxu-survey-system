import assert from 'node:assert/strict';
import test from 'node:test';
import {prepareMutation, executeMutation, pendingMutations, isUncertainMutation} from '../src/mutations.js';

class Storage {
  values = new Map();
  get length() {return this.values.size;}
  key(index) {return Array.from(this.values.keys())[index] ?? null;}
  getItem(key) {return this.values.get(key) ?? null;}
  setItem(key, value) {this.values.set(key, value);}
  removeItem(key) {this.values.delete(key);}
}
const key = '126f4964-34d1-4bf6-bfca-d2e46e7db267';
const prepare = (storage, data = {revision: 2, sourceVersionId: 'v1'}, actor = 'alice', uuid = key) => prepareMutation(actor, 'restore', 'POST', '/questions/q/restore', data, {storage, uuid: () => uuid});

test('双击共享同一请求和Promise，成功后才移除pending', async () => {
  const storage = new Storage(), entry = prepare(storage);
  let calls = 0, finish;
  const send = async (method, path, body, headers) => {calls++; assert.equal(headers['Idempotency-Key'], key); assert.equal(body.revision, 2); await new Promise(resolve => {finish = resolve;}); return {version: 'v4'};};
  const first = executeMutation(entry, send, storage), second = executeMutation(prepare(storage), send, storage);
  assert.equal(first, second); assert.equal(calls, 1); assert.equal(pendingMutations('alice', storage).length, 1);
  finish(); assert.deepEqual(await first, {version: 'v4'}); assert.equal(pendingMutations('alice', storage).length, 0);
});

test('响应丢失、刷新、重新登录仍重用原key和原revision，不发送修改后的意图', async () => {
  const storage = new Storage(), entry = prepare(storage);
  await assert.rejects(executeMutation(entry, async () => {throw Object.assign(new Error('timeout'), {status: 0});}, storage));
  assert.equal(pendingMutations('alice', storage)[0].key, key);
  const restored = prepare(storage); assert.equal(restored.key, key); assert.equal(restored.request.data.revision, 2);
  assert.throws(() => prepare(storage, {revision: 3, sourceVersionId: 'v1'}), error => error.code === 'PENDING_MUTATION');
  await executeMutation(restored, async (_method, _path, body) => {assert.equal(body.revision, 2); return {version: 'v4'};}, storage);
  assert.deepEqual(pendingMutations('alice', storage), []);
});

test('不同账号pending隔离，失败回滚后才允许明确的新操作生成新key', async () => {
  const storage = new Storage(), entry = prepare(storage);
  assert.deepEqual(pendingMutations('bob', storage), []);
  assert.notEqual(prepare(storage, undefined, 'bob', '226f4964-34d1-4bf6-bfca-d2e46e7db267').key, key);
  await assert.rejects(executeMutation(entry, async () => {throw Object.assign(new Error('revision'), {status: 409, code: 'REVISION_CONFLICT'});}, storage));
  assert.equal(pendingMutations('alice', storage).length, 0);
  assert.notEqual(prepare(storage, undefined, 'alice', '326f4964-34d1-4bf6-bfca-d2e46e7db267').key, key);
});

test('处理中、未知提交、服务异常和401保留确认凭据；明确422结束该意图', () => {
  for (const error of [{status: 409, code: 'IDEMPOTENCY_IN_PROGRESS'}, {status: 503, code: 'IDEMPOTENCY_RESULT_UNKNOWN'}, {status: 503, code: 'IDEMPOTENCY_RESULT_UNAVAILABLE'}, {status: 500}, {status: 401}, {status: 200, code: 'INVALID_RESPONSE'}]) assert.equal(isUncertainMutation(error), true);
  assert.equal(isUncertainMutation({status: 422, code: 'INVALID_REQUEST'}), false);
});

test('无法持久化key时在发出请求前失败，避免刷新后重建操作', () => {
  const storage = {length: 0, getItem: () => null, setItem: () => {throw new Error('storage unavailable');}};
  assert.throws(() => prepare(storage), /storage unavailable/);
});

test('独立pending条目避免其他标签页清理成功请求时覆盖尚未确认的请求', async () => {
  const storage = new Storage(), first = prepare(storage);
  const second = prepareMutation('alice', 'create', 'POST', '/questions', {content: {}}, {storage, uuid: () => '426f4964-34d1-4bf6-bfca-d2e46e7db267'});
  assert.equal(pendingMutations('alice', storage).length, 2);
  await executeMutation(first, async () => ({}), storage);
  assert.deepEqual(pendingMutations('alice', storage).map(item => item.key), [second.key]);
});
