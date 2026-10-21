import { test, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = path.join(process.env.E2E_OUTPUT_DIR || path.join(ROOT, 'output/frontend-e2e'), 'screenshots');
const PASSWORD = 'Survey-e2e-correct-2026!';
const titles = {
  survey: '数据库课程学习体验调查',
  single: '你是否参加过课程实验？',
  multiple: '你喜欢哪些学习环节？',
  number: '你每周学习多少小时？',
  text: '你希望增加哪些内容？',
};
const uniqueName = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;

async function screenshot(page, name, fullPage = false) {
  await mkdir(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage, animations: 'disabled' });
}

async function mobileScreenshot(page, name) {
  const previous = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  try {
    if (await page.locator('.sidebar').count()) {
      await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await screenshot(page, name);
  } finally {
    await page.setViewportSize(previous);
  }
}

async function registerApi(request, username) {
  const registered = await request.post('/api/auth/register', { data: { username, password: PASSWORD } });
  expect(registered.status()).toBe(201);
  const login = await request.post('/api/auth/login', { data: { username, password: PASSWORD } });
  expect(login.ok()).toBeTruthy();
  return { Authorization: `Bearer ${(await login.json()).access_token}` };
}

async function loginUi(page, username) {
  await page.goto('/login');
  await page.getByLabel('用户名', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我的问卷.', exact: true })).toBeVisible();
}

async function currentHeaders(page) {
  const token = await page.evaluate(() => sessionStorage.getItem('wenxu.access'));
  expect(token).toBeTruthy();
  return { Authorization: `Bearer ${token}` };
}

async function seededSurvey(request, headers, title, questions, status = 'draft') {
  const created = await request.post('/api/surveys', { headers, data: {
    title, description: '用真实回答，帮助我们把下一步安排得更好。',
    settings: { anonymous: true, allowRepeat: false }, deadlineAt: null, questions,
  } });
  expect(created.status()).toBe(201);
  let survey = await created.json();
  if (status !== 'draft') {
    const published = await request.post(`/api/surveys/${survey._id}/publish`, { headers, data: { revision: survey.revision } });
    expect(published.ok()).toBeTruthy();
    survey = await published.json();
  }
  if (status === 'closed') {
    const closed = await request.post(`/api/surveys/${survey._id}/close`, { headers, data: { revision: survey.revision } });
    expect(closed.ok()).toBeTruthy();
    survey = await closed.json();
  }
  return survey;
}

test('未登录访问受保护页面会返回登录，错误凭据有明确反馈', async ({ page }) => {
  await page.goto('/surveys/new');
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByLabel('用户名', { exact: true }).fill(uniqueName('不存在的账号'));
  await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('用户名或密码错误');
  await expect(page).toHaveURL(/\/login/);
});

test('创建四类题目并发布，匿名填写、修改清空、提交、统计与移动端完整闭环', async ({ page, browser, request, baseURL }) => {
  test.setTimeout(120_000);
  const runtimeErrors = [];
  page.on('pageerror', error => runtimeErrors.push(error.message));
  const ownerName = '林同学';

  await test.step('通过注册表单创建账号', async () => {
    await page.goto('/register');
    await page.getByLabel('用户名', { exact: true }).fill(ownerName);
    await page.getByLabel('密码', { exact: true }).fill(PASSWORD);
    await page.getByLabel('确认密码', { exact: true }).fill(PASSWORD);
    await page.getByRole('button', { name: '注册并开始使用', exact: true }).click();
    await expect(page.getByRole('heading', { name: '我的问卷.', exact: true })).toBeVisible();
    await page.goto('/surveys/new');
  });

  await test.step('用界面配置四种题型、校验和向后跳转', async () => {
    await page.getByLabel('问卷标题', { exact: true }).fill(titles.survey);
    await page.getByLabel('问卷说明', { exact: true }).fill('想听听你对课程学习与上机实践的真实感受。问卷约需 2 分钟，所有回答统一匿名，感谢你的认真参与。');
    await page.getByRole('button', { name: '发布问卷', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('至少添加一道题目');
    await page.getByRole('button', { name: '添加单选题', exact: true }).click();
    await page.getByLabel('第1题题目', { exact: true }).fill(titles.single);
    await page.getByLabel('第1题选项1', { exact: true }).fill('参加过');
    await page.getByLabel('第1题选项2', { exact: true }).fill('未参加过');
    await page.getByRole('button', { name: '添加多选题', exact: true }).click();
    await page.getByLabel('第2题题目', { exact: true }).fill(titles.multiple);
    await page.getByLabel('第2题选项1', { exact: true }).fill('课堂讲解');
    await page.getByLabel('第2题选项2', { exact: true }).fill('上机实践');
    await page.getByTestId('question-editor').nth(1).getByRole('button', { name: '添加选项', exact: true }).click();
    await page.getByLabel('第2题选项3', { exact: true }).fill('小组讨论');
    await page.getByLabel('第2题最少选择项数', { exact: true }).fill('1');
    await page.getByLabel('第2题最多选择项数', { exact: true }).fill('2');
    await page.getByRole('button', { name: '添加数字填空', exact: true }).click();
    await page.getByLabel('第3题题目', { exact: true }).fill(titles.number);
    await page.getByLabel('第3题最小值', { exact: true }).fill('0');
    await page.getByLabel('第3题最大值', { exact: true }).fill('168');
    await page.getByLabel('第3题仅允许整数', { exact: true }).check();
    await page.getByRole('button', { name: '添加文本填空', exact: true }).click();
    await page.getByLabel('第4题题目', { exact: true }).fill(titles.text);
    await page.getByTestId('question-editor').nth(3).getByLabel('必答题', { exact: true }).uncheck();
    await page.getByLabel('第4题最多字符数', { exact: true }).fill('100');
    const numberId = (await page.getByLabel('第3题题目', { exact: true }).getAttribute('id')).replace(/-title$/, '');
    await page.getByTestId('question-editor').nth(0).getByRole('button', { name: '添加跳转规则', exact: true }).click();
    await page.getByLabel('第1题规则1匹配选项', { exact: true }).selectOption({ label: '未参加过' });
    await page.getByLabel('第1题规则1跳转目标', { exact: true }).selectOption(numberId);
    await expect(page.getByRole('radio', { name: '匿名问卷', exact: true })).toBeChecked();
    await expect(page.getByLabel('允许同一账号多次填写', { exact: true })).not.toBeChecked();
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await expect(page).toHaveURL(/\/surveys\/[a-f0-9]{24}\/edit$/);
    await expect(page.getByText('所有修改已保存', { exact: true })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await screenshot(page, 'desktop-editor');
    await mobileScreenshot(page, 'mobile-editor');
  });

  const headers = await currentHeaders(page);
  const surveyId = page.url().match(/surveys\/([a-f0-9]{24})\/edit/)[1];
  const savedResponse = await request.get(`/api/surveys/${surveyId}`, { headers });
  expect(savedResponse.ok()).toBeTruthy();
  let survey = await savedResponse.json();
  expect(survey.questions.map(q => q.type)).toEqual(['single', 'multiple', 'number', 'text']);
  expect(survey.questions[0].jumpRules[0].target.questionId).toBe(survey.questions[2].id);

  await test.step('发布后题目、规则与匿名设置锁定', async () => {
    await page.getByRole('button', { name: '发布问卷', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '确认发布', exact: true }).click();
    await expect(page.getByLabel('问卷分享链接', { exact: true })).toHaveValue(new RegExp(`/survey/${survey.publicId}$`));
    await page.getByRole('dialog').getByRole('button', { name: '完成', exact: true }).click();
    await expect(page.getByLabel('第1题题目', { exact: true })).toBeDisabled();
    await expect(page.getByLabel('第1题规则1跳转目标', { exact: true })).toBeDisabled();
    await expect(page.getByRole('radio', { name: '匿名问卷', exact: true })).toBeDisabled();
    await expect(page.getByLabel('问卷标题', { exact: true })).toBeEnabled();
  });

  const participantName = '陈同学';
  await registerApi(request, participantName);
  const participantContext = await browser.newContext({ baseURL, viewport: { width: 1440, height: 1000 } });
  const participant = await participantContext.newPage();
  participant.on('pageerror', error => runtimeErrors.push(error.message));
  try {
    await test.step('另一浏览器会话登录后填写，必答题有服务端校验', async () => {
      await participant.goto(`/survey/${survey.publicId}`);
      await expect(participant).toHaveURL(/\/login\?next=/);
      await participant.getByLabel('用户名', { exact: true }).fill(participantName);
      await participant.getByLabel('密码', { exact: true }).fill(PASSWORD);
      await participant.getByRole('button', { name: '登录', exact: true }).click();
      await expect(participant.getByRole('heading', { name: titles.survey, exact: true })).toBeVisible();
      await expect(participant.getByText('你的答卷不记录账号身份', { exact: true })).toBeVisible();
      await participant.getByRole('button', { name: '开始填写', exact: true }).click();
      await expect(participant.getByRole('heading', { name: titles.single, exact: true })).toBeVisible();
      await screenshot(participant, 'desktop-filling');
      await mobileScreenshot(participant, 'mobile-filling');
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByRole('alert')).toBeVisible();
      await participant.getByRole('radio', { name: '参加过', exact: true }).check();
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await participant.getByRole('checkbox', { name: '课堂讲解', exact: true }).check();
      await participant.getByRole('checkbox', { name: '上机实践', exact: true }).check();
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await participant.getByLabel(titles.number, { exact: true }).fill('8');
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await participant.getByLabel(titles.text, { exact: true }).fill('原先填写的建议');
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByRole('heading', { name: '回答已就绪，提交前再看一眼', exact: true })).toBeVisible();
    });

    await test.step('只返回查看不清空，修改前面答案后清空所有后续答案并重新跳转', async () => {
      await participant.getByRole('button', { name: `修改：${titles.single}`, exact: true }).click();
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByRole('checkbox', { name: '课堂讲解', exact: true })).toBeChecked();
      await expect(participant.getByRole('checkbox', { name: '上机实践', exact: true })).toBeChecked();
      await participant.getByRole('button', { name: '上一题', exact: true }).click();
      await participant.getByRole('radio', { name: '未参加过', exact: true }).check();
      await expect(participant.getByText('继续后将清空本题之后的答案，并重新计算填写路径。', { exact: true })).toBeVisible();
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByRole('heading', { name: titles.number, exact: true })).toBeVisible();
      await expect(participant.getByLabel(titles.number, { exact: true })).toHaveValue('');
      await participant.getByLabel(titles.number, { exact: true }).fill('12');
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByLabel(titles.text, { exact: true })).toHaveValue('');
      await participant.getByLabel(titles.text, { exact: true }).fill('增加 MongoDB 真实项目案例');
      await participant.getByRole('button', { name: '下一步', exact: true }).click();
      await expect(participant.getByRole('button', { name: `修改：${titles.multiple}`, exact: true })).toHaveCount(0);
      await participant.getByRole('button', { name: '确认提交', exact: true }).click();
      await expect(participant.getByRole('heading', { name: '已收到你的回答', exact: true })).toBeVisible();
      await expect(participant.getByText('本次答卷未记录你的账号身份', { exact: true })).toBeVisible();
    });

    await test.step('禁止同一账号重复填写', async () => {
      await participant.goto(`/survey/${survey.publicId}`);
      await expect(participant.getByRole('button', { name: '你已提交过这份问卷', exact: true })).toBeDisabled();
    });
  } finally {
    await participantContext.close();
  }

  await test.step('独立账号的真实提交为统计页补充样本', async () => {
    for (const [index, hours] of [6, 10, 14].entries()) {
      const actor = await registerApi(request, uniqueName(`样本同学${index}`));
      const started = await request.post(`/api/public/surveys/${survey.publicId}/start`, { headers: actor });
      expect(started.ok()).toBeTruthy();
      const attempt = await started.json();
      const q = survey.questions;
      const selected = index === 1 ? [q[1].options[1].id, q[1].options[2].id] : [q[1].options[0].id, q[1].options[1].id];
      const submitted = await request.post(`/api/public/surveys/${survey.publicId}/submit`, { headers: actor, data: {
        ...attempt, answers: [
          { questionId: q[0].id, value: q[0].options[0].id },
          { questionId: q[1].id, value: selected },
          { questionId: q[2].id, value: hours },
          { questionId: q[3].id, value: ['增加练习题解析', '希望有更多案例讨论', '保持上机实践环节'][index] },
        ],
      } });
      expect(submitted.status()).toBe(201);
    }
    const responses = await request.get(`/api/surveys/${survey._id}/responses`, { headers });
    const raw = await responses.json();
    expect(raw.total).toBe(4);
    expect(raw.items.every(item => !('respondentId' in item) && !('respondentName' in item) && !('submissionKey' in item))).toBeTruthy();
  });

  await test.step('结果页面使用真实统计，单题内容和原始匿名答卷可查看', async () => {
    await page.goto(`/surveys/${survey._id}/results`);
    await expect(page.getByRole('heading', { name: '答卷与统计.', exact: true })).toBeVisible();
    await expect(page.locator('.stat-card')).toHaveCount(4);
    await expect(page.locator('.number-stat strong')).toHaveText('10.5');
    await screenshot(page, 'desktop-results', true);
    await page.getByLabel('选择统计题目', { exact: true }).selectOption(survey.questions[3].id);
    await expect(page.getByText('增加 MongoDB 真实项目案例', { exact: true })).toBeVisible();
    await page.getByRole('tab', { name: '原始答卷', exact: true }).click();
    await expect(page.locator('.response-card')).toHaveCount(4);
    await expect(page.getByText('匿名答卷', { exact: true })).toHaveCount(4);
  });

  await test.step('工作空间状态筛选、搜索、真实计数与移动端布局', async () => {
    await seededSurvey(request, headers, '课程作业负担与节奏反馈', survey.questions, 'published');
    await seededSurvey(request, headers, '校园阅读空间使用调研', survey.questions, 'draft');
    await seededSurvey(request, headers, '新生学习支持需求', survey.questions, 'draft');
    await seededSurvey(request, headers, '课程小组协作体验', survey.questions, 'closed');
    await page.goto('/');
    await expect(page.locator('.survey-card')).toHaveCount(5);
    const mainCard = page.locator('.survey-card').filter({ has: page.getByRole('heading', { name: titles.survey, exact: true }) });
    await expect(mainCard.getByText('4 份答卷', { exact: true })).toBeVisible();
    await page.getByRole('tab', { name: '草稿', exact: true }).click();
    await expect(page.locator('.survey-card')).toHaveCount(2);
    await page.getByRole('tab', { name: '收集中', exact: true }).click();
    await expect(page.locator('.survey-card')).toHaveCount(2);
    await page.getByRole('tab', { name: '全部问卷', exact: false }).click();
    await page.getByLabel('搜索问卷', { exact: true }).fill('数据库');
    await expect(page.locator('.survey-card')).toHaveCount(1);
    await page.getByRole('button', { name: '清空搜索', exact: true }).click();
    await expect(page.locator('.survey-card')).toHaveCount(5);
    await screenshot(page, 'desktop-dashboard');
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('button', { name: '打开导航', exact: true })).toBeVisible();
    await expect.poll(() => page.locator('.sidebar').evaluate(element => element.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await screenshot(page, 'mobile-dashboard', true);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  await test.step('关闭后不再接受填写', async () => {
    await page.goto(`/surveys/${survey._id}/edit`);
    await page.getByRole('button', { name: '关闭问卷', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '确认关闭', exact: true }).click();
    await expect(page.getByLabel('问卷标题', { exact: true })).toBeDisabled();
    await page.goto(`/survey/${survey.publicId}`);
    await expect(page.getByRole('heading', { name: '这份问卷已结束', exact: true })).toBeVisible();
  });
  expect(runtimeErrors).toEqual([]);
});

test('草稿版本冲突保留本地输入，确认重新加载后读取服务器最新值', async ({ page, request }) => {
  const name = uniqueName('版本验证');
  const headers = await registerApi(request, name);
  const questions = [{ id: 'q1', type: 'text', title: '你的建议', description: '', required: false, validation: {}, jumpRules: [] }];
  const survey = await seededSurvey(request, headers, '原始标题', questions);
  await loginUi(page, name);
  await page.goto(`/surveys/${survey._id}/edit`);
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('原始标题');
  const update = await request.patch(`/api/surveys/${survey._id}`, { headers, data: { revision: survey.revision, title: '服务器最新标题' } });
  expect(update.ok()).toBeTruthy();
  await page.getByLabel('问卷标题', { exact: true }).fill('本地尚未保存的标题');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('问卷已被修改');
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('本地尚未保存的标题');
  await page.getByRole('button', { name: '重新加载', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: '放弃修改并重新加载', exact: true }).click();
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('服务器最新标题');
});

test('服务器已保存但响应丢失时，保留原答案和凭据重试且只生成一份答卷', async ({ page, request }) => {
  const owner = await registerApi(request, uniqueName('重试创建者'));
  const fillerName = uniqueName('重试填写者');
  await registerApi(request, fillerName);
  const question = { id: 'q1', type: 'text', title: '请留下你的建议', description: '', required: true, validation: {}, jumpRules: [] };
  const survey = await seededSurvey(request, owner, '提交确认与网络重试验证', [question], 'published');
  await loginUi(page, fillerName);
  await page.goto(`/survey/${survey.publicId}`);
  await page.getByRole('button', { name: '开始填写', exact: true }).click();
  await page.getByLabel(question.title, { exact: true }).fill('服务器实际已保存的回答');
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  const attempts = [];
  await page.route(`**/api/public/surveys/${survey.publicId}/submit`, async route => {
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) {
      // This is a real server commit. Only its browser response is discarded.
      const committed = await route.fetch();
      expect(committed.status()).toBe(201);
      await route.abort('failed');
    } else {
      await route.continue();
    }
  });
  await page.getByRole('button', { name: '确认提交', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试原提交', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: `修改：${question.title}`, exact: true })).toBeDisabled();
  await expect(page.getByText('提交结果暂未确认。请保留本页并重试，系统会确认同一次提交，不会重复生成答卷。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '重试原提交', exact: true }).click();
  await expect(page.getByRole('heading', { name: '已收到你的回答', exact: true })).toBeVisible();
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toEqual(attempts[0]);
  const saved = await request.get(`/api/surveys/${survey._id}/responses`, { headers: owner });
  expect(saved.ok()).toBeTruthy();
  const data = await saved.json();
  expect(data.total).toBe(1);
  expect(data.items[0].answers).toEqual([{ questionId: 'q1', value: '服务器实际已保存的回答' }]);
});

test('编辑保存收到真实 401 后原地重新登录，未保存修改保持并可继续保存', async ({ page, request }) => {
  const name = uniqueName('登录恢复');
  const owner = await registerApi(request, name);
  const question = { id: 'q1', type: 'text', title: '你的建议', description: '', required: false, validation: {}, jumpRules: [] };
  const survey = await seededSurvey(request, owner, '登录恢复前的标题', [question]);
  await loginUi(page, name);
  await page.goto(`/surveys/${survey._id}/edit`);
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('登录恢复前的标题');
  await page.getByLabel('问卷标题', { exact: true }).fill('登录恢复后继续保存的标题');
  let rejected = false;
  await page.route(`**/api/surveys/${survey._id}`, async route => {
    if (route.request().method() === 'PATCH' && !rejected) {
      rejected = true;
      // The real backend validates this invalid bearer and returns its real 401.
      await route.continue({ headers: { ...route.request().headers(), authorization: 'Bearer invalid' } });
    } else {
      await route.continue();
    }
  });
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  const modal = page.getByRole('dialog', { name: '登录已过期，当前修改已保留', exact: true });
  await expect(modal).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/surveys/${survey._id}/edit$`));
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('登录恢复后继续保存的标题');
  await modal.getByLabel('原账号密码', { exact: true }).fill('wrong-password');
  await modal.getByRole('button', { name: '重新登录', exact: true }).click();
  await expect(modal.getByRole('alert')).toContainText('用户名或密码错误');
  await modal.getByLabel('原账号密码', { exact: true }).fill(PASSWORD);
  await modal.getByRole('button', { name: '重新登录', exact: true }).click();
  await expect(modal).toHaveCount(0);
  await expect(page.getByLabel('问卷标题', { exact: true })).toHaveValue('登录恢复后继续保存的标题');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.getByText('所有修改已保存', { exact: true })).toBeVisible();
  const stored = await request.get(`/api/surveys/${survey._id}`, { headers: owner });
  expect(stored.ok()).toBeTruthy();
  expect((await stored.json()).title).toBe('登录恢复后继续保存的标题');
});
