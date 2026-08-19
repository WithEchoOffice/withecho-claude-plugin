---
name: withecho-data
description: 读取用户 WithEcho（Echo）账号的日常、日记、碎碎念、研究任务、提醒数据，以及导出日常背后的语音识别原文（ASR 转写）。当用户要求查看、总结、搜索或分析自己的 Echo 记录（日常/日记/碎碎念/任务成果/提醒），要把 Echo 提醒同步到日历，或要看某段日常/某天的原始对话转写时使用。首次使用需引导用户完成 WithEcho 浏览器授权。
---

# WithEcho 数据读取

通过 WithEcho 开放平台 OAuth 授权，只读用户的五类数据（内容均为 markdown）与语音识别原文：

- **日常（daily）**：语音记录分析出的活动事件，含标题/摘要/标签/起止时间，详情含正文与洞察卡片
- **日记（diary）**：按天生成的日记，一天一篇
- **碎碎念（muse）**：Echo 的内心独白短文——是 Echo 看到用户日常后自己的想法，不是用户写的笔记
- **研究任务（tasks）**：AI 提出、用户确认后执行的深度研究，交付物为 Markdown 全文
- **提醒（reminders）**：标准格式触发规则（ISO 8601 datetime / iCal RRULE），可直接转日历
- **语音识别原文（asr）**：日常事件的来源——设备录音的逐句转写（说话人 + 时间 + 文本），
  按文件逐个导出，**每个文件消耗会员月配额 1 次**（见下文「ASR 原文导出」）

## 认证

所有命令都在本 skill 的 `scripts/` 目录下运行（Python 3，无第三方依赖）。

1. 先检查登录态：`python scripts/auth.py status`
2. 未登录时运行：`python scripts/auth.py login` —— 会自动打开浏览器完成 WithEcho
   授权（手机号 + 短信验证码），完成后令牌保存在 `~/.withecho/credentials.json`
3. access_token 过期由脚本自动刷新，无需手动处理
4. 用户要求退出/解绑时：`python scripts/auth.py logout`（吊销令牌并删除本地凭证）

login 需要用户在浏览器里操作，运行后提示用户完成授权，命令会等待回调（最长 5 分钟）。

## 读取数据（均输出 JSON 到 stdout）

**按需检索优先**：回答开放式问题先用 `search` / `digest` 定位，不要盲目 `--all` 全量拉取。

```bash
# 定位与聚合（推荐入口）
python scripts/fetch.py search --q 关键词          # 跨域检索：洞察卡片/日记/任务
python scripts/fetch.py digest --date 2026-08-04   # 单日聚合：事件+日记正文+碎碎念

# 日常 / 日记 / 碎碎念
python scripts/fetch.py daily --limit 20            # 日常事件列表（新→旧）
python scripts/fetch.py daily-detail --event-id ID  # 事件详情：正文 + 洞察卡片
python scripts/fetch.py diary --from 2026-08-01 --to 2026-08-05   # 按日期范围
python scripts/fetch.py diary-detail --diary-id ID  # 日记正文
python scripts/fetch.py muse --limit 50             # 碎碎念（列表自带正文）

# 任务 / 提醒
python scripts/fetch.py tasks --status completed    # 研究任务列表
python scripts/fetch.py task-detail --task-id ID    # 任务交付物 Markdown 全文
python scripts/fetch.py reminders                   # 生效中的提醒（--status all 看全部）
```

- 列表接口分页：响应里 `next_cursor` 非空时，用 `--cursor <next_cursor>` 取更早数据；
  确要全量时用 `--all` 自动翻页
- `search` 只回 id + 标题 + 摘要；正文按 id 再调对应 detail 接口
- 提醒的 `trigger_rule`：once 为 ISO 8601 datetime（含时区），recurring 为 iCal
  RRULE，可直接生成 .ics 或写入日历工具
- 时间口径分三类，勿混用：
  - 时间戳字段（`start_time`/`end_time`/`created_at`/`completed_at` 等，带 `Z` 后缀）
    为 **UTC**，展示给用户时转为本地时间
  - `date` 字段与日期参数（`digest --date`、`diary --from/--to`、`asr-files --date/--from/--to`）为**用户本地日**：
    "今天/昨天"直接按用户本地日期计算传入，**不要**从 UTC 时间戳换算日期（会差一天）
  - `trigger_rule.datetime` 保留用户设定提醒时的时区，原样使用
- 接口字段详情见 `references/api.md`

## ASR 原文导出（谨慎，扣额度）

```bash
python scripts/fetch.py asr-files --date 2026-08-19            # 当天转写文件 + 对应 event_ids + 余量（不扣额度）
python scripts/fetch.py asr-files --from 2026-08-01 --to 2026-08-07   # 区间最多 31 天
python scripts/fetch.py asr-export --filename F1 F2 F3        # 批量导出（一次传多个文件名，别一个个调）
python scripts/fetch.py asr-export --date 2026-08-19           # 导出当天全部文件（已缓存的不再请求）
```

- 文件名从两处来：`daily-detail` 响应的 `transcript_file`（某条日常的来源原文），或 `asr-files`
  按天列表（`files[].event_ids` 是该文件切出的日常事件）
- **一次命令拿齐**：要多个文件就把文件名一次全传给 `--filename`（或直接 `--date`），脚本按 50 个
  一批向服务器批量导出，不要循环逐个调用
- **导出结果永久缓存在本地** `~/.withecho/asr/<openid>/<filename>`，`asr-export` 先查本地、
  没有才请求服务器；`asr-files` 的 `cached` 字段标出哪些已在本地。缓存命中不扣额度，
  所以同一文件反复分析没有成本，**不要绕过脚本直接调接口**
- 输出 `files[]` 与入参同序：每项 `source=local|server` + `content`，或 `error=not_found|quota_exceeded`
  （余量不够时按顺序能给多少给多少，给不到的标 `quota_exceeded`，已拿到的照常返回）
- 每导出一个未缓存的文件扣会员月配额 1 次（pro 1000 / max 3000 / ultra 18000，免费会员不开放），
  响应 `quota` 有 `limit/used/remaining/period_end`。批量导出前先看 `asr-files` 的余量，
  未缓存文件数接近余量时提醒用户再动手；用户没要求就不要整月扫
- 免费会员返回 `membership_required`，额度用尽返回 `quota_exceeded`——如实告知，不要重试
- 原文格式：首行 `对话时间段：<起> 至 <止>`（用户时区），之后每行 `序号.[说话人,起,止]文本`

## 错误处理

- `未登录` / `invalid_grant`：运行 `python scripts/auth.py login` 重新授权
  （用户可能在 WithEcho App 里撤销过授权，这是正常路径，不要反复重试）
- `insufficient_scope`：用户授权时未同意对应权限，引导重新 login
  （老用户升级插件后首次用任务/提醒/ASR 导出也会遇到——重新 login 补授权即可）
- `not_found`：数据不存在或尚未生成完毕，如实告知用户即可
- `membership_required` / `quota_exceeded`：ASR 导出不开放 / 本月次数用尽，如实告知，不要重试
- `search` / `digest` 响应里某段为 `null` 表示未授权对应 scope，`[]` 才是无数据
- 脚本非 0 退出时，错误信息在 stderr（JSON 格式）

## 隐私约定

- 这些是用户的私人生活数据（日记、日常与 Echo 的观察，尤其是 ASR 原文——是真实对话的逐句转写，
  含他人发言），仅用于完成用户当前的请求，不要主动外发
- 除非用户明确要求，不要把原文整篇写入项目文件或提交到 git
