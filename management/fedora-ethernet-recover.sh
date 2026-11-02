#!/usr/bin/env bash
# Recover the RTL8153 behind the Genesys USB 3 hub if a warm boot misses it.
set -euo pipefail

sys_usb=/sys/bus/usb/devices
hub_candidates=()
for device in "$sys_usb"/*; do
    [[ -f "$device/idVendor" && -f "$device/idProduct" ]] || continue
    [[ $(<"$device/idVendor") == 05e3 && $(<"$device/idProduct") == 0626 ]] || continue
    hub_candidates+=("$device")
done

if ((${#hub_candidates[@]} != 1)); then
    echo "Expected one Genesys 05e3:0626 USB hub; found ${#hub_candidates[@]}" >&2
    exit 1
fi

hub=${hub_candidates[0]}
hub_name=${hub##*/}
port_disable="$sys_usb/$hub_name:1.0/$hub_name-port1/disable"
[[ -w "$port_disable" ]] || { echo "Ethernet hub port is unavailable: $port_disable" >&2; exit 1; }

# Avoid suspending the hub while its Ethernet child is idle or still probing.
printf 'on\n' > "$hub/power/control"

ethernet_present() {
    local child="$sys_usb/$hub_name.1"
    [[ -f "$child/idVendor" && -f "$child/idProduct" ]] &&
        [[ $(<"$child/idVendor") == 0bda && $(<"$child/idProduct") == 8153 ]]
}

if ethernet_present; then
    printf 'on\n' > "$sys_usb/$hub_name.1/power/control"
    echo "RTL8153 Ethernet adapter present on $hub_name-port1"
    exit 0
fi

echo "RTL8153 absent on $hub_name-port1; resetting that USB port"
restore_port() { printf '0\n' > "$port_disable" 2>/dev/null || true; }
trap restore_port EXIT INT TERM
for attempt in 1 2; do
    printf '1\n' > "$port_disable"
    sleep 2
    printf '0\n' > "$port_disable"
    sleep 8
    if ethernet_present; then
        printf 'on\n' > "$sys_usb/$hub_name.1/power/control"
        echo "RTL8153 recovered after port reset $attempt"
        exit 0
    fi
done

echo "RTL8153 still absent after two port resets" >&2
exit 1
