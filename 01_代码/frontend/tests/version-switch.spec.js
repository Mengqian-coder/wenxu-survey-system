import {test, expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';

const PASSWORD = 'Version-switch-e2e-2026!';
const unique = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
const keyed = headers => ({...headers, 'Idempotency-Key': randomUUID()});
const choice = title => ({type: 'single', title, description: '', validation: {}, options: [{id: 'a', label: '选项 A'}, {id: 'b', label: '选项 B'}]});
const text = title => ({type: 'text', title, description: '', validation: {maxLength: 200}});
const reference = (created, id, extra = {}) => ({id, questionId: created.question._id, versionId: created.version._id, required: false, jumpRules: [], ...extra});
const jump = (id, value, questionId) => ({id, condition: {operator: 'eq', value}, target: questionId ? {kind: 'question', questionId} : {kind: 'end'}});

async function account(request, username) {
  expect((await request.post('/api/auth/register', {data: {username, password: PASSWORD}})).status()).toBe(201);
  const response = await request.post('/api/auth/login', {data: {username, password: PASSWORD}});
  expect(response.ok()).toBeTruthy();
  return {Authorization: `Bearer ${(await response.json()).access_token}`};
}
async function login(page, username) {
  await page.goto('/login');
  await page.getByLabel('用户名', {exact: true}).fill(username);
  await page.getByLabel('密码', {exact: true}).fill(PASSWORD);
  await page.getByRole('button', {name: '登录', exact: true}).click();
  await expect(page.getByRole('heading', {name: '我的问卷.', exact: true})).toBeVisible();
}
async function createQuestion(request, headers, content) {
  const response = await request.post('/api/questions', {headers: keyed(headers), data: {content}});
  expect(response.status()).toBe(201);
  return response.json();
}
async function addVersion(request, headers, original, content) {
  const response = await request.post(`/api/questions/${original.question._id}/versions`, {headers: keyed(headers), data: {
    revision: original.question.revision, baseVersionId: original.version._id, content,
  }});
  expect(response.status()).toBe(201);
  return response.json();
}
async function createSurvey(request, headers, questions) {
  const response = await request.post('/api/surveys', {headers: keyed(headers), data: {
    title: '题目换版影响检查', description: '', settings: {anonymous: true, allowRepeat: false}, questions,
  }});
  expect(response.status()).toBe(201);
  return response.json();
}
async function getSurvey(request, headers, id) {
  const response = await request.get(`/api/surveys/${id}`, {headers});
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function previewVersion(page, index, versionNo, title) {
  await page.getByRole('button', {name: '选择其他版本', exact: true}).nth(index).click();
  const picker = page.getByRole('dialog');
  await picker.locator('.library-row').filter({hasText: `v${versionNo} · ${title}`})
    .getByRole('button', {name: '查看并选用', exact: true}).click();
  return picker;
}
async function screenshot(page, label) {
  const directory = path.join(process.env.E2E_OUTPUT_DIR || '../output/frontend-e2e', 'screenshots');
  await mkdir(directory, {recursive: true});
  await page.screenshot({path: path.join(directory, `${label}.png`), fullPage: true, animations: 'disabled'});
}

test('删除选项的换版影响显式告知，取消不改配置，确认并保存后仅清空本题跳转', async ({page, request}) => {
  test.setTimeout(120_000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const username = unique('换版检查'), headers = await account(request, username);
  const gate = await createQuestion(request, headers, choice('前置题'));
  const original = await createQuestion(request, headers, choice('原版中间题'));
  const tail = await createQuestion(request, headers, text('最后一道题'));
  const replacement = await addVersion(request, headers, original, {
    ...choice('新版中间题'), options: [{id: 'a', label: '选项 A'}, {id: 'c', label: '选项 C'}],
  });
  const sourceRules = [jump('rule_a', 'a', 'tail_local'), jump('rule_b', 'b')];
  const incomingRules = [jump('gate_to_middle', 'a', 'middle_local')];
  const survey = await createSurvey(request, headers, [
    reference(gate, 'gate_local', {required: true, jumpRules: incomingRules}),
    reference(original, 'middle_local', {jumpRules: sourceRules}),
    reference(tail, 'tail_local'),
  ]);
  const patches = [];
  page.on('request', outgoing => {
    if (outgoing.method() === 'PATCH' && new URL(outgoing.url()).pathname === `/api/surveys/${survey._id}`) patches.push(outgoing.postDataJSON());
  });
  await login(page, username);
  await page.goto(`/surveys/${survey._id}/edit`);
  const middle = page.locator('#question-middle_local');
  await expect(middle.getByTestId('jump-rule-editor')).toHaveCount(2);
  await expect(middle.getByRole('checkbox', {name: '必答题', exact: true})).not.toBeChecked();

  await test.step('重新查看当前版本不会重设跳转', async () => {
    const picker = await previewVersion(page, 1, 1, original.version.content.title);
    await expect(picker.getByRole('button', {name: '当前使用版本', exact: true})).toBeDisabled();
    await expect(picker.getByRole('button', {name: '使用 v1', exact: true})).toHaveCount(0);
    await picker.getByRole('button', {name: '取消', exact: true}).click();
    await expect(middle.getByTestId('jump-rule-editor')).toHaveCount(2);
    expect(patches).toEqual([]);
  });

  await test.step('预览显示版本与卷内配置边界及具体影响', async () => {
    const picker = await previewVersion(page, 1, 2, replacement.version.content.title);
    await expect(picker).toContainText(/题干/);
    await expect(picker).toContainText(/选项/);
    await expect(picker).toContainText(/校验/);
    await expect(picker).toContainText(/必答/);
    await expect(picker).toContainText(/顺序|位置/);
    await expect(picker).toContainText(/跳转/);
    const impact = picker.getByTestId('version-switch-impact');
    await expect(impact).toContainText('换版影响');
    await expect(impact).toContainText('选项 B');
    await expect(impact).toContainText(/删除|移除/);
    await expect(impact).toContainText(/清空[\s\S]*2\s*条|2\s*条[\s\S]*清空/);
    await screenshot(page, 'version-switch-deleted-option-impact');
    const previousViewport = page.viewportSize();
    await page.setViewportSize({width: 390, height: 844});
    try {
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
      await screenshot(page, 'version-switch-deleted-option-impact-mobile');
    } finally {await page.setViewportSize(previousViewport);}
    await picker.getByRole('button', {name: '使用 v2', exact: true}).click();
    const confirmation = page.getByRole('dialog', {name: '更换题目版本？', exact: true});
    await expect(confirmation).toBeVisible();
    await expect(confirmation).toContainText(/2\s*条/);
    await expect(confirmation.getByRole('button', {name: '确认换版并清空规则', exact: true})).toBeVisible();
    await confirmation.getByRole('button', {name: '取消', exact: true}).click();
    await expect(confirmation).toHaveCount(0);
    // Returning to the picker is allowed; neither cancellation path mutates the draft.
    const remainingPicker = page.getByRole('dialog');
    if (await remainingPicker.count()) await remainingPicker.getByRole('button', {name: '取消', exact: true}).click();
    await expect(middle.getByLabel('第2题题目', {exact: true})).toHaveValue(original.version.content.title);
    await expect(middle.getByTestId('jump-rule-editor')).toHaveCount(2);
    await expect(page.getByText('所有修改已保存', {exact: true})).toBeVisible();
    expect(patches).toEqual([]);
    expect((await getSurvey(request, headers, survey._id)).questions).toEqual(survey.questions);
  });

  await test.step('确认仅改本地，保存才提交，保留其他卷内配置', async () => {
    const picker = await previewVersion(page, 1, 2, replacement.version.content.title);
    await picker.getByRole('button', {name: '使用 v2', exact: true}).click();
    await page.getByRole('dialog', {name: '更换题目版本？', exact: true})
      .getByRole('button', {name: '确认换版并清空规则', exact: true}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(middle.getByLabel('第2题题目', {exact: true})).toHaveValue(replacement.version.content.title);
    await expect(middle.getByTestId('jump-rule-editor')).toHaveCount(0);
    await expect(middle.getByRole('checkbox', {name: '必答题', exact: true})).not.toBeChecked();
    await expect(page.locator('#question-gate_local').getByLabel('第1题规则1跳转目标', {exact: true})).toHaveValue('middle_local');
    await expect(page.getByText('有未保存的修改', {exact: true})).toBeVisible();
    expect(patches).toEqual([]);
    expect((await getSurvey(request, headers, survey._id)).questions).toEqual(survey.questions);
    await page.getByRole('button', {name: '保存草稿', exact: true}).click();
    await expect(page.getByText('所有修改已保存', {exact: true})).toBeVisible();
    expect(patches).toHaveLength(1);
    const saved = await getSurvey(request, headers, survey._id);
    expect(saved.questions.map(item => item.id)).toEqual(['gate_local', 'middle_local', 'tail_local']);
    expect(saved.questions[0]).toEqual(survey.questions[0]);
    expect(saved.questions[2]).toEqual(survey.questions[2]);
    expect(saved.questions[1]).toMatchObject({
      id: 'middle_local', questionId: original.question._id, versionId: replacement.version._id,
      versionNo: 2, required: false, jumpRules: [], options: replacement.version.content.options,
    });
    await page.reload();
    await expect(middle.getByTestId('jump-rule-editor')).toHaveCount(0);
    await expect(page.locator('#question-gate_local').getByLabel('第1题规则1跳转目标', {exact: true})).toHaveValue('middle_local');
  });
  expect(errors).toEqual([]);
});

test('没有本题跳转时仍说明换版和题型变化，选用后无需清空规则确认且保存前不落库', async ({page, request}) => {
  test.setTimeout(90_000);
  const username = unique('题型换版'), headers = await account(request, username);
  const original = await createQuestion(request, headers, choice('无跳转单选题'));
  const replacement = await addVersion(request, headers, original, text('换成文本填空'));
  const survey = await createSurvey(request, headers, [reference(original, 'plain_local')]);
  const patches = [];
  page.on('request', outgoing => {
    if (outgoing.method() === 'PATCH' && new URL(outgoing.url()).pathname === `/api/surveys/${survey._id}`) patches.push(outgoing.postDataJSON());
  });
  await login(page, username);
  await page.goto(`/surveys/${survey._id}/edit`);
  const picker = await previewVersion(page, 0, 2, replacement.version.content.title);
  const impact = picker.getByTestId('version-switch-impact');
  await expect(impact).toContainText('换版影响');
  await expect(impact).toContainText('单选');
  await expect(impact).toContainText('文本');
  await expect(impact).toContainText(/没有|无.*跳转|0\s*条/);
  await picker.getByRole('button', {name: '使用 v2', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const card = page.locator('#question-plain_local');
  await expect(card.getByLabel('第1题题目', {exact: true})).toHaveValue(replacement.version.content.title);
  await expect(card.getByLabel('第1题最多字符数', {exact: true})).toHaveValue('200');
  await expect(card.getByRole('checkbox', {name: '必答题', exact: true})).not.toBeChecked();
  expect(patches).toEqual([]);
  expect((await getSurvey(request, headers, survey._id)).questions).toEqual(survey.questions);
  await page.getByRole('button', {name: '保存草稿', exact: true}).click();
  await expect(page.getByText('所有修改已保存', {exact: true})).toBeVisible();
  expect(patches).toHaveLength(1);
  expect((await getSurvey(request, headers, survey._id)).questions[0]).toMatchObject({
    id: 'plain_local', versionId: replacement.version._id, type: 'text', required: false, jumpRules: [], validation: {maxLength: 200},
  });
});
