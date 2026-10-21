import assert from 'node:assert/strict';
import test from 'node:test';
import { answerInput, answerLabel, answersEqual, canonicalAnswer, questionHint } from '../src/fill-utils.js';

const choices = [{ id: 'a', label: '课堂讲解' }, { id: 'b', label: '上机实践' }];
const single = { type: 'single', options: choices, required: true };
const multiple = { type: 'multiple', options: choices, required: true };
const text = { type: 'text', validation: { maxLength: 100 }, required: false };
const number = { type: 'number', validation: { integerOnly: false }, required: true };

test('文本首尾空白标准化，空答案统一为 null，保留 Unicode 内容', () => {
  assert.equal(canonicalAnswer(text, '  建议增加 🍀 实验\n'), '建议增加 🍀 实验');
  assert.equal(canonicalAnswer(text, '\n\t '), null);
  assert.equal(canonicalAnswer(text, null), null);
});

test('单选只能引用现有选项，空值交由后端判断是否必答', () => {
  assert.equal(canonicalAnswer(single, 'a'), 'a');
  assert.equal(canonicalAnswer(single, ''), null);
  assert.throws(() => canonicalAnswer(single, 'missing'), /选项/);
});

test('多选按选项定义顺序标准化且不修改输入数组', () => {
  const selected = ['b', 'a'];
  assert.deepEqual(canonicalAnswer(multiple, selected), ['a', 'b']);
  assert.deepEqual(selected, ['b', 'a']);
  assert.equal(canonicalAnswer(multiple, []), null);
  assert.throws(() => canonicalAnswer(multiple, ['a', 'a']), /重复/);
  assert.throws(() => canonicalAnswer(multiple, ['missing']), /失效/);
  assert.throws(() => canonicalAnswer(multiple, 'a'), /选项/);
});

test('数字接受十进制和科学计数法，空白转为 null，负零统一为零', () => {
  assert.equal(canonicalAnswer(number, ' 2.50 '), 2.5);
  assert.equal(canonicalAnswer(number, '1e3'), 1000);
  assert.equal(canonicalAnswer(number, ''), null);
  assert.equal(Object.is(canonicalAnswer(number, '-0'), -0), false);
});

for (const value of ['NaN', 'Infinity', '1e999', '0x10', '12abc', true]) {
  test(`数字拒绝非法输入 ${String(value)}`, () => {
    assert.throws(() => canonicalAnswer(number, value), /数字/);
  });
}

test('整数模式拒绝小数和不安全整数，接受安全边界', () => {
  const integer = { ...number, validation: { integerOnly: true } };
  assert.throws(() => canonicalAnswer(integer, '2.5'), /整数/);
  assert.throws(() => canonicalAnswer(integer, '9007199254740992'), /整数/);
  assert.equal(canonicalAnswer(integer, '9007199254740991'), Number.MAX_SAFE_INTEGER);
});

test('仅调整多选顺序、文本首尾空白和等值数字均不算修改答案', () => {
  assert.equal(answersEqual(multiple, ['b', 'a'], ['a', 'b']), true);
  assert.equal(answersEqual(text, '  建议 ', '建议'), true);
  assert.equal(answersEqual(number, '02.00', 2), true);
  assert.equal(answersEqual(single, 'a', 'b'), false);
  assert.equal(answersEqual(number, 'invalid', 2), false);
});

test('返回题目时正确恢复控件输入，不共享多选数组引用', () => {
  const saved = ['a'];
  const restored = answerInput(multiple, saved);
  restored.push('b');
  assert.deepEqual(saved, ['a']);
  assert.equal(answerInput(number, 5), '5');
  assert.equal(answerInput(number, null), '');
  assert.deepEqual(answerInput(multiple, null), []);
  assert.equal(answerInput(text, null), '');
});

test('提交确认页显示选项文字、数字与选填留空含义', () => {
  assert.equal(answerLabel(single, 'a'), '课堂讲解');
  assert.equal(answerLabel(multiple, ['b', 'a']), '课堂讲解、上机实践');
  assert.equal(answerLabel(number, 0), '0');
  assert.equal(answerLabel(text, null), '未填写（选填）');
});

test('填写提示包含数量、长度及整数范围边界', () => {
  assert.match(questionHint({ ...multiple, validation: { minSelections: 2, maxSelections: 2 } }), /选择 2 项/);
  assert.match(questionHint(text), /100/);
  assert.match(questionHint({ ...number, validation: { integerOnly: true, min: 0, max: 168 } }), /0～168.*整数.*包含边界/);
});
