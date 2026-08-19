# WithEcho 开放数据接口速查

服务地址 `https://api.withecho.cn`，认证 `Authorization: Bearer <access_token>`。
所有内容字段均为 markdown。
数据不存在 / 不属于当前用户 / 尚未生成完毕，统一返回 `404 {"error":"not_found"}`。

时间口径分三类：时间戳字段（带 `Z` 后缀，RFC 3339）为 **UTC**；`date` 字段与
日期参数（digest 的 `date`、diaries 的 `from`/`to`）为**用户本地日**，勿用 UTC
时间戳换算日期；提醒的 `trigger_rule.datetime` 保留用户语境时区。

## 批量形态（一次拿齐，别逐条往返）

- 详情接口：`/open/daily/detail?event_ids=a,b`、`/open/diaries/detail?diary_ids=a,b`、`/open/tasks/detail?task_ids=a,b`
  （逗号分隔或重复传，最多 50 个）→ `{"events"|"diaries"|"tasks": [...]}` 与请求同序，
  缺失项为 `{"<id_key>": "…", "error": "not_found"}`；单 ID 参数（`event_id` 等）仍返回单个对象
- `/open/digest?from=&to=`（≤31 天）→ `{"digests": [单日对象, ...]}` 每天一条
- `/open/asr/export?filename=a&filename=b`（≤50）见下文

## 分页通用规则

- `limit` 默认 20，最大 50（search 例外：每段默认 10，最大 20）
- 列表按时间新→旧返回；响应 `next_cursor` 非空时，作为下一次请求的 `cursor`
  参数向更早翻页，为空即到底
- `cursor` 是不透明串，原样回传，不要解析

## GET /open/search?q= — 跨域检索（搜索面 = 已授权 scope）

参数：`q`（必填，1-100 字符）、`limit`（每段条数，默认 10，最大 20）

```json
{
  "daily":   [{ "event_id": "01J...", "tag": "工作", "title": "…", "summary": "…" }],
  "diaries": [{ "diary_id": "01J...", "date": "2026-08-04", "title": "…", "summary": "…" }],
  "tasks":   [{ "task_id": "01J...", "title": "…", "result_title": "…", "result_summary": "…", "completed_at": "…" }]
}
```

- 段为 `null` = 未授权对应 scope（daily→`daily:read`、diaries→`diary:read`、tasks→`task:read`）；
  `[]` = 已授权但无命中
- 只回 id + 标题 + 摘要；正文走 `/open/daily/detail`、`/open/diaries/detail`、`/open/tasks/detail`
- 碎碎念不在搜索面内（正文短，直接翻 `/open/muses`）

## GET /open/digest?date= | ?from=&to= — 按天聚合

参数：`date`（单日）或 `from`+`to`（≤31 天，响应 `{"digests": [...]}` 每天一条）。单日一次取齐当天事件 + 日记（含正文）+ 碎碎念：

```json
{
  "date": "2026-08-04",
  "events": [{ "event_id": "…", "title": "…", "summary": "…", "tags": ["…"], "date": "…", "start_time": "…", "end_time": "…" }],
  "diary": { "diary_id": "…", "date": "…", "title": "…", "summary": "…", "content": "…markdown…" },
  "muses": [{ "muse_id": "…", "event_id": "…", "content": "…markdown…", "created_at": "…" }]
}
```

- `events`/`muses` 为 `null` = 未授权；`[]` = 当日无数据。`diary` 为 `null` = 未授权或当日无日记
- 事件正文与洞察卡片走 `/open/daily/detail?event_id=`

## GET /open/daily — 日常事件列表（scope: daily:read）

参数：`limit`、`cursor`

```json
{
  "events": [{
    "event_id": "01J...",
    "title": "…", "summary": "…", "tags": ["工作"],
    "date": "2026-08-04",
    "start_time": "2026-08-04T09:30:00Z",
    "end_time": "2026-08-04T10:10:00Z"
  }],
  "next_cursor": ""
}
```

## GET /open/daily/detail?event_id= | ?event_ids=a,b — 事件详情（scope: daily:read）

`event_ids` 批量形态响应 `{"events": [...]}`。列表字段之外多两项：

- `detail`：事件详情正文 markdown（可能为空串）
- `deliverables[]`：洞察卡片 `{tag, subtitle, title, summary, content}`，`content` 为 markdown
- `transcript_file`：来源转写文件名 `segments/YYYY/MM/DD/<id>.txt`（令牌带 `asr:read` 时下发），
  传给 `/open/asr/export` 可拿原文；无来源时缺省

## GET /open/diaries — 日记列表（scope: diary:read）

两种互斥模式：

- `from` + `to`（均 `YYYY-MM-DD`，`from<=to`）：范围内一次取齐（最多 100 篇），不分页
- `limit` + `cursor`（cursor 为 `YYYY-MM-DD`）：游标分页

```json
{
  "diaries": [{ "diary_id": "01J...", "date": "2026-08-04", "title": "…", "summary": "…" }],
  "next_cursor": "2026-07-29"
}
```

列表不含正文；正文逐篇调详情接口。

## GET /open/diaries/detail?diary_id= | ?diary_ids=a,b — 日记正文（scope: diary:read）

`diary_ids` 批量形态响应 `{"diaries": [...]}`。

```json
{ "diary_id": "01J...", "date": "2026-08-04", "title": "…", "summary": "…", "content": "…markdown…" }
```

## GET /open/muses — 碎碎念列表（scope: muse:read）

碎碎念是 Echo 的内心独白（看到用户日常后有感而发），不是用户本人写的内容，
解读和转述时注意视角。参数：`limit`、`cursor`。列表直接带正文，无详情接口。

```json
{
  "muses": [{
    "muse_id": "01J...", "event_id": "01J...",
    "content": "…markdown…", "created_at": "2026-08-04T12:00:00Z"
  }],
  "next_cursor": ""
}
```

`event_id` 关联日常事件，可用 `/open/daily/detail` 查看来源事件（需 `daily:read`）。

## GET /open/tasks — 研究任务列表（scope: task:read）

参数：`limit`、`cursor`、`status`（可选：proposed/confirmed/running/completed/failed/cancelled）

```json
{
  "tasks": [{
    "task_id": "01J...", "event_id": "01J...",
    "title": "…", "description": "…", "status": "completed",
    "result_title": "…", "result_summary": "…",
    "created_at": "…", "completed_at": "…"
  }],
  "next_cursor": ""
}
```

`event_id` 非空 = 来自日常事件；为空 = 来自对话。

## GET /open/tasks/detail?task_id= | ?task_ids=a,b — 任务详情（scope: task:read）

列表字段之外多一项 `result_md`：Markdown 交付物全文（未完成为空）。`task_ids` 批量形态响应 `{"tasks": [...]}`。

## GET /open/reminders — 提醒列表（scope: reminder:read）

参数：`limit`、`cursor`、`status`（默认 `active`；可传 proposed/triggered/cancelled/expired 或 `all`）

```json
{
  "reminders": [{
    "reminder_id": "01J...", "event_id": "01J...",
    "title": "…", "description": "…",
    "type": "once",
    "trigger_rule": { "datetime": "2026-08-10T09:00:00+08:00" },
    "status": "active",
    "next_trigger_at": "2026-08-10T01:00:00Z",
    "created_at": "…"
  }],
  "next_cursor": ""
}
```

- `type=once`：`trigger_rule.datetime` 为 ISO 8601（保留用户语境时区）
- `type=recurring`：`trigger_rule.rrule` 为 iCal RRULE，另有 `last_triggered_at`
- 可直接生成 .ics 或写入日历工具

## GET /open/asr/files?date= | ?from=&to= — 转写文件按天列表（scope: asr:read，不计次）

参数：`date`（单日）或 `from`+`to`（闭区间，最多 31 天），均为用户本地日

```json
{
  "files": [{ "filename": "segments/2026/08/19/01K….txt", "date": "2026-08-19", "event_ids": ["01K…", "01K…"] }],
  "quota": { "limit": 1000, "used": 12, "remaining": 988, "period_end": "2026-09-05T03:00:00Z" }
}
```

- 从当天可见的日常事件反查来源文件，一个文件对应它切出的全部事件；没有事件的录音段不出现
- `quota` 为当前会员窗口的导出额度（免费会员 `limit=0`）

## GET /open/asr/export?filename=a&filename=b — 批量导出转写原文（scope: asr:read，每个成功文件计 1 次）

`filename` 可重复传或逗号分隔，单次最多 50 个。

```json
{
  "files": [
    { "filename": "segments/2026/08/19/01K….txt", "content": "对话时间段：…\n1.[张三,09:30:05,09:30:12]…\n" },
    { "filename": "segments/2026/08/19/01K….txt", "error": "quota_exceeded" },
    { "filename": "segments/2026/08/19/01K….txt", "error": "not_found" }
  ],
  "quota": { "limit": 1000, "used": 1000, "remaining": 0, "period_end": "…" }
}
```

- `files` 与请求同序，每项要么 `content` 要么 `error`；`not_found`（不存在 / 非本人）不扣次
- 余量不够时按顺序能给多少给多少，给不到的标 `quota_exceeded`；一个都给不了才整体 `429 quota_exceeded`
- 免费会员整体 `403 membership_required`
- 同一文件重复导出照计，脚本层已做本地缓存，勿绕过

## 错误

| HTTP | error | 处理 |
|------|-------|------|
| 400 | `invalid_request` | 参数缺失或格式错误 |
| 401 | `invalid_token` | 刷新令牌后重试一次；仍失败重新授权 |
| 403 | `insufficient_scope` | 授权时未同意对应权限，重新 login |
| 404 | `not_found` | 数据不存在，如实告知用户 |
| 403 | `membership_required` | ASR 导出仅付费会员可用，如实告知 |
| 429 | `quota_exceeded` | ASR 导出本月次数用尽，如实告知，不要重试 |
