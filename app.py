#!/usr/bin/env python3
"""
BC-211 Battery Monitor + ntfy.sh notification (CP2112 driver version)

- CP2112 I2C バス制御は自作モジュール cp2112_driver.Cp2112I2CBus を使用
- MCP23017 で BC-211 の LED 状態を読み取り
- Flask で Web ダッシュボード提供
- ntfy.sh への通知機能あり（config.json の ntfy_url を使用）

2025,Nov 7M4MON
"""

from flask import Flask, jsonify, render_template
from cp2112_driver import Cp2112I2CBus, Cp2112Error

import time
import requests
import json
import os

app = Flask(__name__)

# --- MCP23017 register map ---
I2C_ADDR = 0x20
IODIRA, IODIRB = 0x00, 0x01
IPOLA,  IPOLB  = 0x02, 0x03
GPPUA,  GPPUB  = 0x0C, 0x0D
GPIOA,  GPIOB  = 0x12, 0x13

# --- config / paths ---
#   - 開発時: この app.py があるフォルダ
#   - exe 化後(PyInstaller): 実行ファイル(.exe)が置かれているフォルダ
import sys

if getattr(sys, "frozen", False):
    # PyInstaller などで凍結された実行ファイル
    RUNTIME_DIR = os.path.dirname(sys.executable)
else:
    # 通常の Python スクリプト実行
    RUNTIME_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(RUNTIME_DIR, "config.json")
TEMPLATE_DIR = os.path.join(RUNTIME_DIR, "templates")
STATIC_DIR = os.path.join(RUNTIME_DIR, "static")

# --- globals ---
ntfy_url: str = ""
HOST: str = "0.0.0.0"
PORT: int = 5000
last_states = None   # {slot_no: "SLOT EMPTY" / "CHARGING" / "FULL" / "ERR"}
bus: Cp2112I2CBus | None = None

# Flask app (templates/static 明示版: PyInstaller exe でも index.html を読めるようにする)
app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)


# -------------------------------------------------------------
#   MCP23017 helpers (over I2C via CP2112)
# -------------------------------------------------------------
def mcp_write8(dev: Cp2112I2CBus, reg: int, val: int) -> None:
    dev.write_reg8(I2C_ADDR, reg, val & 0xFF)


def mcp_read8(dev: Cp2112I2CBus, reg: int) -> int:
    return dev.read_reg8(I2C_ADDR, reg) & 0xFF


def mcp_setup_for_diode_noninvert_3v3(dev: Cp2112I2CBus) -> None:
    """
    ダイオード非反転 + MCP23017内蔵プルアップ使用を前提とした設定。
    MCP23017 / CP2112 とも VIO=3.3V 動作を想定。
    A0..A5, B0..B5 を入力にし、内蔵プルアップを有効にする。
    """
    # A0..A5 input
    mcp_write8(dev, IODIRA, 0x3F)
    # B0..B5 input
    mcp_write8(dev, IODIRB, 0x3F)

    # 論理反転なし（Active-Low LED をそのまま 1=OFF, 0=ON で読む）
    mcp_write8(dev, IPOLA,  0x00)
    mcp_write8(dev, IPOLB,  0x00)

    # 内蔵プルアップ ON（A0..A5, B0..B5）
    mcp_write8(dev, GPPUA,  0x3F)
    mcp_write8(dev, GPPUB,  0x3F)


def read_12bits(dev: Cp2112I2CBus) -> int:
    """
    A0..A5 → bit0..5
    B0..B5 → bit6..11
    の 12bit としてまとめて返す。
    """
    a = mcp_read8(dev, GPIOA) & 0x3F  # A0..A5
    b = mcp_read8(dev, GPIOB) & 0x3F  # B0..B5
    return (b << 6) | a


# -------------------------------------------------------------
#   State decoding (Active-Low LED)
# -------------------------------------------------------------
def slot_state(r: int, g: int) -> str:
    """
    r,g: 1 = LED OFF, 0 = LED ON （オープンコレクタ Active Low）

    NJW4100 の状態想定：
        R:OFF,G:OFF → スロット空 or 初期状態
        R:ON, G:OFF → 充電中
        R:OFF,G:ON  → 充電完了
    """
    if r == 1 and g == 1:
        return "SLOT EMPTY"
    if r == 0 and g == 1:
        return "CHARGING"
    if r == 1 and g == 0:
        return "FULL"
    return "ERR"  # 同時点灯など想定外


def decode(bits12: int) -> list[dict]:
    """
    現物の配線にもとづくビット割り当て：

        bit0..5 : A0..A5 → S4..S6
        bit6..11: B0..B5 → S1..S3

    mapping:
        ("S1_R", 6), ("S1_G", 7),
        ("S2_R", 8), ("S2_G", 9),
        ("S3_R",10), ("S3_G",11),
        ("S4_R", 0), ("S4_G", 1),
        ("S5_R", 2), ("S5_G", 3),
        ("S6_R", 4), ("S6_G", 5),
    """
    mapping = [
        ("S1_R", 6), ("S1_G", 7),
        ("S2_R", 8), ("S2_G", 9),
        ("S3_R",10), ("S3_G",11),
        ("S4_R", 0), ("S4_G", 1),
        ("S5_R", 2), ("S5_G", 3),
        ("S6_R", 4), ("S6_G", 5),
    ]
    vals: dict[str, int] = {name: (bits12 >> bit) & 1 for (name, bit) in mapping}

    slots: list[dict] = []
    for i in range(1, 7):
        r = vals[f"S{i}_R"]
        g = vals[f"S{i}_G"]
        slots.append({
            "slot": i,
            "state": slot_state(r, g),
            "R": r,
            "G": g,
        })
    return slots


# -------------------------------------------------------------
#   Notification (ntfy.sh)
# -------------------------------------------------------------
def load_config() -> None:
    """
    config.json の設定を読み込む。
    例:
        {
          "ntfy_url": "https://ntfy.sh/yourtopic",
          "host": "0.0.0.0",
          "port": 5000
        }
    """
    global ntfy_url, HOST, PORT
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # ntfy.sh URL
        ntfy_url = (cfg.get("ntfy_url") or "").strip()

        # host (optional)
        host = cfg.get("host")
        if isinstance(host, str) and host.strip():
            HOST = host.strip()

        # port (optional)
        port = cfg.get("port")
        try:
            if port is not None:
                PORT = int(port)
        except (TypeError, ValueError):
            # 無効な値の場合はデフォルト PORT を維持
            pass

        print(f"[config] ntfy_url = {ntfy_url!r}")
        print(f"[config] host = {HOST!r}, port = {PORT}")
    except FileNotFoundError:
        print("[config] config.json not found, notifications disabled")
        ntfy_url = ""
    except Exception as e:
        print("[config] error loading config.json:", e)
        ntfy_url = ""


def send_ntfy(slot_full: int, slots: list[dict]) -> None:
    """
    指定スロットが FULL になったとき、ntfy.sh に通知を送る。
    ntfy_url が空なら何もしない。
    """
    global ntfy_url
    if not ntfy_url:
        return

    states_str = ", ".join(f"S{s['slot']}={s['state']}" for s in slots)
    title = f"BC-211 Slot {slot_full} FULL"
    body = f"{title}\nCurrent states: {states_str}"

    try:
        requests.post(ntfy_url, data=body.encode("utf-8"), timeout=5)
        print(f"[ntfy] sent for slot {slot_full}")
    except Exception as e:
        print("[ntfy] error:", e)


def check_full_transition(slots: list[dict]) -> None:
    """
    前回状態からの変化を見て、
    「FULL に遷移した瞬間」のスロットがあれば ntfy 送信。
    """
    global last_states
    if last_states is None:
        last_states = {s["slot"]: s["state"] for s in slots}
        return

    for s in slots:
        slot_no = s["slot"]
        prev = last_states.get(slot_no)
        now = s["state"]
        if prev != "FULL" and now == "FULL":
            send_ntfy(slot_no, slots)

    last_states = {s["slot"]: s["state"] for s in slots}


# -------------------------------------------------------------
#   CP2112 bus init / reconnect
# -------------------------------------------------------------
def init_bus() -> None:
    """
    CP2112 + MCP23017 を初期化。
    既存の bus があれば閉じてから新規オープン。
    """
    global bus
    # 既存をクローズ
    if bus is not None:
        try:
            bus.close()
        except Exception:
            pass
        bus = None

    # 新規接続
    try:
        bus = Cp2112I2CBus()
        mcp_setup_for_diode_noninvert_3v3(bus)
        print("[driver] CP2112 + MCP23017 initialized")
    except Exception as e:
        print("[driver] failed to init CP2112:", e)
        bus = None


# -------------------------------------------------------------
#   Flask routes
# -------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    global bus

    if bus is None:
        # 一応ここでも初期化を試みる
        init_bus()
        if bus is None:
            return jsonify({
                "timestamp": time.time(),
                "bits12": None,
                "slots": [],
                "ntfy_url": ntfy_url,
                "error": "CP2112 not initialized",
            }), 503

    bits = None
    last_error: Exception | None = None

    # 最大2回トライ：1回目失敗 → 再初期化 → 2回目再チャレンジ
    for attempt in range(2):
        try:
            bits = read_12bits(bus)  # type: ignore[arg-type]
            last_error = None
            break
        except (Cp2112Error, OSError, IOError, ValueError) as e:
            last_error = e
            print(f"[I2C] read error (attempt {attempt+1}):", e)
            init_bus()
            if bus is None:
                break
        except Exception as e:
            last_error = e
            print(f"[I2C] unexpected error (attempt {attempt+1}):", e)
            init_bus()
            if bus is None:
                break

    if bits is None:
        # 2回とも失敗したら、エラーJSONを返す（サーバは落とさない）
        return jsonify({
            "timestamp": time.time(),
            "bits12": None,
            "slots": [],
            "ntfy_url": ntfy_url,
            "error": f"CP2112 not connected or I2C error: {last_error}",
        }), 503

    slots = decode(bits)
    check_full_transition(slots)

    return jsonify({
        "timestamp": time.time(),
        "bits12": bits,
        "slots": slots,
        "ntfy_url": ntfy_url,
    })


# -------------------------------------------------------------
#   Main
# -------------------------------------------------------------
if __name__ == "__main__":
    load_config()
    init_bus()
    print(f"[flask] starting dev server on {HOST}:{PORT} (LAN use only)")
    print("[flask] インターネットへ公開せず、家庭内/社内LANの範囲でご利用ください。")
    app.run(host=HOST, port=PORT)
