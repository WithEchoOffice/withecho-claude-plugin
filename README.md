# WithEcho Claude Code Plugin

让 Claude Code 经用户授权后读取其 WithEcho 账号的**日常、日记、碎碎念**数据
（OAuth 2.0 授权码 + PKCE，公开应用形态，无 client_secret）。

```
claude-plugin/
├── .claude-plugin/plugin.json     # plugin 清单
└── skills/withecho-data/
    ├── SKILL.md                   # 技能入口（Claude 按 description 自动触发）
    ├── scripts/auth.py            # OAuth 登录/刷新/登出（Python 3 标准库）
    ├── scripts/fetch.py           # 拉取 daily / diary / muse 数据
    └── references/api.md          # 开放接口字段速查
```

## 配置

`scripts/auth.py` 顶部的 `CLIENT_ID` 已内置 WithEcho 后台登记的**公开应用** client_id
（回调地址登记 `http://127.0.0.1/callback`，scope 上限
`profile daily:read diary:read muse:read`）。公开应用的 client_id 本身不是机密，
内置分发是安全的。用户侧可用环境变量 `WITHECHO_CLIENT_ID` 覆盖；
测试环境用 `WITHECHO_API_BASE` 指到非生产地址。

## 分发方式

**方式一：marketplace（推荐，可持续更新）**

把本目录发布到一个公开 Git 仓库（如 `withecho/claude-plugin`），仓库根放
`.claude-plugin/marketplace.json`：

```json
{
  "name": "withecho",
  "owner": { "name": "WithEcho" },
  "plugins": [
    { "name": "withecho", "source": "./", "description": "读取你的 WithEcho 日常、日记、碎碎念" }
  ]
}
```

用户安装：

```
/plugin marketplace add withecho/claude-plugin
/plugin install withecho
```

**方式二：本地目录（开发/内测）**

```bash
claude --plugin-dir /path/to/claude-plugin
```

**方式三：直接拷贝 skill（最轻，无更新机制）**

把 `skills/withecho-data/` 整个拷到 `~/.claude/skills/` 即可。

## 用户数据与安全

- 令牌仅保存在用户本机 `~/.withecho/credentials.json`（0600），刷新采用原子写入
- 脚本只读数据，不提供任何写接口
- 用户可随时在 WithEcho App「设置 → 授权管理」撤销授权，令牌即刻失效
