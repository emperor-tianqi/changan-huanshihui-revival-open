# 长安幻世绘 复原服务端（公开预览版）

> 非官方粉丝项目，和原游戏公司没有关系。
> 本仓库**不包含**原版客户端、APK、美术、音乐或配置资源。

这是一个跑在 **Windows 电脑**上的“私人服务器”。
装好之后，你手里**合法取得的原版 0.1.20.1 安卓客户端**可以连到这台电脑上，
像单机一样玩，并且能存档。

当前版本：**v0.1.0-preview**（预览版，不是完整复原）。

---

## 一、你需要准备什么

请先对照这张表，缺一样都跑不起来：

| 你需要 | 是什么 | 去哪弄 |
| --- | --- | --- |
| 一台 Windows 电脑 | Win10/Win11，能一直开着 | 你自己的电脑 |
| Python 3.11 或更高 | 运行服务端的程序 | python.org 官网下载，安装时勾选 “Add python.exe to PATH” |
| 安卓手机 | 能装游戏、能开 USB 调试 | 你自己的手机 |
| 原版 0.1.20.1 客户端 | 已经装在手机上的原版游戏 | **自行通过合法途径取得**，本仓库不提供 |
| 数据线 | 能传数据的 USB 线（不是纯充电线） | 自备 |
| ADB | 电脑和手机通信的工具 | Google 的 platform-tools，解压后把目录加进 PATH |

**不需要**：Git、Python 第三方库、Docker、Linux、任何加速器。

> 如果你不会装 Python 或 ADB，直接看第三节“让 AI 帮你”。

---

## 二、懒人版：双击两个文件就行

解压后，项目根目录有两个 `.bat`：

1. **一键启动.bat** —— 第一次自动创建存档并启动服务端；以后双击就是继续上次进度。
2. **连接手机.bat** —— 插好手机后双击，自动做端口映射、推送垫片。

看到服务端窗口出现两个端口就说明成功；然后手机上开游戏，选“1服-幻世单机”。

> 连 bat 都不想点？看 [docs/AI_AGENT_GUIDE.md](docs/AI_AGENT_GUIDE.md)，
> 把整个文件夹交给 AI，让它全程替你操作。

### 可选：让 AI 全自动（推荐 DeepSeek Harness，作者同款）

> **这一步不是必须的。** 已经会接 agent（Claude Code、Codex CLI、Cursor、Trae 等）的人，
> 直接跳到 [docs/AI_AGENT_GUIDE.md](docs/AI_AGENT_GUIDE.md) 复制提示词即可，不用装 Harness。
> 下面这套流程，是给“想照着作者的环境来”的新手准备的。

1. **装 Node.js**（LTS 版）：https://nodejs.org/zh-cn
2. 打开 PowerShell，安装 Harness：

       npm install -g @deepseek-ai/dsh
       dsh --version

3. 启动 Web 界面：

       dsh web

   浏览器打开终端里提示的地址（默认 http://127.0.0.1:3080）

4. **注册 DeepSeek 账号并充值**：

   - 打开 https://platform.deepseek.com 注册 / 登录
   - 左侧「充值」充一点（先充 10 元试水即可，按 token 计费）
   - 左侧「API Keys」→ 创建 → **复制保存**（只显示一次，丢了只能重建）

5. **用 Key 激活**：

   - 在 DSH 页面打开 设置（Settings）→ 模型（Models）
   - 找到 DeepSeek 一行 → 粘贴 API Key → 应用
   - 端点保持默认 https://api.deepseek.com/anthropic

6. 回到项目文件夹，按 [docs/AI_AGENT_GUIDE.md](docs/AI_AGENT_GUIDE.md) 把总提示词发给它。

> API Key 是“电费”，Harness 是“司机”，缺一不可。
> Key 不要发给别人、不要贴到 Issue、不要提交进 git。

---

## 三、你要做的三步

### 第 1 步：下载本仓库

GitHub 页面点 **Code → Download ZIP**，解压到一个**路径里没有中文和空格**的目录，
例如 D:/changan-open。

（会用 git 的人也可以 git clone。）

### 第 2 步：启动服务端

在解压出来的目录里，右键 → “在终端中打开”（或 PowerShell），运行：

    powershell -ExecutionPolicy Bypass -File scripts/check_env.ps1
    powershell -ExecutionPolicy Bypass -File scripts/start_server.ps1 -FirstRun

第一条检查环境，第二条启动服务端。**这个窗口不要关。**

### 第 3 步：连接手机并进游戏

手机插上电脑，在手机上允许 USB 调试，然后运行：

    powershell -ExecutionPolicy Bypass -File scripts/connect_phone.ps1

打开游戏 → 服务器列表选 **“1服-幻世单机”** → 创建角色 → 进主城。

以后每次玩：先运行 start_server.ps1（**不要**加 -FirstRun），再运行 connect_phone.ps1。

---

## 四、让 AI 帮你（推荐给不懂电脑的人）

你不需要会写代码。把下面这段话复制给 DeepSeek、ChatGPT 或 Claude：

    这是我下载的《长安幻世绘》复原项目。
    请先阅读 README.md 和 docs/PLAYER_GUIDE.md，
    检查我的 Windows 环境（Python、ADB、端口），
    一步一步帮我启动服务端并连接安卓手机。
    优先使用 scripts/ 里现成的脚本，不要重写项目，也不要删我的存档。
    每步告诉我应该看到什么；出错时我贴报错给你。

AI 能帮你：装环境、看报错、改配置、判断是服务端问题还是手机问题。
AI 不能替你：提供原版客户端、绕过版权、保证改完一定能跑。

更多提示词（修 BUG、调测试等级、继续开发）见 **docs/AI_GUIDE.md**。

---

## 五、我们已经做了什么

### 能玩的

- 登录、创建角色、长安主城、存档与读档
- 妖灵背包、队伍编成、升级/进阶/升星
- 觉醒技洗练（含祝福保底、7-9 级掉级、10 级必成规则）
- 天赋（五系节点）、法宝、神符、时装
- 邮件、月签、祈灵（测试池，不是真实掉率）
- 神器副本与通天塔的一部分

### 能进但有缺失的

- 昆仑装备副本：面板能开，战斗流程没接完
- 魔涧：详情能开，点“进入”会停住
- 商城/排行榜/公会：入口在，交互不全

### 还不能用的

- 交易行（打开会断线）
- 装备副本战斗、魔涧进入
- iOS、多人联机

完整清单见 **FEATURE_STATUS.md**。

---

## 六、重要说明（看了少踩坑）

1. **服务端只在你电脑上**：手机通过 USB 连过来，所以玩的时候电脑要开着、线要插着。
2. **客户端要能指向本机**：本仓库提供独立编写的 Lua 垫片，不修改 APK；
   不同渠道包可能不兼容。详见 docs/PLAYER_GUIDE.md 第 8 节。
3. **存档在电脑上**：%LOCALAPPDATA%/chang_an_revival_profiles/main/，
   重装游戏不会丢，但删这个目录会丢，记得备份。
4. **数值是兼容实现**：和原版不完全一致，部分系统可能卡住或黑屏。
5. **不要**把账号、Token、完整存档贴到公开 Issue。

---

## 七、文档目录

| 文档 | 给谁看 |
| --- | --- |
| docs/PLAYER_GUIDE.md | 第一次装的玩家：12 节完整教程 + 故障排查 |
| docs/AI_AGENT_GUIDE.md | **懒人版**：装个 AI 让它全程替你操作 |
| docs/AI_GUIDE.md | 想让 AI 帮忙的人：四段可复制提示词 |
| docs/GLOSSARY.md | 看不懂术语的人：服务端/ADB/端口/垫片都是什么 |
| FEATURE_STATUS.md | 想知道哪些能玩、哪些不能 |
| docs/DEV_NOTES.md | 想继续开发的程序员 |
| CONTRIBUTING.md | 想贡献代码/报问题 |

---

## 八、许可与声明

- 本仓库原创代码：MIT（见 LICENSE）
- 原版游戏内容不属于本仓库（见 NOTICE）
- 仅供学习、研究与个人兼容性测试，请遵守当地法律和原游戏用户协议
