import asyncio
import threading
import subprocess
import time
import io
from Xlib import X, display
from flask import Flask, render_template_string, request, jsonify, Response
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.gatt.descriptor import descriptor, DescriptorFlags as DescFlags
from bluez_peripheral.util import get_message_bus
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.agent import NoIoAgent
from dbus_next.service import dbus_property, PropertyAccess
from dbus_next.signature import Variant
import mss
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: sans-serif; text-align: center; background: #0f172a; color: #f8fafc; margin-top: 10px; }
        .card { background: #1e293b; display: inline-block; padding: 15px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); margin: 10px; width: 90%; max-width: 400px; vertical-align: top; text-align: left; }
        button { font-size: 15px; padding: 12px; margin: 6px 0; cursor: pointer; background: #3b82f6; color: white; border: none; border-radius: 8px; font-weight: bold; transition: 0.2s; width: 100%; }
        button:active { background: #2563eb; transform: scale(0.98); }
        .click-btn { background: #10b981; }
        .click-btn:active { background: #059669; }
        .secondary-btn { background: #64748b; }
        .secondary-btn:active { background: #475569; }
        ul { padding-left: 0; list-style: none; max-height: 200px; overflow-y: auto; background: #0f172a; border-radius: 8px; padding: 10px; }
        li { font-size: 14px; margin-bottom: 8px; background: #1e293b; padding: 8px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; word-break: break-all; }
        .screen-preview { width: 100%; height: auto; background: #000; border-radius: 8px; margin-bottom: 12px; border: 1px dashed #475569; overflow: hidden; }
        .screen-preview img { width: 100%; display: block; }
        .view { display: none; }
        .active { display: block; }
        .d-pad { text-align: center; margin-top: 10px; }
        .d-pad button { width: auto; display: inline-block; margin: 4px; padding: 15px 25px; touch-action: manipulation; }
    </style>
</head>
<body>
    <h1>ArchMouse Control</h1>

    <!-- Экран 1: Выбор устройства -->
    <div id="view-devices" class="card active">
        <h3>🔗 Подключение</h3>
        <p style="font-size: 13px; color: #94a3b8;">1. Подключись к UxPlay на iPhone.<br>2. Найди ArchMouse в Bluetooth.</p>
        <button onclick="scanDevices()">🔍 Искать устройства (Bluetooth)</button>
        <ul id="deviceList">
            <li style="justify-content: center; color: #64748b;">Эфир чист...</li>
        </ul>
        <button class="secondary-btn" onclick="showControlView()" style="margin-top: 15px;">Я уже подключил мышь ➡️</button>
    </div>

    <!-- Экран 2: Контроль и Снимок -->
    <div id="view-control" class="view">
        <div class="card">
            <h3>📱 Экран iPhone (через сервер)</h3>
            <div class="screen-preview">
                <img src="/video_feed" alt="Ожидание стрима (включи uxplay)...">
            </div>
        </div>

        <div class="card">
            <h3>🖱️ Управление курсором</h3>
            <div class="d-pad">
                <div><button onclick="move(0, -30)">⬆️</button></div>
                <div>
                    <button onclick="move(-30, 0)">⬅️</button>
                    <button class="click-btn" onclick="clickMouse()">Клик</button>
                    <button onclick="move(30, 0)">➡️</button>
                </div>
                <div><button onclick="move(0, 30)">⬇️</button></div>
            </div>
            <br>
            <button class="secondary-btn" onclick="showDeviceView()">⬅️ Назад в настройки</button>
        </div>
    </div>

    <script>
        function showControlView() {
            document.getElementById('view-devices').classList.remove('active');
            document.getElementById('view-control').classList.add('active');
        }

        function showDeviceView() {
            document.getElementById('view-control').classList.remove('active');
            document.getElementById('view-devices').classList.add('active');
        }

        function scanDevices() {
            const list = document.getElementById('deviceList');
            list.innerHTML = "<li style='justify-content: center; color: #38bdf8;'>Сканирование (4 сек)...</li>";
            fetch('/scan').then(res => res.json()).then(data => {
                list.innerHTML = "";
                if(data.length === 0) {
                    list.innerHTML = "<li style='justify-content: center; color: #f87171;'>Устройства не найдены</li>";
                    return;
                }
                data.forEach(dev => {
                    let li = document.createElement('li');
                    li.innerHTML = `<span><b>${dev.name || "Unknown"}</b><br><small>${dev.address}</small></span>` +
                                   `<button style="width: auto; padding: 6px 12px; font-size: 12px;" onclick="connectDev('${dev.address}')">OK</button>`;
                    list.appendChild(li);
                });
            });
        }

        function connectDev(addr) {
            showControlView();
        }

        function move(x, y) {
            fetch(`/move?x=${x}&y=${y}`);
        }

        function clickMouse() {
            fetch('/move?x=0&y=0&click=1').then(() => {
                setTimeout(() => fetch('/move?x=0&y=0&click=0'), 100);
            });
        }
    </script>
</body>
</html>
"""

def find_window_geometry(name_containing="UxPlay"):
    """Находит координаты и размер окна X11 по части имени с отладкой"""
    try:
        d = display.Display()
        root = d.screen().root
        
        net_client_list = d.intern_atom('_NET_CLIENT_LIST')
        window_ids = root.get_full_property(net_client_list, X.AnyPropertyType)
        
        if window_ids is None:
            return None
            
        for win_id in window_ids.value:
            win = d.create_resource_object('window', win_id)
            wm_name = win.get_wm_name()
            if wm_name and name_containing.lower() in wm_name.lower():
                geometry = win.get_geometry()
                trans = root.translate_coords(win, 0, 0)
                
                return {
                    'left': trans.x_dst,
                    'top': trans.y_dst,
                    'width': geometry.width,
                    'height': geometry.height
                }
    except Exception as e:
        print("Ошибка Xlib при поиске окна:", e)
    return None

def generate_frames():
    """Генератор MJPEG-видеопотока с захватом ОКНА UXPLAY"""
    with mss.mss() as sct:
        while True:
            monitor_bbox = find_window_geometry("UxPlay")
            
            if monitor_bbox is not None:
                try:
                    sct_img = sct.grab(monitor_bbox)
                    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    
                    img_io = io.BytesIO()
                    img.thumbnail((800, 800), Image.Resampling.LANCZOS)
                    img.save(img_io, format='JPEG', quality=45)
                    frame = img_io.getvalue()
                    
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    
                    time.sleep(0.05)
                except Exception:
                    time.sleep(0.2)
                    pass
            else:
                img = Image.new('RGB', (640, 480), color=(0, 0, 0))
                img_io = io.BytesIO()
                img.save(img_io, format='JPEG', quality=20)
                frame = img_io.getvalue()
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                time.sleep(1.0)

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
        except Exception as e:
            print("Ошибка отправки BLE пакета:", e)
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
    except Exception as e:
        print("Ошибка сканирования Bluetooth:", e)
    return jsonify(devices)

def keep_alive_ping():
    """Фоновый пинг, чтобы iOS не отключала мышь из-за неактивности"""
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
