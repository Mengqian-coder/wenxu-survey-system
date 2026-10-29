import {test, expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';

const PASSWORD = 'Management-e2e-2026!';
const name = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
const content = title => ({type: 'text', title, description: '', validation: {maxLength: 300}});
const keyHeaders = headers => ({...headers, 'Idempotency-Key': randomUUID()});
async function account(request, username) {
  expect((await request.post('/api/auth/register', {data: {username, password: PASSWORD}})).status()).toBe(201);
  const login = await request.post('/api/auth/login', {data: {username, password: PASSWORD}});
  return {Authorization: `Bearer ${(await login.json()).access_token}`};
}
async function login(page, username) {
  await page.goto('/login'); await page.getByLabel('用户名', {exact: true}).fill(username); await page.getByLabel('密码', {exact: true}).fill(PASSWORD);
  await page.getByRole('button', {name: '登录', exact: true}).click(); await expect(page).toHaveURL('/');
}
async function createQuestion(request, headers, title) {
  const response = await request.post('/api/questions', {headers: keyHeaders(headers), data: {content: content(title)}});
  expect(response.status()).toBe(201); return response.json();
}
async function seedSurvey(request, headers, title, questionId, versionId) {
  const response = await request.post('/api/surveys', {headers: keyHeaders(headers), data: {title, description: '', settings: {anonymous: true, allowRepeat: false}, deadlineAt: null, questions: [{id: 'question_one', questionId, versionId, required: true, jumpRules: []}]}});
  expect(response.status()).toBe(201); return response.json();
}
async function share(page, username) {
  await page.getByLabel('接收者用户名', {exact: true}).fill(username); await page.getByRole('button', {name: '共享整道题目', exact: true}).click();
  await page.getByRole('dialog').getByRole('button', {name: '确认共享', exact: true}).click();
  await expect(page.locator('.library-row').filter({hasText: username})).toContainText('有效');
}
async function createBank(page, bankName) {
  await page.goto('/question-banks'); await page.getByRole('button', {name: '创建题库', exact: true}).click();
  await page.getByLabel('题库名称', {exact: true}).fill(bankName); await page.getByLabel('题库说明', {exact: true}).fill('课程团队的分类，仅整理题目。');
  await page.getByRole('dialog').getByRole('button', {name: '保存题库', exact: true}).click(); await expect(page).toHaveURL(/\/question-banks\/[a-f0-9]{24}$/);
  await expect(page.getByRole('heading', {name: `${bankName}.`, exact: true})).toBeVisible(); return page.url().split('/').at(-1);
}
async function shot(page, label) {
  const directory = path.join(process.env.E2E_OUTPUT_DIR || '../output/frontend-e2e', 'screenshots'); await mkdir(directory, {recursive: true});
  await page.screenshot({path: path.join(directory, `${label}.png`), fullPage: true, animations: 'disabled'});
}
async function mobileShot(page, label) {
  const previous = page.viewportSize(); await page.setViewportSize({width: 390, height: 844});
  try {await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy(); await shot(page, label);}
  finally {await page.setViewportSize(previous); await page.mouse.move(0, 0); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
}

test('题库弹窗逐字符输入保持焦点，支持编辑、键盘导航和取消回焦', async ({page, request}) => {
  const username = name('题库输入检查'), headers = await account(request, username);
  await login(page, username);
  await page.goto('/question-banks');
  const create = page.getByRole('button', {name: '创建题库', exact: true});
  const dialog = page.getByRole('dialog');
  const bankName = page.getByLabel('题库名称', {exact: true});
  const description = page.getByLabel('题库说明', {exact: true});
  const previousOverflow = await page.evaluate(() => document.body.style.overflow);
  const typeCharacters = async (field, text) => {
    let expected = await field.inputValue();
    for (const character of text) {
      if (character === '\n') await page.keyboard.press('Enter');
      else await page.keyboard.insertText(character);
      expected += character;
      await expect(field).toHaveValue(expected);
      await expect(field).toBeFocused();
    }
  };

  await create.click();
  await expect(dialog).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', {name: '保存题库', exact: true})).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', {name: '关闭对话框', exact: true})).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', {name: '保存题库', exact: true})).toBeFocused();
  await bankName.click();
  await typeCharacters(bankName, '课程反馈Ab');
  await page.keyboard.press('Home');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.insertText('·');
  await expect(bankName).toHaveValue('课程·反馈Ab');
  await expect(bankName).toBeFocused();
  expect(await bankName.evaluate(element => element.selectionStart)).toBe(3);
  await page.keyboard.press('Backspace');
  await expect(bankName).toHaveValue('课程反馈Ab');
  await description.click();
  await typeCharacters(description, '第一行\n第二行');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(create).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe(previousOverflow);

  await create.click();
  await expect(bankName).toHaveValue('');
  await bankName.click();
  await typeCharacters(bankName, '连续输入课程题库');
  await description.click();
  await typeCharacters(description, '创建时的说明');
  await dialog.getByRole('button', {name: '保存题库', exact: true}).click();
  await expect(page).toHaveURL(/\/question-banks\/[a-f0-9]{24}$/);
  const bankId = page.url().split('/').at(-1);
  const edit = page.getByRole('button', {name: '编辑题库', exact: true});
  await edit.click();
  await bankName.click();
  await page.keyboard.press('End');
  await typeCharacters(bankName, '·修订');
  await description.click();
  await page.keyboard.press('End');
  await typeCharacters(description, '\n编辑后保留光标');

  let releaseSave;
  const saveGate = new Promise(resolve => {releaseSave = resolve;});
  await page.route(`**/api/question-banks/${bankId}`, async route => {
    if (route.request().method() === 'PATCH') {
      await saveGate;
      await route.continue();
    } else await route.continue();
  });
  try {
    await dialog.getByRole('button', {name: '保存题库', exact: true}).click();
    await expect(dialog.getByRole('button', {name: '取消', exact: true})).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
  } finally {releaseSave();}
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole('heading', {name: '连续输入课程题库·修订.', exact: true})).toBeVisible();
  const saved = await (await request.get(`/api/question-banks/${bankId}`, {headers})).json();
  expect(saved.name).toBe('连续输入课程题库·修订');
  expect(saved.description).toBe('创建时的说明\n编辑后保留光标');

  await edit.click();
  await bankName.click();
  await page.keyboard.press('End');
  await typeCharacters(bankName, '取消修改');
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(edit).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe(previousOverflow);
});

test('历史恢复、整题共享、独立副本再共享、撤权后旧草稿发布、引用隐私和题库管理', async ({page, browser, request, baseURL}) => {
  test.setTimeout(180_000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const owner = name('题目所有者'), recipient = name('共享接收者'), third = name('副本接收者');
  const [ownerHeaders, recipientHeaders, thirdHeaders] = await Promise.all([account(request, owner), account(request, recipient), account(request, third)]);
  const oldTitle = '团队课程反馈：最希望改善的环节', editedTitle = '团队课程反馈：下一步改进建议';
  const created = await createQuestion(request, ownerHeaders, oldTitle), questionId = created.question._id;
  const editedResponse = await request.post(`/api/questions/${questionId}/versions`, {headers: keyHeaders(ownerHeaders), data: {revision: created.question.revision, baseVersionId: created.version._id, content: content(editedTitle)}});
  expect(editedResponse.status()).toBe(201);
  const ownerSurvey = await seedSurvey(request, ownerHeaders, '题目所有者自己的问卷', questionId, created.version._id);
  const recipientContext = await browser.newContext({baseURL}), thirdContext = await browser.newContext({baseURL});
  const recipientPage = await recipientContext.newPage(), thirdPage = await thirdContext.newPage();
  recipientPage.on('pageerror', error => errors.push(error.message)); thirdPage.on('pageerror', error => errors.push(error.message));
  try {
    await login(page, owner); await page.goto(`/questions/${questionId}`);
    await page.locator('.version-row').filter({hasText: `v1 · ${oldTitle}`}).click();
    await page.getByRole('button', {name: '将 v1 恢复为新版本', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认操作', exact: true}).click();
    await expect(page.getByText('内容来源：v1', {exact: true})).toBeVisible();
    await expect(page.locator('.version-row')).toHaveCount(3); await expect(page.getByText('当前查看 v3，最新版本 v3', {exact: true})).toBeVisible();
    await page.locator('.version-row').filter({hasText: `v2 · ${editedTitle}`}).click(); await page.getByRole('button', {name: '归档本版本', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认操作', exact: true}).click();
    await page.getByLabel('历史版本归档筛选', {exact: true}).selectOption('only'); await expect(page.locator('.version-row')).toHaveCount(1); await expect(page.locator('.version-row')).toContainText('v2');
    await page.getByLabel('历史版本归档筛选', {exact: true}).selectOption('include'); await share(page, recipient);
    await shot(page, 'management-owner-history-sharing');
    await mobileShot(page, 'management-owner-history-sharing-mobile');

    await login(recipientPage, recipient); await recipientPage.goto('/questions'); await recipientPage.getByLabel('题目来源筛选', {exact: true}).selectOption('shared');
    await expect(recipientPage.locator('.survey-card')).toHaveCount(1); await recipientPage.getByRole('heading', {name: oldTitle, exact: true}).click();
    await expect(recipientPage.getByRole('heading', {name: '共享管理', exact: true})).toHaveCount(0);
    await expect(recipientPage.getByRole('button', {name: /恢复为新版本/})).toHaveCount(0); await expect(recipientPage.locator('.version-row')).toHaveCount(3);
    const bankName = '共享课程题库', bankId = await createBank(recipientPage, bankName);
    await recipientPage.getByRole('button', {name: '添加题目到题库', exact: true}).click();
    await recipientPage.getByRole('dialog').getByLabel('搜索加入题库的题目', {exact: true}).fill(oldTitle);
    await recipientPage.getByRole('dialog').getByRole('button', {name: '加入题库', exact: true}).click(); await expect(recipientPage.getByRole('dialog').getByRole('button', {name: '已在此题库', exact: true})).toBeDisabled();
    await recipientPage.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
    await recipientPage.goto('/surveys/new'); await recipientPage.getByLabel('问卷标题', {exact: true}).fill('接收者的私密草稿标题');
    await recipientPage.getByRole('button', {name: '从题目列表选用', exact: true}).last().click(); await recipientPage.getByRole('dialog').getByLabel('按题库选题', {exact: true}).selectOption(bankId);
    await recipientPage.getByRole('dialog').getByRole('button', {name: '选择版本', exact: true}).click();
    await recipientPage.getByRole('dialog').locator('.library-row').filter({hasText: `v1 · ${oldTitle}`}).getByRole('button', {name: '查看并选用', exact: true}).click();
    await recipientPage.getByRole('dialog').getByRole('button', {name: '使用 v1', exact: true}).click();
    await recipientPage.getByRole('button', {name: '保存草稿', exact: true}).click(); await expect(recipientPage).toHaveURL(/\/surveys\/[a-f0-9]{24}\/edit$/);
    const recipientSurveyId = recipientPage.url().match(/surveys\/([a-f0-9]{24})/)[1];
    const savedSurvey = await (await request.get(`/api/surveys/${recipientSurveyId}`, {headers: recipientHeaders})).json(); expect(savedSurvey.questions[0].versionId).toBe(created.version._id);
    await recipientPage.goto(`/questions/${questionId}`); await recipientPage.locator('.version-row').filter({hasText: `v1 · ${oldTitle}`}).click();
    await recipientPage.getByRole('button', {name: '另存为独立题目', exact: true}).click(); await recipientPage.getByRole('dialog').getByRole('button', {name: '确认操作', exact: true}).click();
    await expect(recipientPage).not.toHaveURL(`/questions/${questionId}`); const copyId = recipientPage.url().split('/').at(-1);
    await expect(recipientPage.locator('.version-row')).toHaveCount(1); await expect(recipientPage.getByText('独立副本', {exact: true})).toBeVisible();
    await recipientPage.getByRole('link', {name: '查看副本来源版本', exact: true}).click(); await expect(recipientPage).toHaveURL(`/questions/${questionId}?versionId=${created.version._id}`); await expect(recipientPage.getByText('当前查看 v1，最新版本 v3', {exact: true})).toBeVisible();
    await recipientPage.goto(`/questions/${copyId}`); await share(recipientPage, third);
    await recipientPage.getByRole('button', {name: '加入题库', exact: true}).click(); await recipientPage.getByRole('dialog').getByRole('button', {name: '加入此题库', exact: true}).click(); await recipientPage.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
    await login(thirdPage, third); await thirdPage.goto(`/questions/${copyId}`); await expect(thirdPage.getByLabel('第1题题目', {exact: true})).toHaveValue(oldTitle); await expect(thirdPage.getByRole('heading', {name: '共享管理', exact: true})).toHaveCount(0);
    expect((await request.get(`/api/questions/${questionId}`, {headers: thirdHeaders})).status()).toBe(404);

    await page.reload(); await expect(page.getByRole('heading', {name: '引用查询', exact: true})).toBeVisible();
    await expect(page.locator('.usage-counts')).toContainText('2'); await expect(page.getByRole('link', {name: ownerSurvey.title, exact: true})).toBeVisible();
    await expect(page.getByText('接收者的私密草稿标题', {exact: true})).toHaveCount(0);
    const usage = await (await request.get(`/api/questions/${questionId}/usage`, {headers: ownerHeaders})).json(); expect(usage.totalSurveyCount).toBe(2); expect(usage.otherSurveyCount).toBe(1); expect(usage.items.map(item => item.surveyId)).toEqual([ownerSurvey._id]);
    await page.locator('.library-row').filter({hasText: recipient}).getByRole('button', {name: '撤销授权', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认撤销', exact: true}).click();
    await expect(page.locator('.library-row').filter({hasText: recipient})).toContainText('已撤销');
    await recipientPage.getByRole('link', {name: '查看副本来源版本', exact: true}).click(); await expect(recipientPage.getByRole('alert')).toBeVisible(); await expect(recipientPage.getByLabel('第1题题目', {exact: true})).toHaveCount(0);
    await recipientPage.goto(`/question-banks/${bankId}`); await expect(recipientPage.getByText('题目暂不可用', {exact: true})).toBeVisible();
    const bankItems = await (await request.get(`/api/question-banks/${bankId}/items`, {headers: recipientHeaders})).json(); expect(bankItems.items.find(item => item.questionId === questionId)).toEqual({questionId, available: false});
    await shot(recipientPage, 'management-bank-revoked-placeholder');
    await mobileShot(recipientPage, 'management-bank-revoked-placeholder-mobile');
    await recipientPage.goto(`/surveys/${recipientSurveyId}/edit`); await recipientPage.getByRole('button', {name: '发布问卷', exact: true}).click(); await recipientPage.getByRole('dialog').getByRole('button', {name: '确认发布', exact: true}).click();
    await expect(recipientPage.getByLabel('问卷分享链接', {exact: true})).toBeVisible(); await recipientPage.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
    await recipientPage.goto(`/question-banks/${bankId}`); await recipientPage.locator('.library-row').filter({hasText: '题目暂不可用'}).getByRole('button', {name: '从题库移除', exact: true}).click(); await recipientPage.getByRole('dialog').getByRole('button', {name: '确认删除', exact: true}).click();
    await expect(recipientPage.getByText('题目暂不可用', {exact: true})).toHaveCount(0); await recipientPage.getByRole('button', {name: '编辑题库', exact: true}).click(); await recipientPage.getByLabel('题库名称', {exact: true}).fill('已整理的课程题库'); await recipientPage.getByRole('dialog').getByRole('button', {name: '保存题库', exact: true}).click(); await expect(recipientPage.getByRole('heading', {name: '已整理的课程题库.', exact: true})).toBeVisible();
    await recipientPage.getByRole('button', {name: '删除题库', exact: true}).click(); await expect(recipientPage.getByRole('dialog')).toContainText('所有题目、历史版本、共享授权和问卷引用均继续保留'); await recipientPage.getByRole('dialog').getByRole('button', {name: '确认删除', exact: true}).click(); await expect(recipientPage).toHaveURL('/question-banks');
    await recipientPage.goto(`/questions/${copyId}`); await expect(recipientPage.getByLabel('第1题题目', {exact: true})).toHaveValue(oldTitle); expect((await request.get(`/api/questions/${copyId}`, {headers: thirdHeaders})).status()).toBe(200);
    await page.getByLabel('共享状态筛选', {exact: true}).selectOption('revoked'); await page.locator('.library-row').filter({hasText: recipient}).getByRole('button', {name: '重新授权', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认共享', exact: true}).click();
    await page.getByLabel('共享状态筛选', {exact: true}).selectOption('active'); await expect(page.locator('.library-row').filter({hasText: recipient})).toContainText('有效'); expect((await request.get(`/api/questions/${questionId}`, {headers: recipientHeaders})).status()).toBe(200);
    expect(errors).toEqual([]);
  } finally {await recipientContext.close(); await thirdContext.close();}
});

test('慢请求返回时不能把上一题库或题目的内容覆盖到当前编辑对象', async ({page, request}) => {
  test.setTimeout(120_000);
  const username = name('导航竞态检查'), headers = await account(request, username);
  const createBankApi = async bankName => (await request.post('/api/question-banks', {headers: keyHeaders(headers), data: {name: bankName, description: bankName}})).json();
  const [bankA, bankB, questionA, questionB] = await Promise.all([createBankApi('慢速题库A'), createBankApi('当前题库B'), createQuestion(request, headers, '慢速题目A'), createQuestion(request, headers, '当前题目B')]);
  await login(page, username);
  let releaseBank, bankHeld = false, bankDelivered;
  const bankGate = new Promise(resolve => {releaseBank = resolve;}), bankDone = new Promise(resolve => {bankDelivered = resolve;});
  await page.route(`**/api/question-banks/${bankA._id}`, async route => {const response = await route.fetch(); bankHeld = true; await bankGate; await route.fulfill({response}); bankDelivered();});
  await page.goto('/question-banks'); await page.getByRole('heading', {name: '慢速题库A', exact: true}).click(); await expect.poll(() => bankHeld).toBeTruthy();
  await page.getByRole('link', {name: '我的题库', exact: true}).click(); await page.getByRole('heading', {name: '当前题库B', exact: true}).click(); await expect(page.getByRole('heading', {name: '当前题库B.', exact: true})).toBeVisible();
  releaseBank(); await bankDone; await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.getByRole('heading', {name: '当前题库B.', exact: true})).toBeVisible(); await page.getByRole('button', {name: '编辑题库', exact: true}).click(); await expect(page.getByLabel('题库名称', {exact: true})).toHaveValue('当前题库B'); await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  let releaseQuestion, questionHeld = false;
  const questionGate = new Promise(resolve => {releaseQuestion = resolve;});
  await page.route(`**/api/questions/${questionA.question._id}`, async route => {const response = await route.fetch(); questionHeld = true; await questionGate; await route.fulfill({response});});
  await page.getByRole('link', {name: '我的题目', exact: true}).click(); await page.getByRole('heading', {name: '慢速题目A', exact: true}).click(); await expect.poll(() => questionHeld).toBeTruthy();
  await page.getByRole('link', {name: '我的题目', exact: true}).click(); await page.getByRole('heading', {name: '当前题目B', exact: true}).click(); await expect(page.getByLabel('第1题题目', {exact: true})).toHaveValue('当前题目B');
  const oldVersionResponse = page.waitForResponse(response => response.url().endsWith(`/questions/${questionA.question._id}/versions/${questionA.version._id}`)); releaseQuestion(); await oldVersionResponse;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); await expect(page.getByLabel('第1题题目', {exact: true})).toHaveValue('当前题目B');
  await page.getByRole('button', {name: '基于 v1 编辑为新版本', exact: true}).click(); await expect(page.getByLabel('第1题题目', {exact: true})).toHaveValue('当前题目B'); await page.getByRole('button', {name: '取消编辑', exact: true}).click();
  expect((await request.get(`/api/question-banks/${bankB._id}`, {headers})).ok()).toBeTruthy(); expect((await request.get(`/api/questions/${questionB.question._id}`, {headers})).ok()).toBeTruthy();
});

test('归档题仍可加入多个题库整理，题库删除的丢失响应用原请求确认', async ({page, request}) => {
  test.setTimeout(120_000);
  const username = name('分类管理者'), headers = await account(request, username), created = await createQuestion(request, headers, '已归档但需要保留分类的题目');
  const archive = await request.patch(`/api/questions/${created.question._id}`, {headers: keyHeaders(headers), data: {revision: created.question.revision, archived: true}}); expect(archive.ok()).toBeTruthy();
  await login(page, username);
  let lostCreate = false;
  await page.route('**/api/question-banks', async route => {if (route.request().method() === 'POST' && !lostCreate) {lostCreate = true; const result = await route.fetch(); expect(result.status()).toBe(201); await route.abort('failed');} else await route.continue();});
  await page.goto('/question-banks'); await page.getByRole('button', {name: '创建题库', exact: true}).click(); await page.getByLabel('题库名称', {exact: true}).fill('网络重试分类'); await page.getByRole('dialog').getByRole('button', {name: '保存题库', exact: true}).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible(); await page.getByRole('dialog').getByRole('button', {name: '取消', exact: true}).click();
  await page.getByRole('button', {name: '确认原操作', exact: true}).click(); await expect(page).toHaveURL(/\/question-banks\/[a-f0-9]{24}$/); const bankId = page.url().split('/').at(-1);
  expect((await (await request.get('/api/question-banks', {headers})).json()).total).toBe(1);
  await page.getByRole('button', {name: '添加题目到题库', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '加入题库', exact: true}).click(); await expect(page.getByRole('dialog').getByRole('button', {name: '已在此题库', exact: true})).toBeDisabled(); await page.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
  await page.goto(`/questions/${created.question._id}`); await page.getByRole('button', {name: '加入题库', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '加入此题库', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
  const secondBank = await createBank(page, '另一个分类'); await page.goto(`/questions/${created.question._id}`); await page.getByRole('button', {name: '加入题库', exact: true}).click(); await page.getByRole('dialog').locator('.library-row').filter({hasText: '另一个分类'}).getByRole('button', {name: '加入此题库', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '完成', exact: true}).click();
  let lostDelete = false;
  await page.route(`**/api/question-banks/${bankId}?*`, async route => {if (route.request().method() === 'DELETE' && !lostDelete) {lostDelete = true; const result = await route.fetch(); expect(result.ok()).toBeTruthy(); await route.abort('failed');} else await route.continue();});
  await page.goto(`/question-banks/${bankId}`); await page.getByRole('button', {name: '删除题库', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认删除', exact: true}).click(); await expect(page.getByRole('button', {name: '确认原操作', exact: true})).toBeVisible(); await page.getByRole('button', {name: '确认原操作', exact: true}).click(); await expect(page).toHaveURL('/question-banks');
  expect((await request.get(`/api/questions/${created.question._id}`, {headers})).status()).toBe(200); const preserved = await (await request.get(`/api/question-banks/${secondBank}/items`, {headers})).json(); expect(preserved.total).toBe(1);
  await page.goto('/surveys/new'); await page.getByRole('button', {name: '从题目列表选用', exact: true}).last().click(); await page.getByRole('dialog').getByLabel('按题库选题', {exact: true}).selectOption(secondBank); await expect(page.getByRole('dialog').getByRole('button', {name: '选择版本', exact: true})).toBeDisabled();
});
