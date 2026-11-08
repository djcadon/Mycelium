#!/usr/bin/env bash
# ==============================================================================
# Mycelium - BLE Node Setup Script
# Run on any fresh Arduino Uno Q (Debian Linux):
#   sudo ./setup.sh
# ==============================================================================

set -e

echo "=========================================="
echo " Initializing Mycelium BLE Mesh Radio     "
echo "=========================================="

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run as root (use sudo)."
  exit 1
fi

# 1. Install BlueZ and system Bluetooth packages if missing
if ! command -v bluetoothctl >/dev/null 2>&1 || ! command -v hciconfig >/dev/null 2>&1; then
    echo "[+] Installing BlueZ packages..."
    apt-get update -y
    apt-get install -y bluez bluez-tools rfkill python3-pip
fi

# 2. Install required Python BLE libraries (bleak, dbus-fast)
echo "[+] Checking Python BLE libraries (bleak, dbus-fast)..."
python3 -c "import bleak, dbus_fast" 2>/dev/null || {
    echo "[+] Installing bleak and dbus-fast..."
    pip3 install --break-system-packages bleak dbus-fast 2>/dev/null || pip3 install bleak dbus-fast
}

# 3. Unblock Bluetooth radio
echo "[+] Ensuring Bluetooth radio is unblocked..."
if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth 2>/dev/null || true
fi

# 4. Start and enable Bluetooth daemon
echo "[+] Starting Bluetooth service..."
systemctl start bluetooth 2>/dev/null || true
systemctl enable bluetooth 2>/dev/null || true
sleep 1

# 5. Bring up HCI controller and power it on
echo "[+] Bringing up hci0 interface..."
hciconfig hci0 up 2>/dev/null || true
sleep 1

bluetoothctl power on 2>/dev/null || true
bluetoothctl discoverable on 2>/dev/null || true

# 6. Extract hardware Bluetooth MAC address
BD_ADDR=$(hciconfig hci0 2>/dev/null | awk '/BD Address:/ {print $3}')
if [ -z "$BD_ADDR" ]; then
    BD_ADDR=$(bluetoothctl list 2>/dev/null | awk '/Controller/ {print $2}')
fi

CLEAN_MAC=$(echo "$BD_ADDR" | tr -d ':')
NODE_ID="Node-${CLEAN_MAC: -4}"

echo "=========================================="
echo " [✓] Mycelium BLE Mesh Node Ready!"
echo "     Controller : hci0 (Bluetooth 5.1)"
echo "     Hardware BD: $BD_ADDR"
echo "     Node ID    : $NODE_ID"
echo "     Role       : Equal Peer (No AP / No Router)"
echo "=========================================="
echo "To start listening:"
echo "  python3 ble.py listen"
echo "=========================================="