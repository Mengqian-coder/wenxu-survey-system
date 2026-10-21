import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildSurveyPayload, codePointLength, createQuestion, EditorValidationError,
  invalidJumpTargets, moveItem, removeInvalidJumpTargets, removeQuestionOption,
  surveyToForm,
} from '../src/editor-utils.js';

const target = questionId => ({ kind: 'question', questionId });
const rule = (id, questionId, value = 'a') => ({ id, condition: { operator: 'eq', value }, target: target(questionId) });
const simple = (id, jumpRules = []) => ({
  id, type: 'single', title: `问题 ${id}`, description: '', required: true,
  options: [{ id: 'a', label: '选项 A' }, { id: 'b', label: '选项 B' }], validation: {}, jumpRules,
});
const form = questions => ({
  title: '课程体验', description: '谢谢参与', settings: { anonymous: true, allowRepeat: false }, deadline: '', questions,
});

test('创建与调整顺序保持稳定 ID，不修改原数组', () => {
  const first = createQuestion('single'), second = createQuestion('number');
  assert.match(first.id, /^[A-Za-z][A-Za-z0-9_-]{0,63}$/);
  assert.notEqual(first.id, second.id);
  const original = [first, second];
  assert.deepEqual(moveItem(original, 0, 1).map(item => item.id), [second.id, first.id]);
  assert.deepEqual(original.map(item => item.id), [first.id, second.id]);
});

test('从服务器加载时深复制题目且保留所有题目及选项 ID', () => {
  const survey = { title: '已有问卷', questions: [simple('q1')], settings: { anonymous: false, allowRepeat: true }, deadlineAt: null };
  const loaded = surveyToForm(survey);
  assert.deepEqual(loaded.questions, survey.questions);
  loaded.questions[0].options[0].label = '本地修改';
  assert.equal(survey.questions[0].options[0].label, '选项 A');
  assert.equal(loaded.settings.anonymous, false);
  assert.equal(loaded.deadline, '');
});

test('删除失效跳转时以所属题目和规则 ID 联合识别，保留其他题的同名规则', () => {
  const questions = [simple('q1', [rule('shared', 'q3')]), simple('q2', [rule('shared', 'q1')]), simple('q3')];
  assert.deepEqual(invalidJumpTargets(questions).map(item => item.questionId), ['q2']);
  const cleaned = removeInvalidJumpTargets(questions);
  assert.equal(cleaned[0].jumpRules.length, 1);
  assert.equal(cleaned[1].jumpRules.length, 0);
  assert.equal(questions[1].jumpRules.length, 1);
});

test('删除选项只移除引用该选项的规则', () => {
  const question = simple('q1', [rule('ra', 'q3', 'a'), rule('rb', 'q3', 'b')]);
  const changed = removeQuestionOption(question, 'a');
  assert.deepEqual(changed.options.map(option => option.id), ['b']);
  assert.deepEqual(changed.jumpRules.map(item => item.id), ['rb']);
  assert.equal(question.options.length, 2);
});

test('表单将字符串限制转换为严格数字并省略未配置上下限', () => {
  const number = createQuestion('number');
  number.title = '每周学习小时';
  number.validation = { integerOnly: true, min: '0', max: '' };
  const payload = buildSurveyPayload(form([number]), { publish: true });
  assert.deepEqual(payload.questions[0].validation, { integerOnly: true, min: 0 });
  assert.equal(payload.deadlineAt, null);
  assert.equal(payload.questions[0].id, number.id);
});

test('已发布问卷仅生成允许修改的字段，不携带锁定题目和身份配置', () => {
  const payload = buildSurveyPayload({ ...form([simple('q1')]), title: '  新标题  ' }, { locked: true });
  assert.deepEqual(payload, { title: '新标题', description: '谢谢参与', deadlineAt: null });
});

test('标题容量按 Unicode 码点计算，发布空问卷和向前跳转均被拒绝', () => {
  assert.equal(codePointLength('🍀'.repeat(200)), 200);
  assert.equal(buildSurveyPayload({ ...form([simple('q1')]), title: '🍀'.repeat(200) }).title.length, 400);
  assert.throws(() => buildSurveyPayload({ ...form([simple('q1')]), title: '🍀'.repeat(201) }), EditorValidationError);
  assert.throws(() => buildSurveyPayload(form([]), { publish: true }), /至少添加一道题目/);
  assert.throws(() => buildSurveyPayload(form([simple('q1'), simple('q2', [rule('back', 'q1')])])), /后面的题目/);
});
