# 贡献指南

感谢你愿意帮忙。这个项目是**非官方粉丝兼容项目**，请在贡献前确认：

- 不提交原版客户端文件、素材、解包资源或逐字反编译代码；
- 不提交真实玩家存档、设备信息、Token；
- 不引入来源不明或不兼容许可的第三方代码。

## 开发环境

- Python 3.11+
- Windows 10/11（玩家主平台）
- 可选：ADB + 安卓手机做真机验证

```powershell
git clone https://github.com/emperor-tianqi/changan-huanshihui-revival-open.git
cd changan-huanshihui-revival-open
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
pip install pytest   # 可选，运行测试用
```

## 运行

```powershell
python -m revival_stub --role-mode play_profile --play-profile dev --profile-mode init --http-port 38083 --tcp-port 46002
```

## 测试

```powershell
python -m pytest tests -q
```

部分测试依赖真机或客户端资源，会跳过；以实际输出为准。

## 提交

1. 从最新 main 拉分支
2. 小步提交，说明“改了什么 / 为什么 / 怎么验证”
3. PR 描述里写清楚：
   - 涉及的系统
   - 真机验证步骤与结果（没有真机就注明“仅离线测试”）
4. 不要在一次 PR 里混入无关的格式化或重排

## 代码风格

- 保持现有模块划分；新增系统单独建模块
- 协议处理放在 `tcp_stub.py` 或对应子系统模块
- 存档写入统一走 `role_store.py`
- 注释说明“这是机制复原还是兼容替代”

## 报告问题

用 Issue 模板，附：

- 复现步骤
- 服务端日志（脱敏）
- 手机型号/安卓版本/客户端版本
- 是否使用开发模式
