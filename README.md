# WithEcho Claude Code Plugin

让 AI 编程助手经你授权后读取你 WithEcho 账号的**日常、日记、碎碎念、研究任务、
提醒**数据，以及导出日常背后的**语音识别原文**，用于总结、检索、分析、同步日历
（OAuth 2.0 授权码 + PKCE，只读，无任何写接口）。

技能遵循开放的 [Agent Skills 标准](https://agentskills.io)（SKILL.md）：
除 Claude Code 外，也可用于 Codex、Cursor、opencode、CodeBuddy、pi 等任何
支持该标准的 agent，见[在其他 Agent 中使用](#在其他-agent-中使用)。

## 在 Claude Code 中安装

在 Claude Code 里执行：

```
/plugin marketplace add https://github.com/WithEchoOffice/withecho-claude-plugin
/plugin install withecho
```

> 安装命令请使用上面的完整 HTTPS URL，不要用 `owner/repo` 简写——简写在部分机器上
> 会走 SSH 克隆，未配 GitHub 公钥就会报 `Permission denied (publickey)`。

前提：机器上有 `git` 和 `python3`（macOS 自带；脚本仅用 Python 标准库，无需装依赖）。

## 使用

装完直接对 Claude 说人话即可，skill 会自动触发：

- 「帮我总结一下上周的日记」
- 「看看 Echo 最近的碎碎念都在想什么」
- 「我 7 月 27 号那天的日常记录了什么」
- 「搜一下我关于装修的记录」
- 「把我 Echo 里的提醒同步到系统日历」
- 「我那个调研任务的结果导出成文件」
- 「把我 8 月 19 号的录音原文导出来」（付费会员功能，按文件计月配额；导出结果留在本地
  `~/.withecho/asr/`，同一文件之后不再重复扣）

**首次使用**会自动弹出浏览器完成 WithEcho 授权（手机号 + 短信验证码）。
之后令牌自动刷新，30 天内活跃无需重新登录。

读取结果默认缓存在本地 `~/.withecho/cache/`：列表/搜索/聚合 10 分钟内不重复请求服务器，
正文详情永久缓存；你说"数据没更新""再拉一次"时 Claude 会带 `--refresh` 穿透拉最新。
想手动清缓存：`python skills/withecho-data/scripts/fetch.py cache-clear`（ASR 原文缓存不受影响）。

| 场景 | 操作 |
|------|------|
| 更新插件 | `/plugin marketplace update withecho` |
| 卸载插件 | `/plugin uninstall withecho` |
| 退出登录 / 解绑 | 对 Claude 说「退出 WithEcho 登录」 |
| 撤销授权 | WithEcho App「设置 → 授权管理」，撤销后令牌立即失效 |

## 在其他 Agent 中使用

`skills/withecho-data/` 目录就是一个标准 skill，可不加修改地用于任何支持
[Agent Skills 标准](https://agentskills.io)的 agent。前提同上：有 `git` 和 `python3`。

**方式一（推荐）：通用 skills CLI 一键安装**（需要 Node.js）：

```
npx skills add https://github.com/WithEchoOffice/withecho-claude-plugin -g
```

它会自动检测本机装了哪些 agent（Codex、Cursor、opencode、CodeBuddy、iFlow CLI、
Qwen Code、Trae、Kimi Code CLI、pi、Cline、Roo Code 等 70+ 种）并提示选择。

**方式二：手动复制**，装进跨工具共享目录 `~/.agents/skills/`：

```
git clone https://github.com/WithEchoOffice/withecho-claude-plugin
cp -r withecho-claude-plugin/skills/withecho-data ~/.agents/skills/
```

Codex、Cursor、opencode、pi 都会读取 `~/.agents/skills/`，装一次多个 agent 同时生效。
如果目标 agent 不读该目录，拷到它自己的技能目录即可：

| Agent | 用户级技能目录 | 项目级技能目录 |
|-------|--------------|--------------|
| OpenAI Codex | `~/.agents/skills/` | `.agents/skills/` |
| Cursor | `~/.cursor/skills/`（也读 `~/.claude/skills/`） | `.cursor/skills/` |
| opencode | `~/.config/opencode/skills/`（也读 `~/.claude/skills/`） | `.opencode/skills/` |
| CodeBuddy | `~/.codebuddy/skills/` | `.codebuddy/skills/` |
| pi | `~/.pi/agent/skills/` | `.pi/skills/` |

装好后用法与 Claude Code 相同，直接说人话触发（Codex 里也可用 `$withecho-data`
显式调用）。令牌统一存在 `~/.withecho/credentials.json`，**所有 agent 共享登录态，
在任意一个里授权过即可**。

## 目录结构

```
.claude-plugin/                # plugin 与 marketplace 清单
skills/withecho-data/
├── SKILL.md                   # 技能入口（Claude 按 description 自动触发）
├── scripts/auth.py            # OAuth 登录/刷新/登出（Python 3 标准库）
├── scripts/fetch.py           # 拉取 daily/diary/muse/tasks/reminders + search/digest + asr（本地缓存优先，--refresh 穿透）
└── references/api.md          # 开放接口字段速查
```

## 配置

`scripts/auth.py` 已内置 WithEcho 公开应用的 client_id（公开应用凭证非机密，
内置分发是安全的）。可用环境变量覆盖：

- `WITHECHO_CLIENT_ID`：替换 client_id
- `WITHECHO_API_BASE`：指向非生产环境（默认 `https://api.withecho.cn`；非本机地址必须是 https）
- `WITHECHO_CACHE_TTL`：列表/搜索/聚合类查询的本地缓存有效期（秒，默认 600；`0` 为不过期）

本地开发调试可不经安装直接加载：`claude --plugin-dir /path/to/withecho-claude-plugin`。

## 数据与安全

- 令牌仅保存在你本机 `~/.withecho/credentials.json`，刷新原子写入；`~/.withecho/` 下的令牌、
  响应缓存、ASR 原文缓存整体 0700/0600，仅当前系统用户可读
- 缓存路径由服务端返回的文件名/ID 拼出，脚本对其做白名单校验，不会读写 `~/.withecho/` 之外的文件
- 所有接口只读；内容以 markdown 返回
- 服务端不向第三方（包括本插件）提供手机号等敏感信息，用户标识为按应用隔离的 openid
