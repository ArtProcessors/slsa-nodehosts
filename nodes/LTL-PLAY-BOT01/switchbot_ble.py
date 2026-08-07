#!/usr/bin/env python3
'''
SwitchBot Bot BLE helper -- invoked by the Nodel recipe.

Install once on the Nodel host:   pip3 install bleak
Usage:                            python3 switchbot_ble.py <ADDRESS> <press|on|off>

<ADDRESS> is the Bot's Bluetooth MAC on Linux/Windows (e.g. E1:22:33:44:55:66),
or the CoreBluetooth device UUID on macOS (bleak hides real MACs on macOS).
Find it with:                     python3 switchbot_ble.py scan

Works for Bots with NO BLE password set. Prints "ok" and exits 0 on success.
'''
import sys, asyncio
from bleak import BleakClient, BleakScanner

WRITE_UUID = "cba20002-224d-11e6-9fb8-0002a5d5c51b"
CMDS = {
    "press": bytearray([0x57, 0x01, 0x00]),
    "on":    bytearray([0x57, 0x01, 0x01]),
    "off":   bytearray([0x57, 0x01, 0x02]),
}

# SwitchBot embeds its real MAC + device-type in the BLE advertisement, so we can
# recover it on macOS (where CoreBluetooth hides the hardware address).
SB_SERVICE_DATA_UUID = "0000fd3d-0000-1000-8000-00805f9b34fb"  # 0xFD3D
SB_COMPANY_ID = 0x0969  # 2409 = Woan Technology (SwitchBot)
SB_MODELS = {  # service_data[0] & 0x7f -> friendly name
    "H": "Bot (WoHand)  <-- THIS IS A BOT", "s": "Motion Sensor", "d": "Contact Sensor",
    "c": "Curtain", "{": "Curtain3", "T": "Meter", "i": "Meter Plus", "w": "Meter Pro",
    "u": "Color Bulb", "g": "Plug Mini", "o": "Lock", "x": "Blind Tilt",
}

def _mac(b):
    return ":".join("%02X" % x for x in b)

async def scan():
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    # closest first -- the Bot you're standing next to should top the list
    for dev, adv in sorted(found.values(), key=lambda t: t[1].rssi, reverse=True):
        sd  = adv.service_data.get(SB_SERVICE_DATA_UUID)
        mfr = adv.manufacturer_data.get(SB_COMPANY_ID)
        if not sd and not mfr:
            continue  # not a SwitchBot device -- skip the noise
        model = SB_MODELS.get(chr(sd[0] & 0x7f), "SwitchBot '%s'" % chr(sd[0] & 0x7f)) if sd else "SwitchBot?"
        # MAC is the first 6 bytes of the mfr payload; print both byte orders to be safe
        if mfr and len(mfr) >= 6:
            macs = "mac=%s (rev %s)" % (_mac(mfr[0:6]), _mac(mfr[0:6][::-1]))
        else:
            macs = "mac=?"
        print("rssi=%4d  %-34s  name=%-10s  %s" % (adv.rssi, macs, dev.name or "?", model))
        print("           connect-addr(uuid)=%s" % dev.address)

async def send(address, cmd):
    async with BleakClient(address) as client:
        await client.write_gatt_char(WRITE_UUID, CMDS[cmd], response=True)

def main():
    if len(sys.argv) == 2 and sys.argv[1] == "scan":
        asyncio.run(scan()); return
    if len(sys.argv) != 3 or sys.argv[2] not in CMDS:
        sys.stderr.write("usage: switchbot_ble.py <ADDRESS> <press|on|off>  (or: scan)\n")
        sys.exit(2)
    asyncio.run(send(sys.argv[1], sys.argv[2]))
    print("ok")

if __name__ == "__main__":
    main()
