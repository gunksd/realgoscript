# RealGo 链上签到脚本

BSC 链上 RealGo 活动（Binance Wallet × RealGo）的自动签到脚本。

## 功能

- 读取 `players(address)` 检测钱包在活动合约上的注册与签到状态
- 未注册的钱包先调用 `register()`，再调用 `checkIn()`
- 按 UTC 自然日判重，当天已签到的钱包自动跳过
- 每个钱包签满目标次数（默认 7 次）后自动停止，不再发交易
- `estimate_gas` 预检作为护栏，合约拒绝的调用不会广播，避免浪费 gas
- 运行结果可通过企业微信 webhook 推送

## 合约

- 网络：BSC 主网（chainId 56）
- 合约地址：`0x4d0571B4e311DB2EF704E4F5212E42150b01494C`
- 方法：`register()` / `checkIn()`（均无参数，身份由 `msg.sender` 决定）

## 依赖

```
pip install web3 python-dotenv requests
```

## 配置

1. `keys.txt` — 每行一个私钥（`#` 开头的行忽略）。此文件不纳入版本控制。
2. `.env` — 环境变量：

```
BSC_RPC=<BSC RPC 节点地址>
SOCKS_PROXY=<可选，socks5 代理，如 socks5h://127.0.0.1:7890>
WECHAT_WEBHOOK=<可选，企业微信机器人 webhook 地址>
```

## 运行

```
python3 realgo_checkin.py
```

签到进度记录在 `realgo_counts.json`（不纳入版本控制）。

## 定时执行

用 cron 每天执行一次，例如每天 10:01：

```
1 10 * * * cd /path/to/repo && /usr/bin/python3 realgo_checkin.py >> realgo.log 2>&1
```

## 安全说明

- `keys.txt`、`.env`、`realgo_counts.json`、`*.log` 均已在 `.gitignore` 中排除，不会提交到仓库。
- 私钥仅在本地读取用于签名，不会外传。
