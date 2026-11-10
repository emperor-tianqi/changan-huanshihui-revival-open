# 长安幻世绘 CN 0.1.20.1 复原服务端（公开预览版）

> **非官方粉丝项目。** 与《长安幻世绘》开发商/发行商无任何关系。
> 本仓库**不包含**原版客户端、APK、美术、音乐或配置资源。
> 你需要自行合法取得原版 0.1.20.1 安卓客户端。

这是一个用 Python 独立实现的兼容服务端，让**合法取得的原版 0.1.20.1 安卓客户端**
可以连接到你自己的电脑，进行单机式游玩与调试。

当前版本：**v0.1.0-preview**（预览版，不是完整复原版）。

## 现在能做什么

- 登录、创建角色、进入长安主城、保存与恢复角色存档
- 妖灵（卡牌）背包、队伍编成、升级/进阶/升星、技能与觉醒技洗练
- 部分副本与养成系统（详见 [FEATURE_STATUS.md](FEATURE_STATUS.md)）
- 天赋、法宝、神符等系统的界面与基础回路

**不能**期待整款游戏 100% 复原；很多系统仍是“能进入但功能缺失”或“仍在开发”。
请先看 [FEATURE_STATUS.md](FEATURE_STATUS.md) 再决定是否适合你。

## 三分钟快速开始

### 1. 准备

- Windows 10/11 电脑
- Python **3.11 或更高**（安装时勾选 “Add python.exe to PATH”）
- 安卓手机 + 数据线 + 已安装的 **原版 0.1.20.1 客户端**
- 电脑上的 [ADB](https://developer.android.com/tools/releases/platform-tools)（platform-tools）

### 2. 下载

```powershell
git clone https://github.com/emperor-tianqi/changan-huanshihui-revival-open.git
cd changan-huanshihui-revival-open
```

没有 git 也可以直接下载 ZIP 解压。

### 3. 检查环境

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1
```

### 4. 第一次启动服务端（创建独立存档）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_server.ps1 -FirstRun
```

看到 `stub_started` 与两个端口就绪即可。**不要关闭这个窗口。**

### 5. 连接手机

```powershell
powershell -ExecutionPolicy Bypass -File scripts\connect_phone.ps1
```

脚本会：

1. 检查手机是否被 ADB 识别；
2. 把手机的两个端口反向映射到电脑（46002 / 38083）；
3. 把本地 SDK 兼容垫片推送到客户端的 `update/luaScript` 目录。

### 6. 进入游戏

手机打开游戏 → 选择“1服-幻世单机” → 进入 → 创建角色。

## 第二次及以后

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_server.ps1
```

不带 `-FirstRun` 就是**继续原存档**。

## 文档

| 文档 | 适合谁 |
| --- | --- |
| [docs/PLAYER_GUIDE.md](docs/PLAYER_GUIDE.md) | 第一次接触本项目的玩家：安装、存档、故障排查 |
| [docs/AI_GUIDE.md](docs/AI_GUIDE.md) | 想让 AI 帮忙安装、修 BUG、调测试角色的人 |
| [FEATURE_STATUS.md](FEATURE_STATUS.md) | 想知道哪些玩法能玩、哪些不能 |
| [docs/DEV_NOTES.md](docs/DEV_NOTES.md) | 想继续开发的程序员 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献代码与报告问题 |

## 已知限制（务必先读）

1. **客户端需要能被“指到本地”**：服务端只监听本机，手机通过 ADB 反向端口访问。
   本仓库提供的是**独立编写的 Lua 兼容垫片**，不修改 APK；如果你使用的客户端版本
   或渠道不同，垫片可能不生效。详见玩家教程第 8 节。
2. **不支持 iOS**：本项目只针对安卓客户端。
3. **必须有一台 Windows 电脑常开**：手机本身不能独立运行服务端。
4. **预览版**：副本、战斗结算、商城等系统仍有缺失，可能出现卡住/黑屏。
5. 数据来自原版客户端配置的兼容实现，可能与原版数值有差异；文档中标注了哪些是
   “机制复原”、哪些是“兼容替代”。

## 许可

本仓库中原创代码使用 MIT 许可（见 [LICENSE](LICENSE)）。
原版游戏的一切内容（客户端、素材、配置、商标）不属于本仓库，见 [NOTICE](NOTICE)。

## 免责声明

本项目仅供学习、研究与个人兼容性测试。请在遵守当地法律与原游戏用户协议的前提下
使用；由此产生的任何后果由使用者自行承担。
