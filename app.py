import asyncio
import threading
import subprocess
import time
import io
import socket
from flask import Flask, render_template_string, request, jsonify, Response, make_response
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
LATEST_FRAME = None

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
    <title>BoSMM Panel v2</title>
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
            font-family: 'Inter', sans-serif; 
            text-align: left; 
            background: var(--bg-main); 
            color: var(--text-main); 
            margin: 0;
            padding: 30px; 
        }
        .container {
            display: flex;
            flex-direction: column;
            align-items: flex-start; 
            gap: 20px;
            box-sizing: border-box;
        }
        .card { 
            background: var(--bg-card); 
            padding: 24px; 
            border-radius: 16px; 
            box-shadow: 0 10px 40px rgba(0,0,0,0.4); 
            display: inline-block; 
            width: max-content; 
            min-width: 320px;
            max-width: 100vw;
            text-align: left;
            border: 1px solid var(--border);
            box-sizing: border-box;
        }
        
        h1 { color: #fff; font-size: 26px; font-weight: 700; margin-top: 0; margin-bottom: 30px; text-align: left; }
        h3 { font-size: 18px; font-weight: 600; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-top: 0; margin-bottom: 16px; color: #f5f5f5; display: flex; justify-content: space-between; align-items: center; gap: 20px; }
        
        .loader { font-size: 12px; color: var(--accent); font-weight: normal; animation: pulse 1.5s infinite; white-space: nowrap; }
        @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }

        .device-grid {
            display: flex;
            flex-direction: column; 
            gap: 20px;
            margin-top: 20px;
            align-items: flex-start;
        }
        
        .device-card {
            background: transparent; 
            border: none; 
            padding: 0;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            display: flex;
            flex-direction: column;
            align-items: flex-start; 
        }
        .device-card:hover { transform: translateX(5px); }
        
        .device-name { font-weight: 600; color: #fff; font-size: 16px; word-break: break-word; text-align: left; }
        .device-mac { font-size: 12px; color: var(--text-muted); margin-top: 4px; font-family: monospace; text-align: left; }
        
        .device-preview {
            max-width: 300px; 
            height: auto;
            max-height: 400px; 
            object-fit: contain; 
            object-position: left top; 
            border-radius: 8px;
            margin-top: 10px;
            background: transparent; 
            border: none;
        }

        button { 
            font-family: 'Inter', sans-serif; font-size: 15px; font-weight: 600;
            padding: 12px 18px; margin: 6px 0; cursor: pointer; 
            background: var(--accent); color: #fff; border: none; border-radius: 10px; 
            transition: 0.2s; display: inline-flex; justify-content: center; align-items: center; gap: 8px;
        }
        button:hover { background: var(--accent-hover); transform: translateY(-2px); }
        button:active { transform: translateY(0); }
        
        .click-btn { background: var(--success); }
        .click-btn:hover { background: var(--success-hover); }
        .secondary-btn { background: #1f1f1f; color: var(--text-main); border: 1px solid #333; }
        .secondary-btn:hover { background: #2a2a2a; }
        
        .screen-preview { 
            background: transparent; 
            border: none; 
            display: flex; 
            justify-content: flex-start; 
            position: relative;
            cursor: crosshair;
            user-select: none;
        }
        .screen-preview img { 
            height: 50vh; 
            max-width: 100vw;
            object-fit: contain; 
            object-position: left top; 
            display: block; 
            border-radius: 12px;
            pointer-events: none; 
        }
        
        .view { display: none; animation: fadeIn 0.4s ease forwards; }
        .active { display: flex; flex-direction: column; align-items: flex-start; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        
        .d-pad { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; margin-top: 10px; }
        .d-pad-row { display: flex; gap: 10px; justify-content: flex-start; }
        .d-pad button { width: 64px; height: 64px; padding: 0; border-radius: 16px; background: #1a1a1a; border: 1px solid #333; color: #fff; box-shadow: 0 4px 15px rgba(0,0,0,0.15); display: flex; justify-content: center; align-items: center; }
        .d-pad button:hover { background: #262626; border-color: #444; }
        .d-pad button:active { background: #333; transform: scale(0.92); }
        .d-pad .click-btn { width: 80px; background: var(--accent); border: none; font-size: 16px; box-shadow: 0 4px 15px rgba(37, 99, 235, 0.3); }
        .d-pad .click-btn:hover { background: var(--accent-hover); }
        
        svg { width: 20px; height: 20px; }
    </style>
</head>
<body>
    <h1>BoSMM Panel</h1>

    <div id="view-devices" class="view active">
        <div class="container">
            <div class="card">
                <h3>
                    <span>
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="vertical-align: bottom; margin-right: 6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"></path></svg>
                        Доступные устройства
                    </span>
                    <span class="loader" id="scanStatus">Идет поиск...</span>
                </h3>
                <div class="device-grid" id="deviceGrid"></div>
                <button class="secondary-btn" onclick="showControlView()" style="margin-top: 20px;">
                    Я уже подключился
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width: 16px; height: 16px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                </button>
            </div>
        </div>
    </div>

    <div id="view-control" class="view">
        <div class="container">
            <div class="card">
                <h3>
                    <span>
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="vertical-align: bottom; margin-right: 6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
                        Экран трансляции (Зажми ЛКМ и двигай для свайпа)
                    </span>
                </h3>
                <div class="screen-preview" id="touchpad" onmousedown="startDrag(event)" onmousemove="onDrag(event)" onmouseup="endDrag()" onmouseleave="endDrag()">
                    <img id="videoStream" src="" alt="Ожидание трансляции...">
                </div>
            </div>

            <div class="card">
                <h3>
                    <span>
                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="vertical-align: bottom; margin-right: 6px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 15l-2 5L9 9l11 4-5 2zm0 0l5 5M7.188 2.239l.777 2.897M5.136 7.965l-2.898-.777M13.95 4.05l-2.122 2.122m-5.657 5.656l-2.12 2.122"></path></svg>
                        Точечное управление
                    </span>
                </h3>
                <div class="d-pad">
                    <div class="d-pad-row">
                        <button onclick="move(0, -30)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7"></path></svg>
                        </button>
                    </div>
                    <div class="d-pad-row">
                        <button onclick="move(-30, 0)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path></svg>
                        </button>
                        <button class="click-btn" onclick="clickMouse()">Клик</button>
                        <button onclick="move(30, 0)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path></svg>
                        </button>
                    </div>
                    <div class="d-pad-row">
                        <button onclick="move(0, 30)">
                            <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width:24px;height:24px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7 7"></path></svg>
                        </button>
                    </div>
                </div>
                <br>
                <button class="secondary-btn" onclick="showDeviceView()" style="margin-top: 15px;">
                    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24" style="width: 16px; height: 16px;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path></svg>
                    Назад в меню
                </button>
            </div>
        </div>
    </div>

    <script>
        let scanInterval;
        let previewIntervals = [];
        let isDragging = false;

        window.onload = () => {
            scanDevices();
            scanInterval = setInterval(scanDevices, 10000);
        };

        function showControlView() {
            document.getElementById('view-devices').classList.remove('active');
            document.getElementById('view-control').classList.add('active');
            document.getElementById('videoStream').src = "/video_feed?t=" + new Date().getTime();
        }

        function showDeviceView() {
            document.getElementById('view-control').classList.remove('active');
            document.getElementById('view-devices').classList.add('active');
            document.getElementById('videoStream').src = "";
        }

        function startDrag(e) {
            isDragging = true;
            fetch('/move?x=0&y=0&click=1');
        }

        function onDrag(e) {
            if (!isDragging) return;
            let dx = e.movementX || 0;
            let dy = e.movementY || 0;
            if (dx !== 0 || dy !== 0) {
                fetch(`/move?x=${dx * 1.5}&y=${dy * 1.5}&click=1`);
            }
        }

        function endDrag() {
            if (!isDragging) return;
            isDragging = false;
            fetch('/move?x=0&y=0&click=0');
        }

        function scanDevices() {
            document.getElementById('scanStatus').innerText = "Обновление...";
            fetch('/scan').then(res => res.json()).then(data => {
                const grid = document.getElementById('deviceGrid');
                document.getElementById('scanStatus').innerText = "Поиск активен";
                
                previewIntervals.forEach(clearInterval);
                previewIntervals = [];

                if(data.length === 0) {
                    grid.innerHTML = "<p style='color: #ef4444; margin: 0;'>Эфир чист. Ищу устройства...</p>";
                    return;
                }
                
                grid.innerHTML = "";

                data.forEach(dev => {
                    const devName = (dev.name || "Unknown").toLowerCase();
                    const isIOS = devName.includes('iphone') || devName.includes('ipad') || devName.includes('ios');
                    
                    let imgId = 'preview-' + dev.address.replace(/:/g, '');
                    let previewHTML = '';
                    
                    if (isIOS) {
                        previewHTML = `<img id="${imgId}" class="device-preview" src="/snapshot?t=${Date.now()}" alt="iOS Screen">`;
                    }

                    const card = document.createElement('div');
                    card.className = 'device-card';
                    card.onclick = () => connectDev(dev.address);
                    card.innerHTML = `
                        <div>
                            <div class="device-name">${dev.name || "Unknown Device"}</div>
                            <div class="device-mac">${dev.address}</div>
                        </div>
                        ${previewHTML}
                    `;
                    grid.appendChild(card);

                    if (isIOS) {
                        const interval = setInterval(() => {
                            const img = document.getElementById(imgId);
                            if (img) img.src = "/snapshot?t=" + Date.now();
                        }, 5000);
                        previewIntervals.push(interval);
                    }
                });
            }).catch(() => {
                document.getElementById('scanStatus').innerText = "Ошибка поиска";
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

def gstreamer_receiver():
    global LATEST_FRAME
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(('127.0.0.1', 5001))
            print("\n[+] Приемник видеопотока успешно подключился к UxPlay!")
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
                            LATEST_FRAME = buffer[start:end+2]
                            buffer = buffer[end+2:]
                        else:
                            buffer = buffer[start:]
                    else:
                        break 
        except Exception:
            time.sleep(1)

def get_fallback_image():
    img = Image.new('RGB', (320, 240), color=(21, 21, 21))
    img_io = io.BytesIO()
    img.save(img_io, format='JPEG', quality=20)
    return img_io.getvalue()

@app.route('/')
def index():
    response = make_response(render_template_string(HTML_PAGE))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route('/snapshot')
def snapshot():
    frame = LATEST_FRAME if LATEST_FRAME else get_fallback_image()
    return Response(frame, mimetype='image/jpeg')

@app.route('/video_feed')
def video_feed():
    def generate():
        while True:
            frame = LATEST_FRAME if LATEST_FRAME else get_fallback_image()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.05)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/move')
def web_move():
    global hid_instance
    if hid_instance:
        x = int(float(request.args.get('x', 0)))
        y = int(float(request.args.get('y', 0)))
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
        subprocess.run(["bluetoothctl", "scan", "on"], capture_output=True, timeout=2)
        out = subprocess.check_output(["bluetoothctl", "devices"], timeout=2).decode("utf-8")
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
        
        adv = FixedAdvertisement("BoSMM Panel", ["1812"], 0x03C2, 0)
        await adv.register(bus)
        print("\n[+] BLE Сервер 'BoSMM Panel' успешно запущен!")
        print("[+] Перейди в браузер по IP-адресу виртуалки на порт 5000\n")
        
        await asyncio.Event().wait()

    asyncio.run(ble_main())

if __name__ == '__main__':
    video_thread = threading.Thread(target=gstreamer_receiver, daemon=True)
    video_thread.start()

    ble_thread = threading.Thread(target=run_ble_loop, daemon=True)
    ble_thread.start()
    
    ping_thread = threading.Thread(target=keep_alive_ping, daemon=True)
    ping_thread.start()
    
    app.run(host='0.0.0.0', port=5000)
