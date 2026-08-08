#!/bin/bash

# ================= Настройки =================
SSID_NAME="CNDVIDEO"
PASSWORD="Rataty987"
PHY_IFACE="wlp0s11u1"       # Ваш физический Wi-Fi интерфейс
# =============================================

if [ "$EUID" -ne 0 ]; then
  echo "Пожалуйста, запустите скрипт с правами root (sudo)"
  exit
fi

echo "Остановка NetworkManager..."
systemctl stop NetworkManager
rfkill unblock wlan

echo "Очистка старых процессов hostapd..."
killall hostapd 2>/dev/null
sleep 1

echo "Настройка точки доступа $SSID_NAME на интерфейсе $PHY_IFACE..."

# Создаем конфигурационный файл для hostapd
CONF_FILE="/tmp/hostapd_${PHY_IFACE}.conf"
cat <<EOF > $CONF_FILE
interface=$PHY_IFACE
driver=nl80211
ssid=$SSID_NAME
hw_mode=g
channel=1
wpa=2
wpa_passphrase=$PASSWORD
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
EOF

# Поднимаем интерфейс и запускаем hostapd
ip link set dev $PHY_IFACE up
hostapd -B $CONF_FILE > /dev/null 2>&1

echo " [+] Точка доступа $SSID_NAME успешно запущена!"
