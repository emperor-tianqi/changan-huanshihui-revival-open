# 傻瓜式：让 AI 帮你装好、跑起来

> 这份教程写给“完全不想碰命令行”的人。
> 你只需要：装一个能操作电脑的 AI，把仓库文件夹交给它，然后复制粘贴下面的提示词。

---

## 第 0 步：先选一个“能动手”的 AI

普通网页版 ChatGPT / DeepSeek **只能聊天，不能操作你的电脑**。
你需要一个“AI 编程助手/代理”，它能在你电脑上执行命令、读写文件。

任选其一（都有免费或试用额度）：

| 工具 | 说明 |
| --- | --- |
| **Claude Code** | Anthropic 的命令行 AI，能直接执行命令 |
| **Codex CLI** | OpenAI 的命令行 AI |
| **DeepSeek Harness** | 本项目作者用的执行器，支持 DeepSeek 模型（安装与充值见文末附录） |
| **Cursor / Windsurf** | 带 AI 的编辑器，能改文件、跑终端 |
| **Qoder / Trae** | 国内可用的 AI IDE |

> 选哪个都行，关键是：**它要能执行命令**（不只是聊天）。

---

## 第 1 步：把仓库放到电脑上

1. 打开 https://github.com/emperor-tianqi/changan-huanshihui-revival-open
2. 点 **Code → Download ZIP**
3. 解压到 **D:/changan-open**（路径不要有中文和空格）

---

## 第 2 步：让 AI 打开这个文件夹

- Claude Code / Codex CLI：在终端里 cd 到 D:/changan-open，然后启动
- Cursor / Windsurf / Trae：File → Open Folder → 选 D:/changan-open

---

## 第 3 步：复制这段“总提示词”给 AI

    你是我的 Windows 安装助手。这个文件夹是《长安幻世绘》复原项目（非官方）。
    请严格按以下顺序帮我，每一步都先解释你要做什么，再执行：

    1. 阅读 README.md、docs/PLAYER_GUIDE.md、docs/GLOSSARY.md，理解项目。
    2. 检查环境：Python 版本（需要 3.11+）、ADB 是否可用、46002/38083 端口是否空闲。
       缺什么就告诉我怎么装，不要自己乱装来源不明的软件。
    3. 如果 Python 或 ADB 缺失，给我官网下载链接和安装要点。
    4. 环境齐了以后，运行 scripts/start_server.ps1 -FirstRun 启动服务端（第一次）。
    5. 让我插好手机、打开 USB 调试，然后运行 scripts/connect_phone.ps1。
    6. 告诉我应该在手机上和电脑上分别看到什么。
    7. 如果我贴报错，先判断是环境问题、服务端问题还是手机问题，再做最小修复。
    8. 绝对不要删除 %LOCALAPPDATA%/chang_an_revival_profiles 下的任何东西。
    9. 每完成一步，用一句话总结“现在到哪了、下一步做什么”。

---

## 第 4 步：按 AI 的指示做

AI 会让你做这些事，照着做即可：

1. 安装 Python（勾选 Add to PATH）
2. 安装 ADB（platform-tools 解压，把目录加进 PATH）
3. 插手机、允许 USB 调试
4. 运行两条脚本（AI 会替你运行，或告诉你双击哪个 bat）

---

## 第 5 步：进游戏

- 电脑上服务端窗口保持开着
- 手机上打开游戏 → 选 **“1服-幻世单机”** → 创建角色

---

## AI 卡住时，这样问

把报错原文（或截图里的文字）贴给 AI，并加一句：

    这是完整的报错。请先定位第一个真正失败的点，
    不要跳过验证、不要重装整个项目、不要删存档。

如果 AI 开始“猜”，提醒它：

    请先读 scripts/ 和 revival_stub/app.py 的实际参数，不要凭想象写命令。

---

## 常见坑（AI 也会踩）

| 现象 | 原因 | 让 AI 检查 |
| --- | --- | --- |
| python 不是内部命令 | 没勾 PATH | 重装勾选 Add to PATH |
| adb 不是内部命令 | platform-tools 没进 PATH | 用绝对路径或加 PATH |
| 端口被占用 | 上次服务端没关 | scripts/stop_server.ps1 |
| 手机列表没有设备 | 没授权 USB 调试 | 手机上点“允许” |
| 游戏连不上 | 没做 adb reverse | 重跑 connect_phone.ps1 |
| 服务端报 profile 已存在 | 第二次还用了 -FirstRun | 去掉 -FirstRun |

---

## 附录：DeepSeek Harness 安装、充值与激活（作者同款）

### 1. 安装

先装 Node.js（LTS）：https://nodejs.org/zh-cn

然后打开 PowerShell：

    npm install -g @deepseek-ai/dsh
    dsh --version

### 2. 启动 Web 界面

    dsh web

浏览器打开终端里显示的地址（默认 http://127.0.0.1:3080）。

### 3. 注册 DeepSeek 账号

1. 打开 https://platform.deepseek.com
2. 注册 / 登录（手机号或邮箱）
3. 左侧「充值」→ 充一点（先 10 元试水，按 token 计费，用多少扣多少）
4. 左侧「API Keys」→「创建 API Key」→ 复制保存
   - 只显示一次，关了就只能重建
   - 不要发给别人、不要贴 Issue、不要提交进 git

### 4. 用 Key 激活

1. 在 DSH Web 页面点 设置（Settings）→ 模型（Models）
2. 找到 DeepSeek 一行，点开
3. 「API 密钥」粘贴刚才的 Key
4. 端点保持默认：https://api.deepseek.com/anthropic
5. 点「应用」

### 5. 开始用

1. DSH 里新建一个会话
2. 工作目录选到项目文件夹（例如 D:/changan-open）
3. 把上面第 3 步的“总提示词”粘进去发送

### 常见问题

| 问题 | 处理 |
| --- | --- |
| npm 不是内部命令 | 没装 Node.js，或安装时没勾选加入 PATH |
| dsh 不是内部命令 | 全局安装失败，重跑 npm install -g @deepseek-ai/dsh |
| 页面打不开 | 看终端里实际端口，可能不是 3080 |
| 提示余额不足 | 去 platform.deepseek.com 充值 |
| 密钥无效 | 重新创建 Key，注意不要带空格 |

> 费用提示：AI 帮你装环境、排错会消耗 token；先充少量，够用再加。

## 安全提醒

- 不要把账号密码、Token、完整存档发给 AI
- 不要让 AI 从不明网站下载“客户端”或“补丁”
- 改之前先备份 %LOCALAPPDATA%/chang_an_revival_profiles/main
- AI 改完代码不代表一定成功，以手机实际画面为准
