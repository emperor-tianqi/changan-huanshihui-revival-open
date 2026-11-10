# 客户端 Lua 兼容垫片

这里的两个文件是**本项目独立编写**的兼容脚本，不是原版客户端代码，也不包含
任何原版资源。它们通过客户端自带的 `update/luaScript` 加载路径生效。

## 文件

| 文件 | 作用 |
| --- | --- |
| `luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua` | 本地 SDK 身份：跳过第三方 SDK 登录，触发原版的“加载服务器列表”流程 |
| `luaScript/createRole/pkgCreateRoleMgr.lua` | 可选：建角流程的诊断日志（不改变任何行为） |

## 部署位置

```
/sdcard/Android/data/<包名>/files/update/luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua
```

`scripts/connect_phone.ps1` 会自动推送。手动推送：

```powershell
adb push client_shim/luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua /sdcard/Android/data/com.aligames.xs.lingxi/files/update/luaScript/sdk/ejoysdk/
```

## 限制

- 只对会从 `update/luaScript` 加载脚本的 0.1.20.1 客户端有效。
- 客户端仍需把服务器入口指向本机；本项目不提供修改后的客户端。
- 如果启动后仍去连官方服务器，说明该客户端不支持此加载路径。
