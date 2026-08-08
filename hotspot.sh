#!/bin/bash

# ================= Настройки =================
SSID_NAME="CNDVIDEO"
PASSWORD="Rataty987"
AP_COUNT=1                 # Количество точек доступа
PHY_IFACE="wlan0"           # Ваш физический Wi-Fi интерфейс
# =============================================

if [ "$EUID" -ne 0 ]; then
  echo "Пожалуйста, запустите скрипт с правами root (sudo)"
  exit
fi

echo "Остановка NetworkManager, чтобы он не мешал работе hostapd..."
systemctl stop NetworkManager
rfkill unblock wlan

echo "Очистка старых виртуальных интерфейсов (если есть)..."
killall hostapd 2>/dev/null
for i in $(seq 1 $AP_COUNT); do
    iw dev v_${PHY_IFACE}_$i del 2>/dev/null
done
sleep 1

echo "Создание $AP_COUNT точек доступа..."

for i in $(seq 1 $AP_COUNT); do
    VIF="v_${PHY_IFACE}_$i"
    
    # Создаем виртуальный интерфейс
    iw dev $PHY_IFACE interface add $VIF type __ap
    
    # Генерируем уникальный MAC-адрес для каждого интерфейса, чтобы избежать конфликтов
    # Формат: 02:00:00:00:00:01, 02:00:00:00:00:02 и т.д.
    HEX_ID=$(printf "%02x" $i)
    ip link set dev $VIF address 02:00:00:00:00:$HEX_ID
    
    # Разносим точки по разным каналам (1, 6, 11 - непересекающиеся)
    CHANNELS=(1 6 11 2 7 3 8 4 9 5 10)
    CHANNEL_IDX=$(( (i-1) % 11 ))
    CHANNEL=${CHANNELS[$CHANNEL_IDX]}
    
    # Создаем конфигурационный файл для hostapd
    CONF_FILE="/tmp/hostapd_${VIF}.conf"
    cat <<EOF > $CONF_FILE
interface=$VIF
driver=nl80211
ssid=$SSID_NAME
hw_mode=g
channel=$CHANNEL
wpa=2
wpa_passphrase=$PASSWORD
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

    # Запускаем интерфейс и hostapd в фоновом режиме
    ip link set dev $VIF up
    hostapd -B $CONF_FILE > /dev/null 2>&1
    
    echo " [+] Точка $VIF ($SSID_NAME) запущена на канале $CHANNEL"
done

echo "Все точки доступа успешно запущены!"
echo "Для остановки точек и возврата к нормальной работе интернета перезагрузите ПК или выполните скрипт остановки."
