import assert from 'node:assert/strict';
import test from 'node:test';
import {loadBanks, mutationTarget, selectableQuestion, versionSourceLabel} from '../src/management-utils.js';

const entry = path => ({request: {path}});
test('题库管理的成功回执打开当前题库，删除返回列表，不误跳问卷或题目', () => {
  assert.equal(mutationTarget(entry('/question-banks'), {_id: 'bank1'}), '/question-banks/bank1');
  assert.equal(mutationTarget(entry('/question-banks/bank1'), {_id: 'bank1'}), '/question-banks/bank1');
  assert.equal(mutationTarget(entry('/question-banks/bank1/items/question1'), {bankId: 'bank1', questionId: 'question1', created: true}), '/question-banks/bank1');
  assert.equal(mutationTarget(entry('/question-banks/bank1/items/question1'), {bankId: 'bank1', questionId: 'question1', removed: true}), '/question-banks/bank1');
  assert.equal(mutationTarget(entry('/question-banks/bank1?revision=9'), {bankId: 'bank1', deleted: true}), '/question-banks');
});
test('题目副本回执跳转新身份，共享回执保留原题目，问卷回执正确导航', () => {
  assert.equal(mutationTarget(entry('/questions/original/copies'), {question: {_id: 'copy'}}), '/questions/copy');
  assert.equal(mutationTarget(entry('/questions/original/shares'), {share: {_id: 'share'}}), '/questions/original');
  assert.equal(mutationTarget(entry('/surveys'), {_id: 'survey'}), '/surveys/survey/edit');
  assert.equal(mutationTarget(entry('/unknown'), {_id: 'item'}), null);
});
test('失效题库占位、归档题目和无使用权限的题目不能从选题器新增使用', () => {
  assert.equal(selectableQuestion({available: false}), false);
  assert.equal(selectableQuestion({archivedAt: '2026-10-22'}), false);
  assert.equal(selectableQuestion({permissions: {canUse: false}}), false);
  assert.equal(selectableQuestion({available: true, permissions: {canUse: true}}), true);
});
test('恢复版本的内容来源按baseVersionId定位，避免误显示成上一编号', () => {
  assert.equal(versionSourceLabel({baseVersionId: 'v1'}, [{_id: 'v1', versionNo: 1}, {_id: 'v3', versionNo: 3}]), '内容来源：v1');
  assert.equal(versionSourceLabel({baseVersionId: 'old'}, []), '内容来源：历史版本');
  assert.equal(versionSourceLabel({baseVersionId: null}), '');
});
test('题库选项加载所有分页，避免只能选用第一百个以内的分类', async () => {
  const calls = [];
  const values = await loadBanks(async path => {calls.push(path); const page = Number(new URLSearchParams(path.split('?')[1]).get('page')); return {items: page === 1 ? Array.from({length: 100}, (_, index) => ({_id: String(index)})) : [{_id: 'last'}], total: 101};});
  assert.equal(values.length, 101); assert.equal(values.at(-1)._id, 'last'); assert.equal(calls.length, 2);
});
