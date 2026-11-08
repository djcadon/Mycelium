#!/usr/bin/env bash

set -u

run_optional() {
  local label="$1"
  shift

  printf '\n--- %s ---\n' "$label"
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Unavailable: %s\n' "$1"
    return
  fi

  "$@" || printf '[command exited with status %s]\n' "$?"
}

printf 'Mycelium peer-to-peer Wi-Fi capability report\n'
printf 'Read-only: no network configuration will be changed.\n'

run_optional 'System' uname -a
run_optional 'Wireless modes and interface combinations' iw list
run_optional 'Active wireless interfaces' iw dev
run_optional 'NetworkManager devices' nmcli device status
run_optional 'Network links' ip -brief link
run_optional 'IP addresses' ip -brief address
run_optional 'Radio block state' rfkill list
