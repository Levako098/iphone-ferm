import asyncio
import threading
import subprocess
import time
import io
import socket
from flask import Flask, render_template_string, request, jsonify, Response
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.gatt.descriptor import descriptor, DescriptorFlags as DescFlags
from bluez_peripheral.util import get_message_bus
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.agent import NoIoAgent
from dbus_next.service import dbus_property, PropertyAccess
from dbus_next.signature import Variant
from PIL import Image

app = Flask(__name__)
hid_instance = None

MOUSE_REPORT_MAP = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01, 
    0x09, 0x01, 0xA1, 0x00, 0x05, 0x09, 0x19, 0x01, 
    0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 
    0x75, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x05, 
    0x81, 0x03, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x02, 
    0x81, 0x06, 0xC0, 0xC0
])

class DeviceInfo(Service):
    def __init__(self):
        super().__init__("180A", True)
    
    @characteristic("2A50", CharFlags.READ)
    def pnp_id(self, options):
        return bytes([0x02, 0xAC, 0x05, 0x2C, 0x02, 0x00, 0x01])

class HIDService(Service):
    def __init__(self):
        super().__init__("1812", True)
        global hid_instance
        hid_instance = self

    @characteristic("2A4A", CharFlags.READ)
    def hid_info(self, options):
        return bytes([0x11, 0x01, 0x00, 0x01])
    
    @characteristic("2A4B", CharFlags.READ)
    def report_map(self, options):
        return MOUSE_REPORT_MAP
    
    @characteristic("2A4D", CharFlags.READ | CharFlags.NOTIFY)
    def input_report(self, options):
        return bytes([0, 0, 0])
    
    @descriptor("2908", input_report, DescFlags.READ)
    def report_reference(self, options):
        return bytes([0x01, 0x01])
    
    @characteristic("2A4E", CharFlags.READ | CharFlags.WRITE_WITHOUT_RESPONSE)
    def protocol_mode(self, options):
        return bytes([0x01])

class FixedAdvertisement(Advertisement):
    @dbus_property(name="TxPower", access=PropertyAccess.READWRITE)
    def tx_power(self) -> 'n':
        return 0

    @tx_power.setter
    def tx_power_setter(self, value):
        pass

    async def get_properties(self):
        props = await super().get_properties()
        props["TxPower"] = Variant('n', 0)
        return props

HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>ArchMouse Web Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #0a0a0a;
            --bg-card: #151515;
            --text-main: #ededed;
            --text-muted: #888888;
            --accent: #2563eb;
            --accent-hover: #3b82f6;
            --border: #262626;
            --success: #16a34a;
            --success-hover: #22c55e;
        }
        body { 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; 
            text-align: center; 
            background: var(--bg-main); 
            color: var(--text-main); 
            margin: 0;
            padding: 30px 0;
            -webkit-font-smoothing: antialiased;
        }
        .container {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 20px;
            padding: 0 15px;
        }
        .card { 
            background: var(--bg-card); 
            padding: 24px; 
            border-radius: 24px; 
            box-shadow: 0 10px 40px rgba(0,0,0,0.4); 
            width: 100%; 
            max-width: 400px; 
            text-align: left;
            border: 1px solid var(--border);
            box-sizing: border-box;
        }
        h1 { color: #fff; font-size: 26px; font-weight: 700; margin-top: 0; margin-bottom: 30px; letter-spacing: -0.5px; text-shadow: 0 2px 10px rgba(255,255,255,0.05); }
        h3 { font-size: 18px; font-weight: 600; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-top: 0; margin-bottom: 16px; color: #f5f5f5; }
        p { color: var(--text-muted); line-height: 1.6; font-size: 14px; margin-bottom: 20px; }
        
        button { 
            font-family: 'Inter', sans-serif;
            font-size: 15px; 
            font-weight: 600;
            padding: 14px; 
            margin: 6px 0; 
            cursor: pointer; 
            background: var(--accent); 
            color: #fff; 
            border: none; 
            border-radius: 14px; 
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); 
            width: 100%; 
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 8px;
        }
        button:hover { background: var(--accent-hover); transform: translateY(-2px); box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25); }
        button:active { transform: translateY(0); }
        
        .click-btn { background: var(--success); }
        .click-btn:hover { background: var(--success-hover); box-shadow: 0 4px 12px rgba(22, 163, 74, 0.25); }
        .secondary-btn { background: #1f1f1f; color: var(--text-main); border: 1px solid #333; }
        .secondary-btn:hover { background: #2a2a2a; box-shadow: none; }
        
        ul { padding-left: 0; list-style: none; max-height: 220px; overflow-y: auto; background: var(--bg-main); border-radius: 14px; padding: 12px; border: 1px solid var(--border); margin-bottom: 20px; }
        li { font-size: 14px; margin-bottom: 8px; background: var(--bg-card); padding: 12px; border-radius: 10px; display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--border); transition: 0.2s; }
        li:hover { border-color: #404040; }
        li b { color: #fff; }
        li small { color: var(--text-muted); display: block; margin-top: 4px; font-family: monospace; font-size: 12px; }
        
        .screen-preview { width: 100%; height: auto; background: #000; border-radius: 14px; margin-bottom: 20px; border: 1px solid var(--border); overflow: hidden; min-height: 220px; display: flex; justify-content: center; align-items: center; position: relative; }
        .screen-preview img { width: 100%; display: block; object-fit: contain; }
        
        .view { display: none; animation: fadeIn 0.4s ease forwards; }
        .active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        
        .d-pad { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-top: 10px; }
        .d-pad-row { display: flex; gap: 10px; justify-content: center; }
        .d-pad button { 
            width: 64px; 
            height: 64px; 
            padding: 0; 
            border-radius: 18px; 
            background: #1a1a1a; 
            border: 1px solid #333;
            color: #fff; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.15); 
        }
        .d-pad button:hover { background: #262626; border-color: #444; }
        .d-pad button:active { background: #333; transform: scale(0.92); }
        .d-pad .click-btn { 
            width: 80px; 
            background: var(--accent); 
            border: none;
            font-size: 16px;
            box-shadow: 0 4px 15px rgba(37, 99, 235, 0.3);
        }
        .d-pad .click-btn:hover { background: var(--accent-hover); }
        
        svg { width: 22px; height: 22px; }
        
        /* Стилизация скроллбара */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-main); border-radius: 8px; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 8px; }
        ::-webkit-scrollbar-thumb:hover { background: #444; }
    </style>
</head>
<body>
    <h1>ArchMouse Control</h1>

    <div class="container" id="view-devices">
        <div class="card active">
            <h3>🔗 Подключение</h3>
            <p>1. Подключись к UxPlay на iPhone.<br>2. Найди ArchMouse в Bluetooth.</p>
            <button onclick="scanDevices()">
                <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                Искать устройства
            </button>
            <ul id="deviceList">
                <li style="justify-content: center; color: var(--text-muted); border: none; background: transparent;">Эфир чист...</li>
            </ul>
            <button class="secondary-btn" onclick="showControlView()">Я уже подключил мышь ➡️</button>
        </div>
    </div>

    <div id="view-control" class="view">
        <div class="container">
            <div class="card" style="max-width: 320px;">
                <h3>📱 Экран iPhone</h3>
                <div class="screen-preview">
                    <img id="videoStream" src="" alt="Ожидание трансляции UxPlay...">
                </div>
            </div>

            <div class="card">
                <h3>🖱️ Управление</h3>
                <div class="d-pad">
                    <div class="d-pad-row">
                        <button onclick="move(0, -30)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"></path></svg>
                        </button>
                    </div>
                    <div class="d-pad-row">
                        <button onclick="move(-30, 0)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                        </button>
                        <button class="click-btn" onclick="clickMouse()">Клик</button>
                        <button onclick="move(30, 0)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                        </button>
                    </div>
                    <div class="d-pad-row">
                        <button onclick="move(0, 30)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7 7"></path></svg>
                        </button>
                    </div>
                </div>
                <br>
                <button class="secondary-btn" onclick="showDeviceView()" style="margin-top: 10px;">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width: 18px; height: 18px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path></svg>
                    Назад в настройки
                </button>
            </div>
        </div>
    </div>

    <script>
        function showControlView() {
            document.getElementById('view-devices').style.display = 'none';
            document.getElementById('view-control').classList.add('active');
            document.getElementById('videoStream').src = "/video_feed?t=" + new Date().getTime();
        }

        function showDeviceView() {
            document.getElementById('view-control').classList.remove('active');
            document.getElementById('view-devices').style.display = 'flex';
            document.getElementById('videoStream').src = "";
        }

        function scanDevices() {
            const list = document.getElementById('deviceList');
            list.innerHTML = "<li style='justify-content: center; color: var(--accent); border: none; background: transparent;'>Сканирование (4 сек)...</li>";
            fetch('/scan').then(res => res.json()).then(data => {
                list.innerHTML = "";
                if(data.length === 0) {
                    list.innerHTML = "<li style='justify-content: center; color: #ef4444; border: none; background: transparent;'>Устройства не найдены</li>";
                    return;
                }
                data.forEach(dev => {
                    let li = document.createElement('li');
                    li.innerHTML = `<span><b>${dev.name || "Unknown"}</b><br><small>${dev.address}</small></span>` +
                                   `<button style="width: auto; padding: 8px 16px; font-size: 13px; margin: 0;" onclick="connectDev('${dev.address}')">OK</button>`;
                    list.appendChild(li);
                });
            });
        }

        function connectDev(addr) { showControlView(); }
        function move(x, y) { fetch(`/move?x=${x}&y=${y}`); }
        function clickMouse() {
            fetch('/move?x=0&y=0&click=1').then(() => {
                setTimeout(() => fetch('/move?x=0&y=0&click=0'), 100);
            });
        }
    </script>
</body>
</html>
"""

def generate_frames():
    """Прямой перехват видеопотока из TCP-сокета GStreamer (Без окон и костылей)"""
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(('127.0.0.1', 5001))
            print("\n[+] Подключились к прямому видеопотоку UxPlay!")
            s.settimeout(None)
            
            buffer = b''
            while True:
                data = s.recv(65536)
                if not data:
                    break
                buffer += data
                
                while True:
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9')
                    
                    if start != -1 and end != -1:
                        if end > start:
                            frame = buffer[start:end+2]
                            buffer = buffer[end+2:]
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                        else:
                            buffer = buffer[start:]
                    else:
                        break 
                        
        except Exception:
            img = Image.new('RGB', (640, 480), color=(15, 15, 15))
            img_io = io.BytesIO()
            img.save(img_io, format='JPEG', quality=20)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + img_io.getvalue() + b'\r\n')
            time.sleep(1)

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/move')
def web_move():
    global hid_instance
    if hid_instance:
        x = int(request.args.get('x', 0))
        y = int(request.args.get('y', 0))
        btn = int(request.args.get('click', 0))
        x_byte = x & 0xff
        y_byte = y & 0xff
        try:
            hid_instance.input_report.changed(bytes([btn, x_byte, y_byte]))
        except Exception:
            pass
    return "OK"

@app.route('/scan')
def scan():
    devices = []
    try:
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, timeout=2)
        subprocess.run(["bluetoothctl", "scan", "on"], capture_output=True, timeout=3)
        out = subprocess.check_output(["bluetoothctl", "devices"], timeout=2).decode("utf-8")
        subprocess.run(["bluetoothctl", "scan", "off"], capture_output=True, timeout=2)
        for line in out.splitlines():
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                devices.append({"address": parts[1], "name": parts[2]})
    except Exception:
        pass
    return jsonify(devices)

def keep_alive_ping():
    while True:
        time.sleep(10)
        global hid_instance
        if hid_instance:
            try:
                hid_instance.input_report.changed(bytes([0, 0, 0]))
            except Exception:
                pass

def run_ble_loop():
    async def ble_main():
        bus = await get_message_bus()
        hid = HIDService()
        dev_info = DeviceInfo()
        
        try:
            await hid.register(bus)
            await dev_info.register(bus)
        except Exception:
            pass
        
        agent = NoIoAgent()
        await agent.register(bus)
        
        adv = FixedAdvertisement("ArchMouse", ["1812"], 0x03C2, 0)
        await adv.register(bus)
        print("\n[+] BLE Сервер 'ArchMouse' успешно запущен!")
        print("[+] Перейди в браузер по IP-адресу виртуалки на порт 5000\n")
        
        await asyncio.Event().wait()

    asyncio.run(ble_main())

if __name__ == '__main__':
    ble_thread = threading.Thread(target=run_ble_loop, daemon=True)
    ble_thread.start()
    
    ping_thread = threading.Thread(target=keep_alive_ping, daemon=True)
    ping_thread.start()
    
    app.run(host='0.0.0.0', port=5000)
