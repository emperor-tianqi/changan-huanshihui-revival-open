# 玩家教程（Windows + 安卓手机）

> 这份教程写给**没有参与过开发、不懂 Python / Git / 逆向工程**的玩家。
> 按顺序做，遇到问题先看第 12 节。
>
> 当前版本是 **v0.1.0-preview**，不是完整复原版。先读
> [FEATURE_STATUS.md](../FEATURE_STATUS.md) 确认你在意的玩法是否可用。

---

## 1. 先了解：现在游戏恢复到什么程度

- 能稳定做的：登录、建角、主城、妖灵背包/队伍、升级/进阶/升星、
  觉醒技洗练、天赋、法宝、神符、时装、邮件、月签、祈灵（测试池）、
  神器副本与通天塔的一部分。
- 能进但缺失：昆仑装备副本（战斗未通）、魔涧（进入卡住）、
  部分商城/排行榜/公会。
- 不能做：交易行（会断线）、装备副本战斗、iOS、多人联机。

完整列表见 [FEATURE_STATUS.md](../FEATURE_STATUS.md)。

---

## 2. 需要准备什么

**电脑（Windows 10/11）：**

- Python 3.11 或更高
- ADB（安卓调试工具）
- 能保持开机（手机玩的时候服务端要一直运行）

**手机（安卓）：**

- 已安装 **原版 0.1.20.1 客户端**（必须是你自己合法取得的）
- 打开“开发者选项 → USB 调试”
- 一根能传数据的 USB 线

> 本项目**不提供**客户端本体、APK、资源包。你需要自己准备。

---

## 3. 下载本项目

方式一（有 git）：

```powershell
git clone https://github.com/emperor-tianqi/changan-huanshihui-revival-open.git
cd changan-huanshihui-revival-open
```

方式二：在 GitHub 页面点 **Code → Download ZIP**，解压到一个**路径不含中文和空格**的目录，
例如 `D:\changan-open`。

---

## 4. 安装 Python

1. 打开 <https://www.python.org/downloads/windows/>
2. 下载 Python 3.12（或 3.11+）64 位安装包
3. 安装时**务必勾选** “Add python.exe to PATH”
4. 安装完成后，在 PowerShell 里验证：

```powershell
python --version
```

应显示 `Python 3.12.x`（或更高）。如果提示找不到命令，重新安装并勾选 PATH。

本项目**不需要** `pip install` 任何第三方库，服务端只用 Python 标准库。

---

## 5. 检查环境

在项目目录里运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
```

它会检查：

- Python 版本
- 两个端口（46002 / 38083）是否被占用
- ADB 是否存在
- 手机是否连接

---

## 6. 第一次启动服务端（创建独立存档）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_server.ps1 -FirstRun
```

- 这一步会在你的用户目录下创建存档目录：
  `%LOCALAPPDATA%\chang_an_revival_profiles\main\
  `
- 服务端窗口**不要关**。看到类似下面的输出就说明启动成功：

```
stub_started
http 127.0.0.1:38083
tcp  127.0.0.1:46002
```

> 如果提示端口被占用，见第 12 节。

---

## 7. 连接手机（USB / ADB）

1. 手机插上电脑，手机上弹出“允许 USB 调试吗？”→ 勾选“一律允许”→ 允许
2. 运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\connect_phone.ps1
```

脚本会：

- 检查 `adb devices` 是否能看到你的手机
- 执行两条端口反向映射：
  `adb reverse tcp:46002 tcp:46002`
  `adb reverse tcp:38083 tcp:38083`
- 把本项目提供的 **Lua 兼容垫片**推送到客户端目录：
  `/sdcard/Android/data/com.aligames.xs.lingxi/files/update/luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua`

> 如果你的客户端包名不是 `com.aligames.xs.lingxi`，把 `-PackageName` 传给脚本。

---

## 8. 客户端配置说明（重要）

服务端只监听本机（127.0.0.1），手机通过 ADB 反向端口访问，所以：

- **必须**保持 USB 连接（或无线 ADB），否则手机连不到服务端
- 客户端需要把服务器地址指向本机。本项目的做法是：

1. 提供**独立编写**的 Lua 垫片 `client_shim/luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua`
2. 客户端启动时会优先加载 `update/luaScript/` 下的同名文件
3. 垫片只负责本地 SDK 身份，不修改 APK、不联网

**限制：**

- 只对内置了 `update/luaScript` 加载路径的 0.1.20.1 客户端有效
- 不同渠道包、修改版客户端可能不生效
- 如果客户端仍然去连官方服务器，说明它的服务器入口不是本机；
  本项目**不提供修改后的客户端**（版权原因），这一点请理解。

安卓版本：Android 7.0 及以上（实测 Android 13/14 可用）。

---

## 9. 进入游戏

1. 保持服务端窗口开启
2. 手机打开游戏
3. 在服务器列表选择 **“1服-幻世单机”**
4. 进入后创建角色（首次）或直接进入（已有存档）

首次进入会看到创建角色界面；创建完成后即进入长安主城。

---

## 10. 第二次打开：继续原存档

1. 保持或重新启动服务端：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_server.ps1
```

（**不要**加 `-FirstRun`）

2. 重新运行 `scripts\connect_phone.ps1`（USB 重新插拔后必须重做端口映射）
3. 打开游戏 → 选同一个服务器 → 直接进入

存档在服务端，不会因为关游戏而丢失。

---

## 11. 退出、备份、恢复、迁移

**正常退出：**

- 服务端窗口按 `Ctrl+C`，或运行 `scripts\stop_server.ps1`
- 先关游戏，再关服务端，避免正在写入时中断

**存档位置：**

```
%LOCALAPPDATA%\chang_an_revival_profiles\main\
```

里面最重要的是 `revival_roles.sqlite3`（角色/卡牌/背包）。

**备份：**

1. 先停止服务端
2. 把整个 `main` 文件夹复制到别处（例如 `D:\changan-backup\`）

**恢复：**

1. 停止服务端
2. 用备份覆盖 `%LOCALAPPDATA%\chang_an_revival_profiles\main\
` 文件夹
3. 重新启动

**迁移到另一台电脑：**

把 `main` 文件夹复制到新电脑的相同位置即可。

> 任何时候都不要在服务端运行时直接编辑/删除 `revival_roles.sqlite3`。

---

## 12. 常见问题

### 12.1 提示端口被占用

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_server.ps1
```

或手动查：

```powershell
Get-NetTCPConnection -LocalPort 46002,38083
```

结束占用进程后重试。

### 12.2 adb 找不到 / 手机不在列表

- 确认已安装 platform-tools，并把 `adb.exe` 所在目录加入 PATH
- 手机重新插拔，确认“允许 USB 调试”
- `adb kill-server` 后重新 `adb devices`

### 12.3 游戏提示“网络状态不佳 / 登录失败”

- 确认服务端窗口还开着
- 确认 `adb reverse --list` 里有 46002 和 38083
- 手机息屏或切后台后，反向映射可能失效，重新运行 `connect_phone.ps1`
- 如果客户端连的是官方服务器而不是本机，见第 8 节限制

### 12.4 黑屏 / 一直转圈

- 多为客户端资源加载失败或该玩法未实现
- 先在主城确认基础功能正常，再逐个系统尝试
- 记录你点的按钮和出现的问题，便于反馈

### 12.5 存档会不会丢？

只要你不删除 `%LOCALAPPDATA%\chang_an_revival_profiles\main\
`，存档就在。
建议每次大版本更新前备份。

---

## 还需要帮助？

- 先看 [AI_GUIDE.md](AI_GUIDE.md)，把仓库交给 AI 帮你排查
- 或在 GitHub Issues 里按模板提交：系统、操作步骤、服务端日志、手机型号
- **不要**在公开 Issue 里贴账号、Token、完整存档
