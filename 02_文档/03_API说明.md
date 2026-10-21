# 问序 API 说明

本文说明第一阶段接口。默认地址 `http://127.0.0.1:8000`，代码入口为 [路由](../01_代码/backend/app/main.py)、[请求模型](../01_代码/backend/app/models.py) 和 [业务服务](../01_代码/backend/app/service.py)。

管理路径的 `{survey_id}` 取问卷 `_id`（24 位十六进制），填写路径的 `{public_id}` 取 `publicId`（32 位小写十六进制），不能互换。示例中的资源 ID、token 和 key 需替换为实际返回值，`revision` 取最新值。

## 1．接口与数据约定

### 1.1 传输格式

- 有请求体的业务接口使用 `Content-Type: application/json`；查询参数放在 URL 中。
- JSON 字段采用 `publicId`、`deadlineAt`、`questionId` 等名称，区分大小写。
- 数据库 ObjectId 在响应中序列化为 24 位十六进制字符串；分享标识 `publicId` 为 32 位小写十六进制字符串。
- 日期请求使用带时区的 ISO 8601 字符串，例如 `2099-12-01T23:59:59+08:00`，或允许位置的 `null`。不接受数值时间戳及不带时区的日期。后端转为 UTC，截取至毫秒精度后存储和比较。
- JSON 中的数字、布尔值使用真实类型，例如 `12`、`true`，不能写成 `"12"`、`"true"`，也不能用布尔值代替数字。URL 查询参数仍按正常 URL 文本传递。
- 请求对象禁止未声明字段。客户端不能自行指定 `_id`、`ownerId`、状态、服务器时间、答卷填写者身份等服务端字段。
- 标题、题干、选项文字和文本答案去除首尾空白；密码不做去空白或大小写转换。
- 默认请求体上限为 12 MiB，可由服务器配置调整；过大或非法长度返回 413。最终 BSON 文档超过 MongoDB 16 MiB 上限也返回 413，各字段另有容量约束。

### 1.2 身份与权限

除注册、登录和健康检查外，全部业务接口需登录，包含 `/api/public/`；创建者只能管理自己的问卷，其他账号访问管理资源返回 404。

| 凭据 | 来源 | 携带位置 |
|---|---|---|
| 登录 `access_token` | `/api/auth/login` | `Authorization: Bearer <access_token>` |
| 填写 `submissionToken` | 分享问卷的 `/start` | 仅在 `/submit` 的 JSON 请求体 |

两类签名 JWT 用途不能互换，默认有效期分别为 120、1440 分钟。`submit` 同时需要登录请求头与本次填写凭据；其他填写接口无需填写凭据。匿名也需登录，但答卷不保存直接账号引用。

### 1.3 分页

| 接口 | 参数 | 默认值与范围 | 返回顺序 |
|---|---|---|---|
| 我的问卷列表 | `page`、`pageSize` | `page=1`，`pageSize=20`；页码至少 1，每页 1～100 | `createdAt`、`_id` 倒序 |
| 问卷答卷列表 | 同上 | 同上 | `submittedAt`、`_id` 倒序 |
| 文本或数字单题填写内容 | 同上，另需 `questionId` | 同上 | 按提交时间及答卷 ID 倒序 |

列表响应为 `{"items":[],"total":0,"page":1,"pageSize":20}`。超出最后一页时，`items` 可以为空，`total` 仍是符合条件的总数。问卷统计接口的分页只作用于指定文本/数字题的 `values`，不作用于问卷总答卷数、平均值或选项计数。

## 2．完整接口表

以下为 16 项业务与健康检查操作。同一路径的 GET、POST 或 PATCH 分别算不同操作。

所有需登录操作均可能返回 401；存在请求体、路径或查询参数的操作可能返回 422；数据库操作异常可能返回 503。POST/PATCH 还受请求体大小限制，可能返回 413。表内列出各接口主要业务错误，完整错误码见第 9 节。

| 方法与路径 | 权限 | 请求 | 成功响应 | 主要业务错误 |
|---|---|---|---|---|
| `GET /health` | 无需登录 | 无 | 200，`{"status":"ok"}` | 503，数据库检查失败 |
| `POST /api/auth/register` | 无需登录 | `username`、`password` | 201，用户公开信息 | 409 用户名占用；422 用户名/密码非法 |
| `POST /api/auth/login` | 无需登录 | `username`、`password` | 200，`access_token`、`token_type` | 401 用户名或密码错误 |
| `GET /api/auth/me` | 已登录 | 无 | 200，当前用户公开信息 | 401 令牌无效、过期或账号不存在 |
| `POST /api/surveys` | 已登录 | `SurveyCreate`，见第 4 节 | 201，完整草稿文档 | 422 配置/截止时间非法；503 标识分配失败 |
| `GET /api/surveys` | 已登录，仅自己的问卷 | 分页；可选 `status`、`search` | 200，分页列表 | 422 查询参数非法 |
| `GET /api/surveys/{survey_id}` | 问卷创建者 | 路径 ID | 200，完整问卷文档 | 404 不存在、ID 非法或不属于本人 |
| `PATCH /api/surveys/{survey_id}` | 问卷创建者 | `revision` 及待修改字段 | 200，更新后的完整问卷文档 | 404；409 修订号冲突、配置锁定或已关闭；422 空更新/非法配置 |
| `POST /api/surveys/{survey_id}/publish` | 问卷创建者 | `{"revision":当前值}` | 200，已发布问卷文档 | 404；409 非草稿或版本冲突；422 空题目/配置/截止时间非法 |
| `POST /api/surveys/{survey_id}/close` | 问卷创建者 | `{"revision":当前值}` | 200，已关闭问卷文档 | 404；409 非已发布状态或版本冲突 |
| `GET /api/surveys/{survey_id}/responses` | 问卷创建者 | 路径 ID、分页 | 200，答卷分页列表 | 404 |
| `GET /api/surveys/{survey_id}/statistics` | 问卷创建者 | 可选 `questionId`、分页 | 200，整份问卷或单题统计 | 404 问卷或指定题目不存在 |
| `GET /api/public/surveys/{public_id}` | 已登录的填写者 | 分享标识 | 200，可填写定义及 `canSubmit` | 404 不存在或草稿；410 已关闭/截止 |
| `POST /api/public/surveys/{public_id}/start` | 已登录的填写者 | 无需请求体 | 200，`submissionKey`、`submissionToken` | 404；409 已参与且禁止重复；410 |
| `POST /api/public/surveys/{public_id}/preview` | 已登录的填写者 | `answers`；可选 `changedQuestionId` | 200，规范答案与后续路径 | 404；410；422 答案/路径非法 |
| `POST /api/public/surveys/{public_id}/submit` | 已登录且填写凭据匹配 | `submissionKey`、`submissionToken`、`answers` | 201，`{"submitted":true}`；合法重试也为 201 | 401 填写凭据失效；403 绑定不符；404；409 重复/标识复用/并发冲突；410；422 |

框架另提供无需登录的 `GET /docs`（Swagger UI）、`GET /redoc`（ReDoc）和 `GET /openapi.json`。已包含前端构建文件时，`/` 还提供问序页面和相应 SPA 页面路由；页面路由不是新增业务 API。

本阶段不提供单独“添加一道题”的接口；题目通过问卷的 `questions` 数组整体写入。也不提供删除账号/问卷/答卷、撤回答卷、修改已提交答案、重新开放问卷、刷新登录令牌或恢复未提交草稿的接口。

## 3．注册与登录

注册和登录请求均为 `{"username":"demo_student","password":"example-password-2026"}`。

- 注册：用户名输入及 NFKC 标准化后为 3～32 字符，无内部空白或控制字符，小写键用于判重；密码 8～128 字符，保持原样。201 返回 `{_id,username,createdAt}`，与 `/me` 的公开用户结构相同，不含密码哈希。
- 登录：模型允许用户名 1～128、密码 1～128 字符，业务层统一规范化用户名。200 返回 `{"access_token":"<JWT>","token_type":"bearer"}`，用户名或密码错误统一 401。

注册接口不直接返回登录令牌，需继续登录；前端会自动执行此步骤。退出时客户端删除凭据，服务端不提供刷新令牌或撤销列表。

## 4．问卷创建、查询与修改

### 4.1 创建字段与完整响应

`POST /api/surveys` 的请求字段如下：

| 字段 | 必填 | 含义与默认值 |
|---|---|---|
| `title` | 是 | 去首尾空白后 1～200 字符 |
| `description` | 否 | 问卷说明，最多 5000 字符，默认 `""` |
| `settings` | 是 | 至少明确提供 `anonymous`；`allowRepeat` 默认 `false` |
| `deadlineAt` | 否 | 未来的带时区日期；默认 `null`，表示不限截止时间 |
| `questions` | 否 | 最多 100 道完整题目；默认 `[]`，草稿允许为空 |

成功返回完整问卷：`_id/ownerId/publicId/title/description/settings/deadlineAt/status/questions/schemaVersion/revision/createdAt/updatedAt/publishedAt/closedAt/closeReason`。初始为 `draft`、`revision=1`、`schemaVersion=1`，发布/关闭时间及原因为空。GET 详情、PATCH、发布与关闭返回同一结构；完整创建请求见第 5 节。

### 4.2 查询问卷

`GET /api/surveys?page=1&pageSize=20&status=published&search=课程`：

- `status` 可省略或为三种合法状态；已到期问卷按 `closed` 筛选。
- `search` 最多 200 字符，去首尾空白后按标题字面包含搜索，不区分大小写。
- 只返回本人问卷，列表元素省略 `questions`，增加 `questionCount/responseCount`。

`GET /api/surveys/{survey_id}` 返回包含完整题目的详情，三种状态均可读取。

### 4.3 修改、发布与关闭

PATCH 例如 `{"revision":1,"title":"修改后的课程体验调查"}`。`revision` 为最新 JSON 正整数（1～9007199254740991），另须至少一个待改字段。

- 未发送字段保持原值；`questions` 数组和 `settings` 对象整体替换，省略的内部可选字段用默认值补齐，不继承旧值。传 `settings` 必须含 `anonymous`，想保留重复设置时也应明确传 `allowRepeat`。
- `deadlineAt:null` 取消截止，省略表示不改；其他可改字段不能设为 `null`。
- 草稿可改完整定义；已发布只可改标题、说明和未来截止设置，或取消截止。题目及 `settings` 即使原样传回也返回锁定错误；关闭后只读。
- 发布与关闭请求仅为 `{"revision":最新整数}`。发布需草稿、至少一道合法题目且未到截止；关闭需有效已发布状态，成功写 `closeReason:"manual"`。

成功更新递增 `revision`；发布、关闭、到期补齐和成功新提交也会递增，合法同次重试不递增。冲突先重读并处理本地修改。状态只沿 `draft → published → closed`，不重新开放；版本及截止算法见 [关键逻辑说明](04_关键逻辑说明.md)。

## 5．四题型、规则与完整创建示例

### 5.1 题目与校验结构

通用字段为 `id`、`type`、`title`、`description`、`required`、`validation`、`jumpRules`。`id/type/title` 必填；说明默认空，`required` 默认 `true`，校验与跳转采用相应题型默认值。题目 ID、选项 ID 和规则 ID 匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。

| 题型 | 专有配置 | 答案 `value` | 业务约束 |
|---|---|---|---|
| `single` | `options` 为 2～50 个 `{id,label}`；`validation:{}` | 一个有效选项 ID 字符串 | 只能选择一项 |
| `multiple` | 同样需要 `options`；校验可含 `minSelections/maxSelections` | 不重复的选项 ID 数组 | 数量限制为 0～50 且不能超过选项数；必答至少允许选一项 |
| `text` | 校验可含 `minLength/maxLength`；不允许 `options` | 文本字符串 | 限制 0～10000，必答必须允许非空；`jumpRules` 只能为空数组 |
| `number` | 校验可含 `min/max`；`integerOnly` 默认 `false`；不允许 `options` | 有限 JSON 数字 | 数值范围含边界；整数模式须在 ±(2^53−1) 内且范围内存在可填整数 |

配置的上下限必须有序。未设置的限制字段应省略，不能设成 `null`。多选恰好选择 N 项使用 `minSelections=maxSelections=N`。题目 ID 在问卷内唯一，选项与规则 ID 在所属题目内唯一，同题选项文字也不能重复。

JSON 整数字面量超出安全整数范围会被拒绝；数字不能使用字符串、布尔值、NaN 或无穷大。前端使用整数模式时同样应检查安全整数范围。

### 5.2 跳转条件

| 来源题型 | 条件示例 | 含义 |
|---|---|---|
| 单选 | `{"operator":"eq","value":"no"}` | 选择指定选项 |
| 多选 | `{"operator":"containsAny","values":["lab","discussion"]}` | 包含指定任意选项 |
| 多选 | `{"operator":"containsAll","values":["lab","discussion"]}` | 包含指定全部选项，可另选其他项 |
| 多选 | `{"operator":"setEquals","values":["lab","discussion"]}` | 与指定集合完全相同，与选择先后无关 |
| 数字 | `{"operator":"gte","value":18}` | 大于或等于；另支持 `gt/lt/lte/eq` |
| 数字 | `{"operator":"between","min":18,"max":30}` | 闭区间，含 18 和 30 |

规则结构为 `{id,condition,target}`。目标只能是同问卷后面的一题，例如 `{"kind":"question","questionId":"q3"}`，或 `{"kind":"end"}`。不允许指向当前题、前面的题或其他问卷。文本题不支持条件跳转。

按规则数组顺序判断，第一条命中就执行；未命中或选填留空进入下一题。到最后一题或目标为 `end` 时进入提交确认，不自动保存答卷。规则是结构化数据，不执行客户端提供的任意代码。

### 5.3 可提交的四题型创建请求

以下 JSON 是 `POST /api/surveys` 的演示请求体，实际调用仍须携带登录令牌。题目和选项 ID 随该创建请求定义；第 1 题选择“否”会跳过第 2 题：

```json
{
  "title": "课程体验调查",
  "description": "请根据实际学习情况填写，整份问卷统一匿名。",
  "settings": { "anonymous": true, "allowRepeat": false },
  "deadlineAt": null,
  "questions": [
    {
      "id": "q1",
      "type": "single",
      "title": "是否参加过课程？",
      "description": "",
      "required": true,
      "validation": {},
      "options": [
        { "id": "yes", "label": "是" },
        { "id": "no", "label": "否" }
      ],
      "jumpRules": [
        {
          "id": "skip_detail",
          "condition": { "operator": "eq", "value": "no" },
          "target": { "kind": "question", "questionId": "q3" }
        }
      ]
    },
    {
      "id": "q2",
      "type": "multiple",
      "title": "喜欢哪些环节？",
      "description": "最多选两项",
      "required": true,
      "validation": { "minSelections": 1, "maxSelections": 2 },
      "options": [
        { "id": "theory", "label": "理论" },
        { "id": "lab", "label": "实验" },
        { "id": "discussion", "label": "讨论" }
      ],
      "jumpRules": []
    },
    {
      "id": "q3",
      "type": "number",
      "title": "每周学习时长",
      "description": "单位为小时",
      "required": true,
      "validation": { "min": 0, "max": 168, "integerOnly": false },
      "jumpRules": []
    },
    {
      "id": "q4",
      "type": "text",
      "title": "其他建议",
      "description": "",
      "required": false,
      "validation": { "maxLength": 1000 },
      "jumpRules": []
    }
  ]
}
```

系统容量：每问卷最多 100 题，每选择题最多 50 项，每题最多 20 条跳转；题干和题目说明各最多 2000 字符，选项文字最多 500 字符，文本答案最多 10000 字符。问卷标题、说明限制见第 4 节。字符数按 Unicode 码点计数。

## 6．获取问卷、开始与预览

### 6.1 获取问卷与开始填写

GET 返回 `_id/publicId/title/description/status/settings/deadlineAt/questions/canSubmit`；题目包含选项和校验，省略 `jumpRules`，不返回创建者账号。已参与且禁止重复时 GET 仍可为 200，但 `canSubmit=false`；草稿/不存在为 404，关闭/截止为 410。

`POST .../start` 无需请求体，200 返回：

```json
{"submissionKey":"<本次64位小写十六进制key>","submissionToken":"<本次签名凭据>"}
```

开始接口检查开放与参与资格。每次主动新填取新 key，同次提交重试保留原值。开始和预览不保存答案，不创建服务端填写草稿。

### 6.2 预览并计算路径

```http
POST /api/public/surveys/{public_id}/preview
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "answers": [ { "questionId": "q1", "value": "no" } ]
}
```

对第 5 节问卷，200 响应为：

```json
{
  "answers": [ { "questionId": "q1", "value": "no" } ],
  "nextQuestionId": "q3",
  "complete": false,
  "clearedQuestionIds": []
}
```

`answers` 每项只接受 `questionId` 和 `value`。预览要求已提供答案构成实际路径的连续前缀；校验通过后返回规范化答案和下一题。显式提交选填题 `value:null` 可以前进；没有提供该题答案时，预览停在该题。路径已全部完成时，`nextQuestionId=null`、`complete=true`，仅表示可以显示提交确认页。

真实修改前题时，发送 `changedQuestionId` 及新答案，例如：

```json
{
  "changedQuestionId": "q1",
  "answers": [
    {"questionId":"q1","value":"no"},
    {"questionId":"q2","value":["lab"]},
    {"questionId":"q3","value":6.5}
  ]
}
```

该例只保留 `q1`，返回 `nextQuestionId:"q3"` 和 `clearedQuestionIds:["q2","q3"]`。修改题之后的已提供答案全清除，即使新路径仍经过也需重填。仅查看或规范化后相同不发送更改信号；后端无历史草稿，由客户端判断实际修改。最终提交不能夹带路径外旧答案。

## 7．提交、重试与截止规则

### 7.1 完整提交

```http
POST /api/public/surveys/{public_id}/submit
Authorization: Bearer <access_token>
Content-Type: application/json
```

请求头中的 `<access_token>` 来自登录接口；下面请求体中的 `submissionToken` 来自 `start`。两者必须对应同一登录账号，且不能互换位置。

```json
{
  "submissionKey": "<start返回的submissionKey>",
  "submissionToken": "<start返回的submissionToken>",
  "answers": [
    { "questionId": "q1", "value": "no" },
    { "questionId": "q3", "value": 6.5 },
    { "questionId": "q4", "value": null }
  ]
}
```

新提交和同次合法重试均返回 201：

```json
{ "submitted": true }
```

“完整”是满足实际路径，不要求填写被跳过的题。上例的 `q2` 被跳过，不能夹带其答案，即使 `value:null` 也不允许；也不能提交不存在或重复的题目 ID。服务端重新验证路径、题型、必答、选项、范围及长度，再构造最终答卷。

实际经过的选填题留空统一保存 `null`；空白文本、空数组会按统一空值规则处理。选填题非空时仍须满足全部限制。最终提交省略实际经过的选填题时，后端会补齐 `null`，但必答题省略返回 422。

匿名答卷不保存 `respondentId`；实名答卷由服务器写入当前用户 ID，客户端无此请求字段。禁止重复填写时，参与记录和答卷在同一事务内写入，失败整体回滚，不占用填写资格。

### 7.2 重试、重复与截止

首次发送后固定本次 key、填写凭据和答案；超时、断网或 503 结果未知时重试原提交，不新开填写。规范化后答案一致则确认成功，不新增答卷；同 key 换成另一份合法答案返回 `409 SUBMISSION_KEY_REUSED`，非法载荷先返回 422。

填写凭据签名/用途/有效期错误为 `401 INVALID_SUBMISSION_TOKEN`，绑定账号、问卷或 key 不符为 403；缺失、空串或类型格式错误为 422。合法同次成功重试可在关闭后确认，但两类凭据仍须有效。

禁止重复时新 key 不能绕过账号限制；允许重复时主动再填取新 key。只有成功提交才持久化，不支持修改或撤回。

新提交要求服务器请求到达时间 `receivedAt < deadlineAt`，等于也拒绝；截止前在途请求可完成。手动关闭与提交按同一问卷有效条件写入的先后协调。事务、截止和匿名边界见 [关键逻辑说明](04_关键逻辑说明.md)。

## 8．答卷与结果统计

### 8.1 答卷列表

`GET /api/surveys/{survey_id}/responses?page=1&pageSize=20` 返回标准分页对象。每份答卷包含 `_id/surveyId/answers/submittedAt/schemaVersion`；实名另含 `respondentId/respondentName`，匿名无身份属性。内部 `submissionKey` 不返回，用户名只在查询时补充。

### 8.2 整卷与单题统计

`GET /api/surveys/{survey_id}/statistics` 返回 `{surveyId,totalResponses,questions}`，题目按定义顺序排列：

| 字段 | 含义 |
|---|---|
| `questionId/title/type/answeredCount` | 各题共有；有效非空回答数 |
| `options:[{optionId,label,count}]` | 选择题选项计数 |
| `average` | 数字题均值，无有效值为 `null` |

增加 `questionId` 只返回该题；文本/数字单题同时返回分页填写内容。例如 `?questionId=q4&page=1&pageSize=20`：

```json
{
  "surveyId":"<survey_id>","totalResponses":2,
  "questions":[{
    "questionId":"q4","title":"其他建议","type":"text","answeredCount":1,
    "values":["建议增加案例"],"valueTotal":1,"page":1,"pageSize":20
  }]
}
```

只统计成功答卷；留空与被跳过不计有效数。多选选项总次数可超过回答数，接口不返回百分比。允许重复时按份数统计。分页仅作用于 `values`，不影响总数、均值或选项计数；整卷统计不返回所有填写内容。

## 9．错误结构与处理

已注册业务异常采用以下结构，`details` 为可选信息：

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "问卷已被修改，请刷新后重试"
  }
}
```

业务答案错误可能附带 `details:{"questionId":"q3"}` 或 `questionIds`；请求模型错误使用 `INVALID_REQUEST`，`details` 数组只包含字段位置 `loc`、错误类型 `type` 和说明 `message`，不回显密码、令牌或答案输入。

| HTTP 状态 | 业务码 | 含义与处理 |
|---|---|---|
| 401 | `AUTH_REQUIRED` | 缺少可提取的 Bearer 凭据，如未携带、为空或使用非 Bearer 认证头 |
| 401 | `INVALID_CREDENTIALS` | 用户名或密码错误 |
| 401 | `INVALID_TOKEN` | 已提供 Bearer，但 JWT 签名、有效期、用途等校验失败，或账号不存在；将 `submissionToken` 放入认证头也属于此类 |
| 401 | `INVALID_SUBMISSION_TOKEN` | 请求结构合法，但请求体中的填写 JWT 校验失败，如篡改、过期或用途错误；不等于当前登录账号必然失效 |
| 403 | `SUBMISSION_TOKEN_MISMATCH` | 填写凭据绑定的账号、问卷或 key 不匹配 |
| 404 | `NOT_FOUND` | 资源不存在、管理 ID 非法、不属于本人、分享草稿或指定题目不存在 |
| 409 | `USERNAME_TAKEN` | 用户名标准化后重复 |
| 409 | `REVISION_CONFLICT` | 修订号或条件更新冲突，重新读取再处理 |
| 409 | `SURVEY_LOCKED` | 尝试修改已发布的锁定字段 |
| 409 | `SURVEY_CLOSED` | 尝试修改已关闭问卷的配置 |
| 409 | `INVALID_STATE` | 对非草稿发布，或对非已发布状态手动关闭 |
| 409 | `ALREADY_SUBMITTED` | 禁止重复填写的账号已经参与 |
| 409 | `SUBMISSION_KEY_REUSED` | 凭据及答案校验通过后，同一提交 key 用于不同规范答案 |
| 410 | `SURVEY_CLOSED` | 填写访问或新提交资格被关闭/截止拒绝；成功同次重试及截止前在途例外见第 7 节 |
| 413 | `REQUEST_TOO_LARGE`、`DOCUMENT_TOO_LARGE` | 请求体或最终文档过大 |
| 422 | `INVALID_REQUEST` | JSON 字段、类型、格式、参数范围等请求契约错误 |
| 422 | `INVALID_USERNAME`、`INVALID_DEADLINE`、`EMPTY_PATCH` | 对应业务参数错误 |
| 422 | `VALIDATION_ERROR` | 题目关联、跳转、答案、必答或实际路径校验失败 |
| 503 | `DATABASE_ERROR` | 数据库操作未完成，不能据此认定提交成功 |
| 503 | `ID_COLLISION` | 多次尝试仍不能分配唯一分享标识，可重试 |

401 业务响应带 `WWW-Authenticate: Bearer`。客户端应同时检查 HTTP status 和 `error.code`：`AUTH_REQUIRED` 检查是否携带登录令牌，`INVALID_CREDENTIALS` 检查登录输入，`INVALID_TOKEN` 处理登录认证，`INVALID_SUBMISSION_TOKEN` 处理本次填写凭据，不能把所有 401 都当作账号退出登录。字段缺失或类型错误应按 422 处理。网络未知或数据库故障后按第 7 节保留原提交重试；409 需按具体原因处理，不能一律覆盖或新建提交。

未定义路径、不支持的方法等框架路由错误可能使用 `{"detail":"Not Found"}`、`{"detail":"Method Not Allowed"}` 等框架默认结构；上述 `error` 契约针对已注册业务异常。

## 10．贯通调用示例与 OpenAPI

运行后访问 [Swagger UI](http://127.0.0.1:8000/docs)，或查看 [离线 OpenAPI](附件/openapi.json)。一次完整调用顺序：

1. 注册并登录，复制 `access_token`。Swagger 的 **Authorize** 中只填 token 值，工具会添加 Bearer 前缀。
2. 使用第 5 节 JSON 创建问卷，记录真实 `_id/publicId/revision`；携带最新 revision 发布。
3. 以填写账号保持登录授权，通过 publicId 获取问卷、调用 `start`；用第 6 节 `preview` 验证 `q1=no` 后跳至 `q3`。
4. 按第 7 节提交 `q1=no、q3=6.5、q4=null` 及本次凭据，收到 `submitted:true`；原请求重试仍为同次成功。
5. 使用创建者登录 token 读取答卷和统计；重读最新 revision 后关闭问卷。

Swagger 会实际写入数据库，示例资源 ID 和凭据需替换为真实值。自动 OpenAPI 尚未逐项声明响应模型和全部业务错误，部分成功响应 schema 为 `{}`；请求字段可查附件，具体响应和业务错误以本文为准。
