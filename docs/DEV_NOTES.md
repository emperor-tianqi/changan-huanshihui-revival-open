# 开发者笔记

## 架构

```
手机客户端 ──USB/adb reverse──> 电脑
                                ├─ HTTP 38083  /enter.txt /server-list.txt /patch/*
                                └─ TCP  46002  自定义二进制协议（见下）
```

- `app.py`：命令行入口，选择角色存储模式，绑定 HTTP/TCP 两个端口。
- `codec.py` / `registry.py`：协议编解码与协议表。
- `tcp_stub.py`：主协议分发（登录、卡牌、副本、商城……）。
- `role_store.py`：SQLite 存档（角色、卡牌、钱包、队伍、元数据）。
- `compatibility_profile.py`：客户端兼容快照（登录包、场景包等）。
- `http_stub.py` / `http_contract.py`：引导入口、服务器列表、补丁清单。

## 角色存储

- 正常模式：`--role-mode play_profile`，一个名字对应一个目录，
  默认在 `%LOCALAPPDATA%\chang_an_revival_profiles\<name>\
`。
- 首次：`--profile-mode init`；之后：`--profile-mode resume`。
- 存档文件：`revival_roles.sqlite3`（外加 WAL/SHM）。

## 协议约定

- 每个 C2S 在 `registry.py` 注册格式；处理分支在 `tcp_stub.py`。
- 需要锁消息的请求必须在处理完回对应的 S2C，否则客户端锁死。
- 客户端字段中字符串到达时可能是 `bytes`，取值前先 `.decode()`。
- 更新卡牌等实体后，要回 S2C 30（`returnCardInfo`）刷新客户端。

## 开发模式（仅测试档案）

- `--dev-play-babel` / `--dev-play-artifact` / `--dev-play-resources` /
  `--dev-play-gacha`：会话级覆盖，只在对应开发档案生效。
- `--test-grant` / `--growth-grant` / `--achieve-level`：测试资源与等级，
  不影响正常档案。
- 这些不是原版机制，文档中必须标注为“兼容替代/测试”。

## 数据来源

- 协议 ID/格式来自对客户端 `*.max` 配置与 Lua 的静态分析（事实性数据）。
- 背包/卡牌/副本数值是**兼容实现**，不保证与原版完全一致。
- 仓库不包含客户端资源；`refine_pool.json` 与 `data/*.csv` 是运行所需的
  兼容性数据表（原创整理），不是美术/音频资源。

## 已知缺口

- 魔涧 topTower 场景切换
- 装备副本战斗
- 交易行协议族
- 公会/好友/竞技场
- iOS

## 真机验证

1. 启动服务端（测试档案）
2. `adb reverse tcp:46002 tcp:46002` 与 `tcp:38083`
3. 推送 `client_shim` 到 `update/luaScript/`（见玩家教程）
4. 进游戏，按系统逐个点
5. 记录服务端日志与手机截图
