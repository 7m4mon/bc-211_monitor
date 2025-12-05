# BC-211 Battery Monitor  
Web-based multi-slot battery monitor for the Icom **BC-211** six-slot charger.  
Reads the LED status signals via **MCP23017 (I²C)** and a **CP2112 USB–I²C bridge**,  
then displays real-time information on a browser and sends optional notifications.

CP2112 driver is independently implemented.

---

## ✨ Features

- Monitor **all 6 slots** of BC-211 simultaneously  
- Detect states: **SLOT EMPTY**, **CHARGING**, **FULL**, **ERR**
- 1-second live update over HTTP  
- Works on Windows, Linux, Raspberry Pi  
- **ntfy.sh** push notifications when any slot becomes *FULL*  
- Simple hardware: no microcontroller required  
- CP2112 driver is MIT-licensed (no GPL contamination)

---

## 🧱 Hardware Requirements

| Component | Purpose |
|----------|---------|
| **Icom BC-211** | 6-slot battery charger (BP-272 etc.) |
| **CP2112 USB–I²C bridge** | Interface to PC/Raspberry Pi |
| **MCP23017** | 16-bit I/O expander for LED signal reading |
| **Schottky diode (BAT54 etc.)** | Non-inverting LED readout |

![Image](https://github.com/user-attachments/assets/716f254f-b0d3-4914-86fc-3da4a7446c0a)
![Image](https://github.com/user-attachments/assets/5fffc91c-1c30-427f-8138-211dd84e5ede)
![Image](https://github.com/user-attachments/assets/ff155845-1fe7-47b1-8361-45ed5bcddbd8)
![Image](https://github.com/user-attachments/assets/704ba817-b20c-4b19-b932-965842e848bc)
![Image](https://github.com/user-attachments/assets/4b2dc1d1-0c37-475e-ac95-eb289cec8024)

---

## ⚡ Wiring Summary

- NJW4100 → **Schottky diode** → **MCP23017 input**
- MCP23017 operates with internal pull-up enabled
- MCP23017 I²C bus ←→ CP2112  
- CP2112 USB → PC / Raspberry Pi

Active-Low LED logic:  
- `0 = LED ON`  
- `1 = LED OFF`

---

## 📁 Directory Structure

```
bc211-monitor/
│
├─ cp2112_driver.py   # MIT-licensed CP2112 driver (no GPL)
├─ app.py              # Flask web application
├─ templates/
│    └─ index.html     # Web dashboard UI
│
├─ config.json         # { "ntfy_url": "https://ntfy.sh/xxxx" }
└─ README.md
```

---

## 🔧 Installation

### 1. Clone Repository

```bash
git clone https://github.com/7m4mon/bc-211_monitor.git
cd bc-211_monitor
```

### 2. Create virtual environment (Raspberry Pi recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate      # Linux / macOS
```

### 3. Install dependencies

```bash
pip install --upgrade pip
pip install flask hidapi requests
```

---

## ▶️ Running the Monitor

```bash
python app.py
```

Open:

```
http://localhost:5000
```

---

## 🔔 Optional: Push Notifications (ntfy.sh)

Edit `config.json`:

```json
{
  "ntfy_url": "https://ntfy.sh/your_topic_here"
}
```

When any slot transitions to **FULL**,  
a notification will be posted with the current slot summary.


![Image](https://github.com/user-attachments/assets/b52c5c66-6596-4232-a6a9-a9c52baddf87)
![Image](https://github.com/user-attachments/assets/14028d89-0623-4def-bd6e-0c26dbc534c6)

---


## 🛡 License (MIT)

```
MIT License

Copyright (c) 2025 7M4MON

```

---

## 🤝 Contributions

Pull requests welcome.  
For feature suggestions or bug reports, open an issue on GitHub.
