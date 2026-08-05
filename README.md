# WithEcho Claude Code Plugin

让 Claude Code 经你授权后读取你 WithEcho 账号的**日常、日记、碎碎念**数据，
用于总结、检索、分析（OAuth 2.0 授权码 + PKCE，只读，无任何写接口）。

## 安装

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
- 「看看我最近的碎碎念，有什么情绪变化」
- 「我 7 月 27 号那天的日常记录了什么」

**首次使用**会自动弹出浏览器完成 WithEcho 授权（手机号 + 短信验证码）。
之后令牌自动刷新，30 天内活跃无需重新登录。

| 场景 | 操作 |
|------|------|
| 更新插件 | `/plugin marketplace update withecho` |
| 卸载插件 | `/plugin uninstall withecho` |
| 退出登录 / 解绑 | 对 Claude 说「退出 WithEcho 登录」 |
| 撤销授权 | WithEcho App「设置 → 授权管理」，撤销后令牌立即失效 |

## 目录结构

```
.claude-plugin/                # plugin 与 marketplace 清单
skills/withecho-data/
├── SKILL.md                   # 技能入口（Claude 按 description 自动触发）
├── scripts/auth.py            # OAuth 登录/刷新/登出（Python 3 标准库）
├── scripts/fetch.py           # 拉取 daily / diary / muse 数据
└── references/api.md          # 开放接口字段速查
```

## 配置

`scripts/auth.py` 已内置 WithEcho 公开应用的 client_id（公开应用凭证非机密，
内置分发是安全的）。可用环境变量覆盖：

- `WITHECHO_CLIENT_ID`：替换 client_id
- `WITHECHO_API_BASE`：指向非生产环境（默认 `https://api.withecho.cn`）

本地开发调试可不经安装直接加载：`claude --plugin-dir /path/to/withecho-claude-plugin`。

## 数据与安全

- 令牌仅保存在你本机 `~/.withecho/credentials.json`（0600 权限），刷新原子写入
- 所有接口只读；内容以 markdown 返回
- 服务端不向第三方（包括本插件）提供手机号等敏感信息，用户标识为按应用隔离的 openid
