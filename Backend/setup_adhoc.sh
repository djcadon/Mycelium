#!/usr/bin/env bash
# ==============================================================================
# Mycelium - Pure Layer-2 Ad-Hoc Setup Script
# Run on any Arduino Uno Q (Debian Linux):
#   sudo ./setup_adhoc.sh
# ==============================================================================

set -e

# Ensure system binaries are in PATH (iw installs to /usr/sbin)
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

INTERFACE="wlan0"
SSID="mycelium-mesh"
FREQ="2412" # 2.4 GHz Channel 1
# Fixed Cell ID so all boards lock onto the exact same ad-hoc cell
BSSID="02:ca:fe:ba:be:01"

echo "=========================================="
echo " Initializing Mycelium Layer-2 Radio Pipe "
echo "=========================================="

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "[!] Please run as root (use sudo)."
  exit 1
fi

# 1. Check and install dependencies (iw)
if ! command -v iw >/dev/null 2>&1; then
    echo "[+] 'iw' not found. Updating package lists and installing 'iw'..."
    apt-get update -y
    apt-get install -y iw
    echo "[✓] 'iw' installed successfully."
fi

# 2. Unmanage wlan0 from NetworkManager so it doesn't interfere
if command -v nmcli >/dev/null 2>&1; then
    echo "[+] Setting $INTERFACE to unmanaged in NetworkManager..."
    nmcli device set "$INTERFACE" managed no 2>/dev/null || true
fi

# 2b. Stop wpa_supplicant if running independently
echo "[+] Stopping wpa_supplicant (if active)..."
systemctl stop wpa_supplicant 2>/dev/null || true
killall wpa_supplicant 2>/dev/null || true

# 3. Leave any previous ad-hoc cells
iw dev "$INTERFACE" ibss leave 2>/dev/null || true

# 4. Bring interface down to reconfigure mode
echo "[+] Bringing $INTERFACE down..."
ip link set "$INTERFACE" down

# 5. Switch to Ad-Hoc (IBSS) mode
echo "[+] Switching $INTERFACE to IBSS (Ad-Hoc) mode..."
iw dev "$INTERFACE" set type ibss

# 6. Bring interface back up and let the driver settle
echo "[+] Bringing $INTERFACE up..."
ip link set "$INTERFACE" up
sleep 1

# 7. Join the mycelium mesh cell with the fixed BSSID (with retry)
echo "[+] Joining IBSS cell '$SSID' on $FREQ MHz with BSSID $BSSID..."
if ! iw dev "$INTERFACE" ibss join "$SSID" "$FREQ" bssid "$BSSID"; then
    echo "[!] Failed to join IBSS cell. Retrying in 2 seconds..."
    sleep 2
    iw dev "$INTERFACE" ibss join "$SSID" "$FREQ" bssid "$BSSID" || {
        echo "[✗] Could not join IBSS cell after retry."
        exit 1
    }
fi

# 8. Flush any existing IP addresses (Pure Layer 2, no IPs needed)
ip addr flush dev "$INTERFACE"

# 9. Verify IBSS association
echo "[+] Verifying IBSS association..."
sleep 1
LINK_STATUS=$(iw dev "$INTERFACE" link 2>&1)
if echo "$LINK_STATUS" | grep -qi "joined ibss\|connected"; then
    echo "[✓] Successfully joined IBSS cell."
else
    echo "[!] WARNING: IBSS association may not have completed."
    echo "    Status: $LINK_STATUS"
fi

# Extract permanent hardware MAC address
MAC_ADDR=$(cat /sys/class/net/"$INTERFACE"/address 2>/dev/null || ip link show "$INTERFACE" | awk '/ether/ {print $2}')
CLEAN_MAC=$(echo "$MAC_ADDR" | tr -d ':')
# Note: the space before -4 is required in bash to distinguish from the :- default value syntax
NODE_ID="Node-${CLEAN_MAC: -4}"

echo "=========================================="
echo " [✓] Mycelium Layer-2 Radio Active!"
echo "     Interface   : $INTERFACE (IBSS Mode)"
echo "     Mesh SSID   : $SSID ($FREQ MHz)"
echo "     Mesh BSSID  : $BSSID"
echo "     Hardware MAC: $MAC_ADDR"
echo "     Derived ID  : $NODE_ID"
echo "     Addressing  : Pure Layer-2 (Zero IP)"
echo "=========================================="
