# WithEcho 开放数据接口速查

服务地址 `https://api.withecho.cn`，认证 `Authorization: Bearer <access_token>`。
所有内容字段均为 markdown；时间字段均为 UTC（RFC 3339）。
数据不存在 / 不属于当前用户 / 尚未生成完毕，统一返回 `404 {"error":"not_found"}`。

## 分页通用规则

- `limit` 默认 20，最大 50
- 列表按时间新→旧返回；响应 `next_cursor` 非空时，作为下一次请求的 `cursor`
  参数向更早翻页，为空即到底
- `cursor` 是不透明串，原样回传，不要解析

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

## GET /open/daily/detail?event_id= — 事件详情（scope: daily:read）

列表字段之外多两项：

- `detail`：事件详情正文 markdown（可能为空串）
- `deliverables[]`：洞察卡片 `{tag, subtitle, title, summary, content}`，`content` 为 markdown

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

## GET /open/diaries/detail?diary_id= — 日记正文（scope: diary:read）

```json
{ "diary_id": "01J...", "date": "2026-08-04", "title": "…", "summary": "…", "content": "…markdown…" }
```

## GET /open/muses — 碎碎念列表（scope: muse:read）

参数：`limit`、`cursor`。列表直接带正文，无详情接口。

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

## 错误

| HTTP | error | 处理 |
|------|-------|------|
| 400 | `invalid_request` | 参数缺失或格式错误 |
| 401 | `invalid_token` | 刷新令牌后重试一次；仍失败重新授权 |
| 403 | `insufficient_scope` | 授权时未同意对应权限，重新 login |
| 404 | `not_found` | 数据不存在，如实告知用户 |
