/** Pure editor operations. IDs survive every edit and array reordering. */
export const QUESTION_TYPES = [
  { value: 'single', label: '单选题', short: '单选', icon: 'list', description: '从多个选项中选择一项' },
  { value: 'multiple', label: '多选题', short: '多选', icon: 'check', description: '支持限制选择的数量' },
  { value: 'text', label: '文本填空', short: '文本', icon: 'pencil', description: '收集文字与开放式建议' },
  { value: 'number', label: '数字填空', short: '数字', icon: 'chart', description: '收集数字与整数答案' },
];

export const MAX_QUESTIONS = 100;
export const MAX_OPTIONS = 50;
export const MAX_RULES = 20;
export const codePointLength = (value = '') => Array.from(String(value)).length;

export function makeId(prefix = 'q') {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return `${prefix}_${globalThis.crypto.randomUUID().replaceAll('-', '')}`;
  }
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `${prefix}_${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`;
}

export function createOption(label = '') {
  return { id: makeId('opt'), label };
}

export function createQuestion(type = 'single') {
  if (!QUESTION_TYPES.some((item) => item.value === type)) throw new Error('不支持的题型');
  const question = {
    id: makeId('q'), type, title: '', description: '', required: true,
    validation: type === 'number' ? { integerOnly: false } : {}, jumpRules: [],
  };
  if (type === 'single' || type === 'multiple') {
    question.options = [createOption('选项 1'), createOption('选项 2')];
  }
  return question;
}

export function createRule(question) {
  let condition;
  if (question.type === 'single') condition = { operator: 'eq', value: question.options[0]?.id || '' };
  else if (question.type === 'multiple') condition = { operator: 'containsAny', values: question.options[0] ? [question.options[0].id] : [] };
  else if (question.type === 'number') condition = { operator: 'gte', value: '' };
  else throw new Error('文本题不支持跳转');
  return { id: makeId('rule'), condition, target: { kind: 'end' } };
}

export function moveItem(items, index, offset) {
  const target = index + offset;
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) return items;
  const result = [...items];
  [result[index], result[target]] = [result[target], result[index]];
  return result;
}

export function invalidJumpTargets(questions) {
  const positions = new Map(questions.map((question, index) => [question.id, index]));
  return questions.flatMap((question, index) => question.jumpRules
    .filter((rule) => rule.target.kind === 'question' &&
      (!positions.has(rule.target.questionId) || positions.get(rule.target.questionId) <= index))
    .map((rule) => ({ questionId: question.id, ruleId: rule.id, targetQuestionId: rule.target.questionId })));
}

export function removeInvalidJumpTargets(questions) {
  const invalid = new Set(invalidJumpTargets(questions).map((item) => `${item.questionId}/${item.ruleId}`));
  return questions.map((question) => ({ ...question, jumpRules: question.jumpRules.filter((rule) => !invalid.has(`${question.id}/${rule.id}`)) }));
}

export function rulesReferencingOption(question, optionId) {
  return question.jumpRules.filter((rule) => rule.condition.value === optionId || rule.condition.values?.includes(optionId));
}

export function removeQuestionOption(question, optionId) {
  const affected = new Set(rulesReferencingOption(question, optionId).map((rule) => rule.id));
  return {
    ...question,
    options: question.options.filter((option) => option.id !== optionId),
    jumpRules: question.jumpRules.filter((rule) => !affected.has(rule.id)),
  };
}

export function toLocalDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (number, width = 2) => String(number).padStart(width, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

export function surveyToForm(survey) {
  return {
    title: survey?.title || '', description: survey?.description || '',
    settings: { anonymous: survey?.settings?.anonymous ?? true, allowRepeat: survey?.settings?.allowRepeat ?? false },
    deadline: toLocalDateTime(survey?.deadlineAt),
    questions: survey ? structuredClone(survey.questions) : [],
  };
}

export class EditorValidationError extends Error {
  constructor(issues) {
    super(issues[0]?.message || '请检查问卷配置');
    this.name = 'EditorValidationError';
    this.issues = issues;
  }
}

/** Normalize UI strings into strict request types; collect readable problems. */
export function buildSurveyPayload(form, { locked = false, publish = false, now = Date.now() } = {}) {
  const issues = [];
  const add = (message, questionId, field) => issues.push({ message, questionId, field });
  const text = (value, max, label, questionId, required = false) => {
    const result = String(value ?? '').trim();
    if (required && !result) add(`请填写${label}`, questionId);
    if (codePointLength(result) > max) add(`${label}不能超过 ${max} 字符`, questionId);
    return result;
  };
  const numeric = (value, label, questionId, { integer = false, min, max, required = false } = {}) => {
    if (value === '' || value == null || (typeof value === 'string' && !value.trim())) {
      if (required) add(`请填写${label}`, questionId);
      return undefined;
    }
    const result = Number(value);
    if (!Number.isFinite(result)) add(`${label}必须是有限数字`, questionId);
    else if (integer && !Number.isSafeInteger(result)) add(`${label}必须是安全范围内的整数`, questionId);
    else if (!integer && Number.isInteger(result) && !Number.isSafeInteger(result) && !JSON.stringify(result).includes('e')) {
      add(`${label}超过安全整数范围，请使用较小数值`, questionId);
    } else if (min !== undefined && result < min) add(`${label}不能小于 ${min}`, questionId);
    else if (max !== undefined && result > max) add(`${label}不能大于 ${max}`, questionId);
    return result;
  };
  const payload = {
    title: text(form.title, 200, '问卷标题', undefined, true),
    description: text(form.description, 5000, '问卷说明'),
    deadlineAt: null,
  };
  if (form.deadline) {
    const deadline = new Date(form.deadline);
    if (Number.isNaN(deadline.getTime())) add('请选择有效的截止时间');
    else if (deadline.getTime() <= now) add('截止时间必须晚于当前时间');
    else payload.deadlineAt = deadline.toISOString();
  }
  if (!locked) {
    payload.settings = { anonymous: !!form.settings.anonymous, allowRepeat: !!form.settings.allowRepeat };
    if (form.questions.length > MAX_QUESTIONS) add(`每份问卷最多 ${MAX_QUESTIONS} 道题目`);
    if (publish && !form.questions.length) add('发布前请至少添加一道题目');
    const positions = new Map(form.questions.map((question, index) => [question.id, index]));
    if (positions.size !== form.questions.length) add('题目标识重复，请重新添加重复题目');
    payload.questions = form.questions.map((question, index) => {
      const qid = question.id;
      const result = {
        id: qid, type: question.type,
        title: text(question.title, 2000, `第 ${index + 1} 题题目`, qid, true),
        description: text(question.description, 2000, `第 ${index + 1} 题说明`, qid),
        required: !!question.required, validation: {}, jumpRules: [],
      };
      const constraints = question.validation || {};
      const bound = (name, label, options) => {
        const value = numeric(constraints[name], label, qid, options);
        if (value !== undefined) result.validation[name] = value;
      };
      if (question.type === 'single' || question.type === 'multiple') {
        result.options = question.options.map((option, optionIndex) => ({
          id: option.id, label: text(option.label, 500, `第 ${index + 1} 题选项 ${optionIndex + 1}`, qid, true),
        }));
        if (result.options.length < 2 || result.options.length > MAX_OPTIONS) add(`第 ${index + 1} 题需要 2～${MAX_OPTIONS} 个选项`, qid);
        if (new Set(result.options.map((option) => option.label)).size !== result.options.length) add(`第 ${index + 1} 题的选项文字不能重复`, qid);
        if (new Set(result.options.map((option) => option.id)).size !== result.options.length) add(`第 ${index + 1} 题的选项标识重复`, qid);
      }
      if (question.type === 'multiple') {
        bound('minSelections', '最少选择数量', { integer: true, min: 0, max: question.options.length });
        bound('maxSelections', '最多选择数量', { integer: true, min: 0, max: question.options.length });
        const low = Math.max(question.required ? 1 : 0, result.validation.minSelections ?? 0);
        if (low > (result.validation.maxSelections ?? question.options.length)) add(`第 ${index + 1} 题选择数量上下限不合理`, qid);
      } else if (question.type === 'text') {
        bound('minLength', '最少字符数', { integer: true, min: 0, max: 10000 });
        bound('maxLength', '最多字符数', { integer: true, min: 0, max: 10000 });
        if (Math.max(question.required ? 1 : 0, result.validation.minLength ?? 0) > (result.validation.maxLength ?? 10000)) add(`第 ${index + 1} 题字符数上下限不合理`, qid);
      } else if (question.type === 'number') {
        result.validation.integerOnly = !!constraints.integerOnly;
        bound('min', '最小值', {}); bound('max', '最大值', {});
        const low = result.validation.min, high = result.validation.max;
        if (low !== undefined && high !== undefined && low > high) add(`第 ${index + 1} 题最小值不能大于最大值`, qid);
        if (constraints.integerOnly && Math.max(-Number.MAX_SAFE_INTEGER, Math.ceil(low ?? -Number.MAX_SAFE_INTEGER)) > Math.min(Number.MAX_SAFE_INTEGER, Math.floor(high ?? Number.MAX_SAFE_INTEGER))) add(`第 ${index + 1} 题范围内不存在安全整数`, qid);
      }
      if (question.jumpRules.length > MAX_RULES) add(`每道题最多 ${MAX_RULES} 条跳转规则`, qid);
      result.jumpRules = question.jumpRules.map((rule, ruleIndex) => {
        const prefix = `第 ${index + 1} 题规则 ${ruleIndex + 1}`;
        let condition;
        if (question.type === 'single') {
          condition = { operator: 'eq', value: rule.condition.value };
          if (!question.options.some((option) => option.id === condition.value)) add(`${prefix}请选择一个有效选项`, qid);
        } else if (question.type === 'multiple') {
          condition = { operator: rule.condition.operator, values: [...rule.condition.values] };
          if (!['containsAny', 'containsAll', 'setEquals'].includes(condition.operator)) add(`${prefix}条件类型无效`, qid);
          if (!condition.values.length || condition.values.some((id) => !question.options.some((option) => option.id === id))) add(`${prefix}请至少选择一个有效选项`, qid);
          if (new Set(condition.values).size !== condition.values.length) add(`${prefix}条件不能包含重复选项`, qid);
        } else if (question.type === 'number') {
          const operator = rule.condition.operator;
          if (!['eq', 'gt', 'gte', 'lt', 'lte', 'between'].includes(operator)) add(`${prefix}条件类型无效`, qid);
          condition = { operator };
          if (operator === 'between') {
            condition.min = numeric(rule.condition.min, `${prefix}区间下限`, qid, { required: true });
            condition.max = numeric(rule.condition.max, `${prefix}区间上限`, qid, { required: true });
            if (condition.min > condition.max) add(`${prefix}区间下限不能大于上限`, qid);
          } else condition.value = numeric(rule.condition.value, `${prefix}比较值`, qid, { required: true });
        } else {
          add('文本填空不支持跳转规则', qid);
          condition = {};
        }
        const target = rule.target.kind === 'end' ? { kind: 'end' } : { kind: 'question', questionId: rule.target.questionId };
        if (target.kind === 'question' && (!positions.has(target.questionId) || positions.get(target.questionId) <= index)) add(`${prefix}必须跳到后面的题目`, qid);
        return { id: rule.id, condition, target };
      });
      return result;
    });
  }
  if (issues.length) throw new EditorValidationError(issues);
  return payload;
}
