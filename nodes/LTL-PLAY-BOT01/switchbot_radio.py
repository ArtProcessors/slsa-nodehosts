#!/usr/bin/env python3
'''
Windows Bluetooth radio reset -- invoked by the Nodel SwitchBot recipe.

The Intel Bluetooth stack wedges after some hours of host uptime: the driver
reports OK, bthserv is Running and the WinRT radio reports On, but BLE discovery
returns ZERO devices and every press times out. Toggling the radio off and on
clears it, with no reboot.

Uses the WinRT Radio API, NOT Disable-PnpDevice: the PnP route needs an elevated
process and the Nodel host is not elevated (it returns "Generic failure" /
HRESULT 0x800 and changes nothing). The Radio API is the same switch as
Settings > Bluetooth & devices, and needs no admin rights.

Usage: python3 switchbot_radio.py [--verify]
Prints "ok" and exits 0 once the radio is back on.
--verify also runs a 10s BLE scan and prints the device count -- a count of 0
after a reset means the radio is not the (only) problem.
'''
import sys, asyncio

OFF_SECONDS = 3.0   # long enough for the stack to actually drop
SETTLE_SECONDS = 6.0  # the radio reports On before it will answer a scan


def _fail(msg):
    sys.stderr.write(msg + '\n')
    sys.exit(1)


try:
    from winrt.windows.devices.radios import Radio, RadioKind, RadioState
except ImportError:
    try:
        from bleak_winrt.windows.devices.radios import Radio, RadioKind, RadioState
    except ImportError:
        _fail('no WinRT radio API available -- the radio can only be reset on Windows')


def _enum(cls, name, fallback):
    '''Member casing differs between the winrt and bleak_winrt bindings.'''
    for n in (name, name.upper(), name.capitalize(), name.lower()):
        if hasattr(cls, n):
            return getattr(cls, n)
    return fallback


STATE_ON = _enum(RadioState, 'on', 1)
STATE_OFF = _enum(RadioState, 'off', 2)
KIND_BLUETOOTH = _enum(RadioKind, 'bluetooth', 3)


async def _bluetooth_radio():
    radios = await Radio.get_radios_async()
    for r in radios:
        if r.kind == KIND_BLUETOOTH:
            return r
    return None


async def main(verify):
    # Without this the set_state calls silently no-op on some builds.
    await Radio.request_access_async()

    radio = await _bluetooth_radio()
    if radio is None:
        _fail('no Bluetooth radio found on this machine')
    print('before: state=%s name=%s' % (radio.state, radio.name))

    await radio.set_state_async(STATE_OFF)
    await asyncio.sleep(OFF_SECONDS)
    await radio.set_state_async(STATE_ON)
    await asyncio.sleep(SETTLE_SECONDS)

    radio = await _bluetooth_radio()
    print('after:  state=%s' % (radio.state if radio else '?'))
    if radio is None or radio.state != STATE_ON:
        _fail('radio did not come back on -- it may be blocked by airplane mode or policy')

    if verify:
        from bleak import BleakScanner
        found = await BleakScanner.discover(timeout=10.0)
        # Zero here means the toggle did not unwedge it -- reboot is the next step.
        print('scan count=%d' % len(found))

    print('ok')


if __name__ == '__main__':
    args = sys.argv[1:]
    if args not in ([], ['--verify']):
        sys.stderr.write('usage: switchbot_radio.py [--verify]\n')
        sys.exit(2)
    asyncio.run(main('--verify' in args))
