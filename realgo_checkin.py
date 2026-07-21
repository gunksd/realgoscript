import os
import sys
import time
import json
import random
import datetime
import requests
from dotenv import load_dotenv
from web3 import Web3

# ============================================================
# RealGo (Binance Wallet x RealGo) 链上签到脚本
# 合约: 0x4d0571B4e311DB2EF704E4F5212E42150b01494C (BSC)
# 逻辑: players()检测注册 -> 未注册则 register() -> checkIn()
# ============================================================

CONTRACT_ADDRESS = "0x4d0571B4e311DB2EF704E4F5212E42150b01494C"
SEL_CHECKIN = "0x183ff085"   # checkIn()
SEL_REGISTER = "0x1aa3a008"  # register()
SEL_PLAYERS = "0xe2eb41ff"   # players(address) -> (registered, lastCheckInTs)
CHAIN_ID = 56
DEFAULT_RPC = "https://bsc-dataseed.binance.org/"
DELAY_RANGE = (2, 5)
GAS_MULTIPLIER = 1.2
TARGET_CHECKINS = 7     # 每个钱包签满 7 次即停止
COUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "realgo_counts.json")


def load_counts():
    """读取每个钱包已签到次数 {address: count}"""
    if os.path.exists(COUNT_FILE):
        try:
            with open(COUNT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_counts(counts):
    tmp = COUNT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(counts, f, indent=2)
    os.replace(tmp, COUNT_FILE)


def notify_wechat(content):
    webhook = os.getenv("WECHAT_WEBHOOK", "")
    if not webhook:
        print("[INFO] WECHAT_WEBHOOK 未配置,跳过通知")
        return
    try:
        requests.post(webhook, json={
            "msgtype": "text", "text": {"content": content}
        }, timeout=10)
    except Exception as e:
        print(f"[WARN] WeChat notify failed: {e}")


def load_private_keys():
    keys_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.txt")
    if not os.path.exists(keys_file):
        print("[ERROR] keys.txt not found")
        sys.exit(1)
    with open(keys_file, "r") as f:
        keys = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not keys:
        print("[ERROR] No private keys found in keys.txt")
        sys.exit(1)
    return keys


def read_player(w3, address):
    """读 players(address) -> (registered:bool, last_checkin_ts:int)"""
    data = SEL_PLAYERS + address[2:].lower().rjust(64, "0")
    raw = w3.eth.call({"to": Web3.to_checksum_address(CONTRACT_ADDRESS), "data": data})
    hexs = raw.hex()
    if hexs.startswith("0x"):
        hexs = hexs[2:]
    words = [hexs[i:i + 64] for i in range(0, len(hexs), 64)]
    registered = int(words[0], 16) if len(words) > 0 else 0
    last_ts = int(words[1], 16) if len(words) > 1 else 0
    return bool(registered), last_ts


def already_checked_today(last_ts):
    """按 UTC 自然日判断今天是否已签。合约本身按 UTC 日判重,
    estimate_gas 会拦截合约拒绝的情况,所以这里只做同一 UTC 日的快速跳过,
    只按 UTC 自然日快速跳过,其余交给合约判重。"""
    if last_ts <= 0:
        return False
    return (last_ts // 86400) == (int(time.time()) // 86400)


def send_tx(w3, private_key, selector, label):
    """发一笔无参数方法调用交易,返回 (tx_hash_hex, status)"""
    account = w3.eth.account.from_key(private_key)
    address = account.address
    nonce = w3.eth.get_transaction_count(address)
    tx = {
        "to": Web3.to_checksum_address(CONTRACT_ADDRESS),
        "data": selector,
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "gasPrice": w3.eth.gas_price,
    }
    # estimate_gas 作为护栏:合约拒绝(已签/条件不满足)会在此抛错
    gas_estimate = w3.eth.estimate_gas({"from": address, **tx})
    tx["gas"] = int(gas_estimate * GAS_MULTIPLIER)
    signed = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    return tx_hash.hex(), receipt["status"]


def process_wallet(w3, key, counts):
    """处理单个钱包: 检查次数 -> 检测注册 -> 注册 -> 签到。返回状态字符串。
    counts 会在 checkIn 成功时就地 +1。"""
    account = w3.eth.account.from_key(key)
    address = account.address
    done = counts.get(address, 0)

    # 0. 已签满目标次数 -> 直接跳过,不发交易
    if done >= TARGET_CHECKINS:
        return address, f"DONE_已达标({done}/{TARGET_CHECKINS})"

    # 1. 读链上注册/签到状态
    try:
        registered, last_ts = read_player(w3, address)
    except Exception as e:
        return address, f"READ_FAIL: {e}"

    # 2. 未注册 -> 先 register()
    if not registered:
        try:
            txh, st = send_tx(w3, key, SEL_REGISTER, "register")
            if st != 1:
                return address, f"REGISTER_REVERTED tx={txh}"
            print(f"  ✅ register 成功 tx={txh}")
            time.sleep(3)  # 等状态可读
        except Exception as e:
            return address, f"REGISTER_FAIL: {e}"

    # 3. 判断今天是否已签
    if registered and already_checked_today(last_ts):
        return address, f"SKIP_今日已签({done}/{TARGET_CHECKINS})"

    # 4. checkIn()
    try:
        txh, st = send_tx(w3, key, SEL_CHECKIN, "checkIn")
        if st == 1:
            counts[address] = done + 1
            return address, f"CHECKIN_OK({counts[address]}/{TARGET_CHECKINS}) tx={txh}"
        return address, f"CHECKIN_REVERTED tx={txh}"
    except Exception as e:
        emsg = str(e).lower()
        if "already" in emsg or "checked" in emsg or "aba47339" in emsg:
            return address, "SKIP_合约拒绝(可能已签)"
        return address, f"CHECKIN_FAIL: {e}"


def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    rpc_url = os.getenv("BSC_RPC", DEFAULT_RPC)
    proxy = os.getenv("SOCKS_PROXY", "socks5h://127.0.0.1:7890")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"proxies": {"http": proxy, "https": proxy}}))

    if not w3.is_connected():
        print(f"[ERROR] RPC 连接失败: {rpc_url}")
        notify_wechat(f"RealGo 签到失败\n❌ RPC 连接失败")
        sys.exit(1)

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[INFO] {ts} | chain={w3.eth.chain_id} | contract={CONTRACT_ADDRESS}")
    keys = load_private_keys()
    counts = load_counts()
    print(f"[INFO] Loaded {len(keys)} wallet(s) | 目标 {TARGET_CHECKINS} 次/钱包\n")

    ok = reg = skip = fail = done = 0
    for i, key in enumerate(keys, 1):
        address = w3.eth.account.from_key(key).address
        print(f"[{i}/{len(keys)}] {address}")
        addr, result = process_wallet(w3, key, counts)
        print(f"  -> {result}")
        save_counts(counts)  # 每个钱包后即时落盘,防中断丢计数
        if "CHECKIN_OK" in result:
            ok += 1
        elif "DONE_" in result:
            done += 1
        elif "SKIP" in result:
            skip += 1
        elif "FAIL" in result or "REVERTED" in result:
            fail += 1
        if "register 成功" in result or "REGISTER" in result:
            reg += 1
        if i < len(keys):
            time.sleep(random.uniform(*DELAY_RANGE))
        print()

    finished = sum(1 for a in (w3.eth.account.from_key(k).address for k in keys)
                   if counts.get(a, 0) >= TARGET_CHECKINS)
    summary = (f"RealGo 签到完成\n✅ 本次签到: {ok}\n⏭️ 跳过: {skip}\n"
               f"🏁 已达标({TARGET_CHECKINS}次): {finished}/{len(keys)}\n❌ 失败: {fail}")
    print("=" * 50)
    print(summary.replace("\n", " | "))
    notify_wechat(summary)


if __name__ == "__main__":
    main()
