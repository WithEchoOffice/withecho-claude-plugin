---
name: withecho-data
description: 读取用户 WithEcho（Echo）账号的日常、日记、碎碎念数据。当用户要求查看、总结、搜索或分析自己的 Echo 日常记录、日记或碎碎念时使用。首次使用需引导用户完成 WithEcho 浏览器授权。
---

# WithEcho 数据读取

通过 WithEcho 开放平台 OAuth 授权，只读用户的三类数据（内容均为 markdown）：

- **日常（daily）**：语音记录分析出的活动事件，含标题/摘要/标签/起止时间，详情含正文与洞察卡片
- **日记（diary）**：按天生成的日记，一天一篇
- **碎碎念（muse）**：轻量随想短文

## 认证

所有命令都在本 skill 的 `scripts/` 目录下运行（Python 3，无第三方依赖）。

1. 先检查登录态：`python scripts/auth.py status`
2. 未登录时运行：`python scripts/auth.py login` —— 会自动打开浏览器完成 WithEcho
   授权（手机号 + 短信验证码），完成后令牌保存在 `~/.withecho/credentials.json`
3. access_token 过期由脚本自动刷新，无需手动处理
4. 用户要求退出/解绑时：`python scripts/auth.py logout`（吊销令牌并删除本地凭证）

login 需要用户在浏览器里操作，运行后提示用户完成授权，命令会等待回调（最长 5 分钟）。

## 读取数据（均输出 JSON 到 stdout）

```bash
python scripts/fetch.py daily --limit 20            # 日常事件列表（新→旧）
python scripts/fetch.py daily --all                 # 拉全部（自动翻页）
python scripts/fetch.py daily-detail --event-id ID  # 事件详情：正文 + 洞察卡片
python scripts/fetch.py diary --from 2026-08-01 --to 2026-08-05   # 按日期范围
python scripts/fetch.py diary --limit 20            # 游标分页（新→旧）
python scripts/fetch.py diary-detail --diary-id ID  # 日记正文
python scripts/fetch.py muse --limit 50             # 碎碎念（列表自带正文）
python scripts/fetch.py muse --all                  # 拉全部（自动翻页）
```

- 列表接口分页：响应里 `next_cursor` 非空时，用 `--cursor <next_cursor>` 取更早数据
- 日记列表不含正文；要读内容需对每篇再调 `diary-detail`
- 时间字段均为 UTC（RFC 3339），展示给用户时转为本地时间
- 接口字段详情见 `references/api.md`

## 错误处理

- `未登录` / `invalid_grant`：运行 `python scripts/auth.py login` 重新授权
  （用户可能在 WithEcho App 里撤销过授权，这是正常路径，不要反复重试）
- `insufficient_scope`：用户授权时未同意对应权限，引导重新 login
- `not_found`：数据不存在或尚未生成完毕，如实告知用户即可
- 脚本非 0 退出时，错误信息在 stderr（JSON 格式）

## 隐私约定

- 这些是用户的私人日记与随想，仅用于完成用户当前的请求，不要主动外发
- 除非用户明确要求，不要把原文整篇写入项目文件或提交到 git
