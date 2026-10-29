# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: management.spec.js >> 题库弹窗逐字符输入保持焦点，支持编辑、键盘导航和关闭回焦
- Location: tests\management.spec.js:48:1

# Error details

```
Error: expect(locator).toBeFocused() failed

Locator:  getByLabel('题库名称', { exact: true })
Expected: focused
Received: inactive
Timeout:  10000ms

Call log:
  - Expect "toBeFocused" getByLabel('题库名称', { exact: true }) with timeout 10000ms
  - waiting for getByLabel('题库名称', { exact: true })
    23 × locator resolved to <input value="课" maxlength="200" aria-label="题库名称"/>
       - unexpected value "inactive"

```

```yaml
- textbox "题库名称": 课
```

# Test source

```ts
  1   | import {test, expect} from '@playwright/test';
  2   | import {randomUUID} from 'node:crypto';
  3   | import {mkdir} from 'node:fs/promises';
  4   | import path from 'node:path';
  5   | 
  6   | const PASSWORD = 'Management-e2e-2026!';
  7   | const name = prefix => `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  8   | const content = title => ({type: 'text', title, description: '', validation: {maxLength: 300}});
  9   | const keyHeaders = headers => ({...headers, 'Idempotency-Key': randomUUID()});
  10  | async function account(request, username) {
  11  |   expect((await request.post('/api/auth/register', {data: {username, password: PASSWORD}})).status()).toBe(201);
  12  |   const login = await request.post('/api/auth/login', {data: {username, password: PASSWORD}});
  13  |   return {Authorization: `Bearer ${(await login.json()).access_token}`};
  14  | }
  15  | async function login(page, username) {
  16  |   await page.goto('/login'); await page.getByLabel('用户名', {exact: true}).fill(username); await page.getByLabel('密码', {exact: true}).fill(PASSWORD);
  17  |   await page.getByRole('button', {name: '登录', exact: true}).click(); await expect(page).toHaveURL('/');
  18  | }
  19  | async function createQuestion(request, headers, title) {
  20  |   const response = await request.post('/api/questions', {headers: keyHeaders(headers), data: {content: content(title)}});
  21  |   expect(response.status()).toBe(201); return response.json();
  22  | }
  23  | async function seedSurvey(request, headers, title, questionId, versionId) {
  24  |   const response = await request.post('/api/surveys', {headers: keyHeaders(headers), data: {title, description: '', settings: {anonymous: true, allowRepeat: false}, deadlineAt: null, questions: [{id: 'question_one', questionId, versionId, required: true, jumpRules: []}]}});
  25  |   expect(response.status()).toBe(201); return response.json();
  26  | }
  27  | async function share(page, username) {
  28  |   await page.getByLabel('接收者用户名', {exact: true}).fill(username); await page.getByRole('button', {name: '共享整道题目', exact: true}).click();
  29  |   await page.getByRole('dialog').getByRole('button', {name: '确认共享', exact: true}).click();
  30  |   await expect(page.locator('.library-row').filter({hasText: username})).toContainText('有效');
  31  | }
  32  | async function createBank(page, bankName) {
  33  |   await page.goto('/question-banks'); await page.getByRole('button', {name: '创建题库', exact: true}).click();
  34  |   await page.getByLabel('题库名称', {exact: true}).fill(bankName); await page.getByLabel('题库说明', {exact: true}).fill('课程团队的分类，仅整理题目。');
  35  |   await page.getByRole('dialog').getByRole('button', {name: '保存题库', exact: true}).click(); await expect(page).toHaveURL(/\/question-banks\/[a-f0-9]{24}$/);
  36  |   await expect(page.getByRole('heading', {name: `${bankName}.`, exact: true})).toBeVisible(); return page.url().split('/').at(-1);
  37  | }
  38  | async function shot(page, label) {
  39  |   const directory = path.join(process.env.E2E_OUTPUT_DIR || '../output/frontend-e2e', 'screenshots'); await mkdir(directory, {recursive: true});
  40  |   await page.screenshot({path: path.join(directory, `${label}.png`), fullPage: true, animations: 'disabled'});
  41  | }
  42  | async function mobileShot(page, label) {
  43  |   const previous = page.viewportSize(); await page.setViewportSize({width: 390, height: 844});
  44  |   try {await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy(); await shot(page, label);}
  45  |   finally {await page.setViewportSize(previous); await page.mouse.move(0, 0); await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
  46  | }
  47  | 
  48  | test('题库弹窗逐字符输入保持焦点，支持编辑、键盘导航和关闭回焦', async ({page, request}) => {
  49  |   const username = name('题库输入检查'), headers = await account(request, username);
  50  |   await login(page, username);
  51  |   await page.goto('/question-banks');
  52  |   const create = page.getByRole('button', {name: '创建题库', exact: true});
  53  |   const dialog = page.getByRole('dialog');
  54  |   const bankName = page.getByLabel('题库名称', {exact: true});
  55  |   const description = page.getByLabel('题库说明', {exact: true});
  56  |   const previousOverflow = await page.evaluate(() => document.body.style.overflow);
  57  |   const typeCharacters = async (field, text) => {
  58  |     let expected = await field.inputValue();
  59  |     for (const character of text) {
  60  |       if (character === '\n') await page.keyboard.press('Enter');
  61  |       else await page.keyboard.insertText(character);
  62  |       expected += character;
  63  |       await expect(field).toHaveValue(expected);
> 64  |       await expect(field).toBeFocused();
      |                           ^ Error: expect(locator).toBeFocused() failed
  65  |     }
  66  |   };
  67  | 
  68  |   await create.click();
  69  |   await expect(dialog).toBeFocused();
  70  |   await page.keyboard.press('Shift+Tab');
  71  |   await expect(dialog.getByRole('button', {name: '保存题库', exact: true})).toBeFocused();
  72  |   await page.keyboard.press('Tab');
  73  |   await expect(dialog.getByRole('button', {name: '关闭对话框', exact: true})).toBeFocused();
  74  |   await page.keyboard.press('Shift+Tab');
  75  |   await expect(dialog.getByRole('button', {name: '保存题库', exact: true})).toBeFocused();
  76  |   await bankName.click();
  77  |   await typeCharacters(bankName, '课程反馈Ab');
  78  |   await page.keyboard.press('Home');
  79  |   await page.keyboard.press('ArrowRight');
  80  |   await page.keyboard.press('ArrowRight');
  81  |   await page.keyboard.insertText('·');
  82  |   await expect(bankName).toHaveValue('课程·反馈Ab');
  83  |   await expect(bankName).toBeFocused();
  84  |   expect(await bankName.evaluate(element => element.selectionStart)).toBe(3);
  85  |   await page.keyboard.press('Backspace');
  86  |   await expect(bankName).toHaveValue('课程反馈Ab');
  87  |   await description.click();
  88  |   await typeCharacters(description, '第一行\n第二行');
  89  |   await page.keyboard.press('Escape');
  90  |   await expect(dialog).toHaveCount(0);
  91  |   await expect(create).toBeFocused();
  92  |   expect(await page.evaluate(() => document.body.style.overflow)).toBe(previousOverflow);
  93  | 
  94  |   await create.click();
  95  |   await expect(bankName).toHaveValue('');
  96  |   await bankName.click();
  97  |   await typeCharacters(bankName, '连续输入课程题库');
  98  |   await description.click();
  99  |   await typeCharacters(description, '创建时的说明');
  100 |   await dialog.getByRole('button', {name: '保存题库', exact: true}).click();
  101 |   await expect(page).toHaveURL(/\/question-banks\/[a-f0-9]{24}$/);
  102 |   const bankId = page.url().split('/').at(-1);
  103 |   const edit = page.getByRole('button', {name: '编辑题库', exact: true});
  104 |   await edit.click();
  105 |   await bankName.click();
  106 |   await page.keyboard.press('End');
  107 |   await typeCharacters(bankName, '·修订');
  108 |   await description.click();
  109 |   await page.keyboard.press('End');
  110 |   await typeCharacters(description, '\n编辑后保留光标');
  111 | 
  112 |   let releaseSave;
  113 |   const saveGate = new Promise(resolve => {releaseSave = resolve;});
  114 |   await page.route(`**/api/question-banks/${bankId}`, async route => {
  115 |     if (route.request().method() === 'PATCH') {
  116 |       await saveGate;
  117 |       await route.continue();
  118 |     } else await route.continue();
  119 |   });
  120 |   try {
  121 |     await dialog.getByRole('button', {name: '保存题库', exact: true}).click();
  122 |     await expect(dialog.getByRole('button', {name: '取消', exact: true})).toBeDisabled();
  123 |     await page.keyboard.press('Escape');
  124 |     await expect(dialog).toBeVisible();
  125 |   } finally {releaseSave();}
  126 |   await expect(dialog).toHaveCount(0);
  127 |   await expect(edit).toBeFocused();
  128 |   await expect(page.getByRole('heading', {name: '连续输入课程题库·修订.', exact: true})).toBeVisible();
  129 |   const saved = await (await request.get(`/api/question-banks/${bankId}`, {headers})).json();
  130 |   expect(saved.name).toBe('连续输入课程题库·修订');
  131 |   expect(saved.description).toBe('创建时的说明\n编辑后保留光标');
  132 | 
  133 |   await edit.click();
  134 |   await bankName.click();
  135 |   await page.keyboard.press('End');
  136 |   await typeCharacters(bankName, '取消修改');
  137 |   await page.keyboard.press('Escape');
  138 |   await expect(dialog).toHaveCount(0);
  139 |   await expect(edit).toBeFocused();
  140 |   expect(await page.evaluate(() => document.body.style.overflow)).toBe(previousOverflow);
  141 | });
  142 | 
  143 | test('历史恢复、整题共享、独立副本再共享、撤权后旧草稿发布、引用隐私和题库管理', async ({page, browser, request, baseURL}) => {
  144 |   test.setTimeout(180_000);
  145 |   const errors = []; page.on('pageerror', error => errors.push(error.message));
  146 |   const owner = name('题目所有者'), recipient = name('共享接收者'), third = name('副本接收者');
  147 |   const [ownerHeaders, recipientHeaders, thirdHeaders] = await Promise.all([account(request, owner), account(request, recipient), account(request, third)]);
  148 |   const oldTitle = '团队课程反馈：最希望改善的环节', editedTitle = '团队课程反馈：下一步改进建议';
  149 |   const created = await createQuestion(request, ownerHeaders, oldTitle), questionId = created.question._id;
  150 |   const editedResponse = await request.post(`/api/questions/${questionId}/versions`, {headers: keyHeaders(ownerHeaders), data: {revision: created.question.revision, baseVersionId: created.version._id, content: content(editedTitle)}});
  151 |   expect(editedResponse.status()).toBe(201);
  152 |   const ownerSurvey = await seedSurvey(request, ownerHeaders, '题目所有者自己的问卷', questionId, created.version._id);
  153 |   const recipientContext = await browser.newContext({baseURL}), thirdContext = await browser.newContext({baseURL});
  154 |   const recipientPage = await recipientContext.newPage(), thirdPage = await thirdContext.newPage();
  155 |   recipientPage.on('pageerror', error => errors.push(error.message)); thirdPage.on('pageerror', error => errors.push(error.message));
  156 |   try {
  157 |     await login(page, owner); await page.goto(`/questions/${questionId}`);
  158 |     await page.locator('.version-row').filter({hasText: `v1 · ${oldTitle}`}).click();
  159 |     await page.getByRole('button', {name: '将 v1 恢复为新版本', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认操作', exact: true}).click();
  160 |     await expect(page.getByText('内容来源：v1', {exact: true})).toBeVisible();
  161 |     await expect(page.locator('.version-row')).toHaveCount(3); await expect(page.getByText('当前查看 v3，最新版本 v3', {exact: true})).toBeVisible();
  162 |     await page.locator('.version-row').filter({hasText: `v2 · ${editedTitle}`}).click(); await page.getByRole('button', {name: '归档本版本', exact: true}).click(); await page.getByRole('dialog').getByRole('button', {name: '确认操作', exact: true}).click();
  163 |     await page.getByLabel('历史版本归档筛选', {exact: true}).selectOption('only'); await expect(page.locator('.version-row')).toHaveCount(1); await expect(page.locator('.version-row')).toContainText('v2');
  164 |     await page.getByLabel('历史版本归档筛选', {exact: true}).selectOption('include'); await share(page, recipient);
```