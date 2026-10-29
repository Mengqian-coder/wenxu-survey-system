import {test, expect} from '@playwright/test';
import {randomUUID} from 'node:crypto';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = path.join(process.env.E2E_OUTPUT_DIR || path.join(ROOT, 'output/frontend-e2e'), 'screenshots');
const PASSWORD = 'Survey-e2e-correct-2026!';
const unique = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
const keyed = headers => ({...headers, 'Idempotency-Key': randomUUID()});
const statPath = (q, v) => `/question-statistics?questionId=${q}&versionId=${v}`;

async function account(request, username) {
  expect((await request.post('/api/auth/register', {data: {username, password: PASSWORD}})).status()).toBe(201);
  const response = await request.post('/api/auth/login', {data: {username, password: PASSWORD}});
  expect(response.ok()).toBeTruthy();
  return {Authorization: `Bearer ${(await response.json()).access_token}`};
}
async function login(page, username) {
  await page.goto('/login'); await page.getByLabel('用户名', {exact: true}).fill(username); await page.getByLabel('密码', {exact: true}).fill(PASSWORD);
  await page.getByRole('button', {name: '登录', exact: true}).click();
  await expect(page.getByRole('heading', {name: '我的问卷.', exact: true})).toBeVisible();
}
async function createQuestion(request, headers, content) {
  const response = await request.post('/api/questions', {headers: keyed(headers), data: {content}});
  expect(response.status()).toBe(201); return response.json();
}
async function survey(request, headers, title, questions, publish = true) {
  const response = await request.post('/api/surveys', {headers: keyed(headers), data: {title, settings: {anonymous: true, allowRepeat: true}, questions}});
  expect(response.status()).toBe(201); const created = await response.json();
  if (!publish) return created;
  const published = await request.post(`/api/surveys/${created._id}/publish`, {headers: keyed(headers), data: {revision: created.revision}});
  expect(published.ok()).toBeTruthy(); return published.json();
}
const reference = (created, id, extra = {}) => ({id, questionId: created.question._id, versionId: created.version._id, required: false, jumpRules: [], ...extra});
async function submit(request, headers, target, answers) {
  const started = await request.post(`/api/public/surveys/${target.publicId}/start`, {headers}); expect(started.ok()).toBeTruthy();
  const response = await request.post(`/api/public/surveys/${target.publicId}/submit`, {headers, data: {...await started.json(), answers}});
  expect(response.status()).toBe(201);
}
async function metric(page, label, value) {
  await expect(page.locator('.cross-stat-counts > div').filter({has: page.locator('dt', {hasText: label})}).locator('dd')).toHaveText(new RegExp(`^${value}(份|次)$`));
}
async function shot(page, name, mobile = false) {
  await mkdir(SHOTS, {recursive: true});
  const previous = page.viewportSize();
  if (mobile) await page.setViewportSize({width: 390, height: 844});
  try {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await page.screenshot({path: path.join(SHOTS, `${name}.png`), fullPage: true, animations: 'disabled'});
  } finally {if (mobile) await page.setViewportSize(previous);}
}

test('跨问卷同版本合并、同人计次、版本恢复副本隔离及四题型结果和原值分页', async ({page, request}) => {
  test.setTimeout(180_000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const username = unique('跨卷管理者'), owner = await account(request, username), participant = await account(request, unique('同一位填写者'));
  const single = await createQuestion(request, owner, {type: 'single', title: '学习方向统计题', options: [{id: 'a', label: '数据库'}, {id: 'b', label: '编译原理'}]});
  const multiple = await createQuestion(request, owner, {type: 'multiple', title: '参与环节统计题', options: [{id: 'a', label: '实验'}, {id: 'b', label: '讨论'}]});
  const number = await createQuestion(request, owner, {type: 'number', title: '每周投入小时统计题'});
  const text = await createQuestion(request, owner, {type: 'text', title: '学习建议统计题'});
  const refs = [reference(single, 'single_a'), reference(multiple, 'multiple_a'), reference(number, 'number_a'), reference(text, 'text_a')];
  const first = await survey(request, owner, '统计本人问卷A', refs), second = await survey(request, owner, '统计本人问卷B', refs.map(item => ({...item, id: item.id.replace('_a', '_b')})));
  for (let index = 0; index < 23; index++) {
    const target = index < 21 ? first : second, suffix = index < 21 ? 'a' : 'b';
    await submit(request, participant, target, [{questionId: `single_${suffix}`, value: 'a'}, {questionId: `multiple_${suffix}`, value: ['a', 'b']}, {questionId: `number_${suffix}`, value: index}, {questionId: `text_${suffix}`, value: `观察建议${index}`}]);
  }
  const version2Response = await request.post(`/api/questions/${single.question._id}/versions`, {headers: keyed(owner), data: {revision: single.question.revision, baseVersionId: single.version._id, content: {...single.version.content, title: '学习方向第二版本'}}});
  expect(version2Response.status()).toBe(201); const version2 = await version2Response.json();
  const restoredResponse = await request.post(`/api/questions/${single.question._id}/restore`, {headers: keyed(owner), data: {revision: version2.question.revision, sourceVersionId: single.version._id}});
  expect(restoredResponse.status()).toBe(201); const restored = await restoredResponse.json();
  const copyResponse = await request.post(`/api/questions/${single.question._id}/copies`, {headers: keyed(owner), data: {versionId: single.version._id}});
  expect(copyResponse.status()).toBe(201); const copy = await copyResponse.json();
  await survey(request, owner, '第二版本草稿', [reference(version2, 'v2')], false);
  await survey(request, owner, '恢复第三版本草稿', [reference(restored, 'v3')], false);
  await survey(request, owner, '独立副本草稿', [reference(copy, 'copy')], false);
  await login(page, username); await page.getByRole('link', {name: '跨问卷统计', exact: true}).click();
  await expect(page.getByRole('heading', {name: '跨问卷统计.', exact: true})).toBeVisible(); await expect(page.locator('.statistic-question-grid > article')).toHaveCount(5);
  await expect(page.getByText('回答次数与答卷份数均不代表独立人数。', {exact: false})).toBeVisible();
  await shot(page, 'cross-statistics-directory'); await shot(page, 'cross-statistics-directory-mobile', true);
  await page.goto(statPath(single.question._id, restored.version._id)); await metric(page, '已提交答卷', 0); await metric(page, '引用问卷', 1);
  await expect(page.locator('.option-stat').first()).toContainText('暂无比例');
  await page.locator('.statistic-version-list a.version-row').filter({hasText: /^v2 ·/}).click(); await metric(page, '已提交答卷', 0);
  await page.locator('.statistic-version-list a.version-row').filter({hasText: /^v1 ·/}).click(); await metric(page, '已提交答卷', 23); await metric(page, '有效回答', 23); await metric(page, '引用问卷', 2);
  await expect(page.locator('.option-stat').first()).toContainText('23 次 · 100%'); await expect(page.locator('.option-stat').last()).toContainText('0 次 · 0%');
  await page.goto(statPath(copy.question._id, copy.version._id)); await metric(page, '已提交答卷', 0); await expect(page.locator('.statistic-version-list a.version-row')).toHaveCount(1);
  await page.goto(statPath(multiple.question._id, multiple.version._id)); await metric(page, '有效回答', 23); await expect(page.locator('.option-stat strong')).toHaveText(['23 次 · 100%', '23 次 · 100%']);
  await expect(page.getByText('比例之和可能超过 100%', {exact: false})).toBeVisible(); await shot(page, 'cross-statistics-multiple'); await shot(page, 'cross-statistics-multiple-mobile', true);
  await page.goto(statPath(number.question._id, number.version._id)); await expect(page.locator('.cross-number-metrics dd')).toHaveText(['0', '22', '11']);
  await expect(page.locator('.text-value')).toHaveCount(20); await page.getByRole('button', {name: '下一页', exact: true}).click(); await expect(page.locator('.text-value')).toHaveCount(3); await expect(page.locator('.text-value p')).toHaveText(['2', '1', '0']);
  await shot(page, 'cross-statistics-number'); await shot(page, 'cross-statistics-number-mobile', true);
  await page.goto(statPath(text.question._id, text.version._id)); await expect(page.locator('.text-value')).toHaveCount(20); await expect(page.locator('.text-value p').first()).toHaveText('观察建议22');
  await page.getByRole('button', {name: '下一页', exact: true}).click(); await expect(page.locator('.text-value p')).toHaveText(['观察建议2', '观察建议1', '观察建议0']); await shot(page, 'cross-statistics-text-page2');
  await page.goto(`/surveys/${first._id}/results`); await page.locator('.stat-card').filter({hasText: single.version.content.title}).getByRole('link', {name: '查看同版本跨问卷统计', exact: true}).click(); await expect(page).toHaveURL(new RegExp(`questionId=${single.question._id}&versionId=${single.version._id}`)); await metric(page, '已提交答卷', 23);
  expect(errors).toEqual([]);
});

test('共享原题所有者和接收者各看本人答卷，撤权与归档保留旧引用统计', async ({page, browser, request, baseURL}) => {
  test.setTimeout(120_000);
  const ownerName = unique('统计原题主人'), recipientName = unique('统计接收者'), owner = await account(request, ownerName), recipient = await account(request, recipientName);
  const created = await createQuestion(request, owner, {type: 'number', title: '权限隔离的数字题'}), qid = created.question._id, vid = created.version._id;
  const sharedResponse = await request.post(`/api/questions/${qid}/shares`, {headers: keyed(owner), data: {revision: created.question.revision, recipientUsername: recipientName}}); expect(sharedResponse.ok()).toBeTruthy(); const shared = await sharedResponse.json();
  const mine = await survey(request, owner, '主人只应看到自己的问卷', [reference(created, 'owner_local')]);
  const theirs = await survey(request, recipient, '接收者私有问卷', [reference(created, 'recipient_local')]);
  await submit(request, recipient, mine, [{questionId: 'owner_local', value: 5}]); await submit(request, owner, theirs, [{questionId: 'recipient_local', value: 55}]);
  const unused = await createQuestion(request, owner, {type: 'text', title: '尚未被本人引用的题目'});
  const revoked = await request.post(`/api/questions/${qid}/shares/${shared.share._id}/revoke`, {headers: keyed(owner), data: {revision: shared.questionRevision}}); expect(revoked.ok()).toBeTruthy(); const revocation = await revoked.json();
  expect((await request.patch(`/api/questions/${qid}`, {headers: keyed(owner), data: {revision: revocation.questionRevision, archived: true}})).ok()).toBeTruthy();
  expect((await request.get(`/api/questions/${qid}`, {headers: recipient})).status()).toBe(404);
  const context = await browser.newContext({baseURL}); const recipientPage = await context.newPage();
  try {
    await login(page, ownerName); await page.goto(statPath(qid, vid)); await metric(page, '引用问卷', 1); await metric(page, '已提交答卷', 1); await expect(page.locator('.cross-number-metrics dd')).toHaveText(['5', '5', '5']);
    await login(recipientPage, recipientName); await recipientPage.goto('/question-statistics'); await expect(recipientPage.getByRole('heading', {name: created.version.content.title, exact: true})).toBeVisible();
    await recipientPage.getByRole('link', {name: '选择版本并统计', exact: true}).click(); await metric(recipientPage, '引用问卷', 1); await expect(recipientPage.locator('.cross-number-metrics dd')).toHaveText(['55', '55', '55']); await shot(recipientPage, 'cross-statistics-revoked-snapshot');
    await page.goto(statPath(unused.question._id, unused.version._id)); await expect(page.getByRole('heading', {name: '尚无可统计的本人引用', exact: true}).first()).toBeVisible(); await expect(page.locator('.cross-stat-counts')).toHaveCount(0);
  } finally {await context.close();}
});

test('空值跳过零分母、目录搜索分页与慢请求不覆盖当前版本', async ({page, request}) => {
  test.setTimeout(180_000);
  const username = unique('空值及导航统计'), headers = await account(request, username);
  const gate = await createQuestion(request, headers, {type: 'single', title: '跳过后续的问题', options: [{id: 'skip', label: '结束'}, {id: 'next', label: '继续'}]});
  const choice = await createQuestion(request, headers, {type: 'multiple', title: '只有跳过与空值的多选', options: [{id: 'a', label: 'A'}, {id: 'b', label: 'B'}]});
  const number = await createQuestion(request, headers, {type: 'number', title: '只有空值的数字'});
  const target = await survey(request, headers, '检查空值统计', [reference(gate, 'gate', {jumpRules: [{id: 'end_rule', condition: {operator: 'eq', value: 'skip'}, target: {kind: 'end'}}]}), reference(choice, 'choice'), reference(number, 'number')]);
  await submit(request, headers, target, [{questionId: 'gate', value: 'skip'}]);
  await submit(request, headers, target, [{questionId: 'gate', value: 'next'}, {questionId: 'choice', value: null}, {questionId: 'number', value: null}]);
  const extra = [];
  for (let index = 0; index < 11; index++) extra.push(reference(await createQuestion(request, headers, {type: 'text', title: `目录分页题目${String(index).padStart(2, '0')}`}), `extra_${index}`));
  await survey(request, headers, '用于统计目录分页的草稿', extra, false);
  await login(page, username); await page.goto('/question-statistics'); await expect(page.locator('.statistic-question-grid > article')).toHaveCount(12);
  await page.getByRole('button', {name: '下一页', exact: true}).click(); await expect(page.locator('.statistic-question-grid > article')).toHaveCount(2);
  await page.getByLabel('搜索统计题目', {exact: true}).fill('目录分页题目10'); await expect(page.locator('.statistic-question-grid > article')).toHaveCount(1); await expect(page.getByRole('heading', {name: '目录分页题目10', exact: true})).toBeVisible();
  await page.getByLabel('搜索统计题目', {exact: true}).fill('搜索没有结果'); await expect(page.getByRole('heading', {name: '没有找到匹配的引用题目', exact: true})).toBeVisible();
  await page.goto(statPath(choice.question._id, choice.version._id)); await metric(page, '已提交答卷', 2); await metric(page, '有效回答', 0); await metric(page, '留空或跳过', 2); await expect(page.locator('.option-stat strong')).toHaveText(['0 次 · 暂无比例', '0 次 · 暂无比例']);
  await page.goto(statPath(number.question._id, number.version._id)); await expect(page.locator('.cross-number-metrics dd')).toHaveText(['暂无数据', '暂无数据', '暂无数据']); await expect(page.getByText('暂无有效回答原值', {exact: true})).toBeVisible(); await shot(page, 'cross-statistics-empty-number');
  let release, held = false, delivered;
  const pause = new Promise(resolve => {release = resolve;}), done = new Promise(resolve => {delivered = resolve;});
  await page.route(`**/api/question-statistics/${choice.question._id}/versions/${choice.version._id}`, async route => {const response = await route.fetch(); held = true; await pause; await route.fulfill({response}); delivered();});
  await page.goto('/question-statistics'); await page.getByRole('heading', {name: choice.version.content.title, exact: true}).click(); await expect.poll(() => held).toBeTruthy();
  await page.getByRole('link', {name: '返回统计题目目录', exact: true}).click(); await page.getByRole('heading', {name: number.version.content.title, exact: true}).click(); await expect(page.locator('.cross-stat-card h2')).toHaveText(number.version.content.title);
  release(); await done; await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); await expect(page.locator('.cross-stat-card h2')).toHaveText(number.version.content.title); await expect(page.locator('.option-stat')).toHaveCount(0);
});
