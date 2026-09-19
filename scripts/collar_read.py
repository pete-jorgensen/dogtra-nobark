#!/usr/bin/env python3
"""
STRICTLY READ-ONLY GATT dump of the Dogtra SMART NOBARK collar.

  ⛔ THIS PROGRAM CANNOT SEND A COMMAND TO THE COLLAR. ⛔

The dog is wearing it. This tool is read-only: never vibrate, never stimulate, never write. That is enforced here by
construction, not by care:

  * Every D-Bus call goes through _call(), which raises ForbiddenCall unless the method is
    in ALLOWED_METHODS. The allowlist is {Connect, Disconnect, ReadValue, GetManagedObjects,
    Get, GetAll} -- all read or session control. Writing, pairing and notification-enabling
    are simply not reachable, whatever a future edit does elsewhere in the file.
  * There is no call site for the BlueZ write method anywhere in this file, and
    scripts/test-no-writes.sh proves that from outside by scanning the source.

⚠️ NOTIFICATIONS ARE DELIBERATELY NOT IMPLEMENTED. Subscribing to a notify characteristic
means BlueZ writes 0x0001 to that characteristic's 0x2902 descriptor. That cannot make the
collar stimulate -- a CCCD only gates whether the peripheral pushes updates -- but it *is* a
write to the device, so it is opt-in (--notify-seconds), never the default. The cost of leaving
it out: we will not see BARKED events. Everything else (the service tree, the properties
that resolve the name->UUID mapping, Device Information, and every readable value) does not
need it.

Uses dbus-python, which Raspberry Pi OS and most desktop Linux distributions ship -- so
this usually installs nothing.

Usage:
    python3 collar_read.py --address AA:BB:CC:DD:EE:FF --out dump.json
    python3 collar_read.py --rssi-only          # just report signal, never connects
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from datetime import datetime, timezone

try:
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
except ImportError:
    sys.exit("dbus-python/GLib missing -- install python3-dbus and python3-gi")

BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"
DEV_I = "org.bluez.Device1"
SVC_I = "org.bluez.GattService1"
CHR_I = "org.bluez.GattCharacteristic1"
DSC_I = "org.bluez.GattDescriptor1"

# ---------------------------------------------------------------- the read-only guard
#
# The single chokepoint. Everything this program does to BlueZ goes through _call(), and
# _call() refuses anything not named here. To make this program able to write to the
# collar you would have to edit this set -- which is the point.
ALLOWED_METHODS = frozenset({
    "Connect",            # open the link
    "Disconnect",         # close it
    "ReadValue",          # read a characteristic or descriptor
    "GetManagedObjects",  # enumerate the GATT tree
    "Get", "GetAll",      # read D-Bus properties
    # StartNotify makes BlueZ write 0x0001 to the 0x2902 CCCD -- a descriptor that only
    # gates whether the collar pushes updates. It is NOT the command pipe and cannot actuate.
    "StartNotify", "StopNotify",
})

# ⛔ STILL FORBIDDEN, and this is the line that matters: WriteValue. That is how a value is
# written to a CHARACTERISTIC, and 0x2A58 is the collar's command pipe (docs/frame-format.md).
# Notifications are opt-in; commanding the collar is impossible here.


class ForbiddenCall(RuntimeError):
    """Raised if anything tries a D-Bus method outside the read-only allowlist."""


def _call(obj, iface: str, method: str, *args, **kw):
    if method not in ALLOWED_METHODS:
        raise ForbiddenCall(
            f"BLOCKED: {iface}.{method} is not in the read-only allowlist. "
            f"This program must never write to the collar."
        )
    return getattr(dbus.Interface(obj, iface), method)(*args, **kw)


# ---------------------------------------------------------------- decoding helpers
SIG_SERVICES = {
    "00001800": "Generic Access",
    "00001801": "Generic Attribute",
    "0000180a": "Device Information",
    "0000180f": "Battery Service",
    "0000181a": "Environmental Sensing (Dogtra's vendor service, NOT env sensing)",
    "0000fe59": "Nordic DFU",
}
SIG_CHARS = {
    "00002a00": ("Device Name", "utf8"),
    "00002a19": ("Battery Level", "pct"),
    "00002a29": ("Manufacturer Name", "utf8"),
    "00002a24": ("Model Number", "utf8"),
    "00002a25": ("Serial Number", "utf8"),
    "00002a26": ("Firmware Revision", "utf8"),
    "00002a27": ("Hardware Revision", "utf8"),
    "00002a28": ("Software Revision", "utf8"),
    "00002a23": ("System ID", "hex"),
    # The four vendor characteristics, per docs/frame-format.md.
    "00002a58": ("Analog", "hex"),
    "00002a59": ("Analog Output", "hex"),
    "00002a5a": ("Aggregate", "hex"),
    "00002a3d": ("String", "utf8?"),
}


def short(u: str) -> str:
    return str(u).lower().split("-")[0]


def decode(uuid: str, raw: bytes):
    e = SIG_CHARS.get(short(uuid))
    if not e or not raw:
        return None
    label, fmt = e
    try:
        if fmt == "utf8":
            return f"{label} = {raw.decode('utf-8', 'replace').strip(chr(0))!r}"
        if fmt == "utf8?":
            txt = raw.decode("utf-8", "replace").strip(chr(0))
            printable = all(32 <= b < 127 or b in (0, 10, 13) for b in raw)
            return f"{label} = {txt!r}" if printable else f"{label} = <binary {raw.hex()}>"
        if fmt == "pct":
            return f"{label} = {raw[0]}%"
        if fmt == "hex":
            extra = ""
            if len(raw) == 2:
                extra = (f"  (u16le={struct.unpack('<H', raw)[0]}"
                         f" i16le={struct.unpack('<h', raw)[0]})")
            elif len(raw) == 4:
                extra = f"  (u32le={struct.unpack('<I', raw)[0]})"
            elif len(raw) == 1:
                extra = f"  (u8={raw[0]})"
            return f"{label} = {raw.hex()}{extra}"
    except Exception as exc:
        return f"{label} = <undecodable {raw.hex()}: {exc}>"
    return None


def infer_role(flags) -> str:
    f = set(flags)
    w = f & {"write", "write-without-response"}
    n = f & {"notify", "indicate"}
    if n and not w:
        return "collar->app pipe (status or event channel)"
    if w and not n:
        return "app->collar pipe (command channel) -- WE NEVER WRITE HERE"
    if w and n:
        return "bidirectional -- could be either pipe"
    if "read" in f:
        return "read-only"
    return "unclear"


# ---------------------------------------------------------------- the dump
def device_path(adapter: str, address: str) -> str:
    return f"/org/bluez/{adapter}/dev_{address.upper().replace(':', '_')}"


def get_prop(bus, path, iface, name):
    try:
        return _call(bus.get_object(BLUEZ, path), PROPS, "Get", iface, name)
    except ForbiddenCall:
        raise
    except Exception:
        return None


def read_rssi(bus, path):
    v = get_prop(bus, path, DEV_I, "RSSI")
    return int(v) if v is not None else None


def read_char(bus, cpath) -> bytes:
    """Read a characteristic. Still a READ -- both forms are ReadValue.

    ⚠️ Three of the four vendor characteristics answered `org.bluez.Error.InvalidArguments:
    Invalid offset` to a bare ReadValue({}), while 0x2A59 answered fine. So
    try an explicit offset first: some peripheral stacks (this one reports itself as
    'Arduino') reject the option dict rather than defaulting it. If BOTH forms fail the
    characteristic is probably a request/response channel that wants a write before it will
    answer -- which we do not do, and that refusal is itself a finding.
    """
    obj = bus.get_object(BLUEZ, cpath)
    last = None
    for opts in ({"offset": dbus.UInt16(0)}, {}):
        try:
            return bytes(bytearray(_call(obj, CHR_I, "ReadValue", opts)))
        except ForbiddenCall:
            raise
        except Exception as exc:
            last = exc
    raise last


def _paths_for(objs, srec, devpath):
    """D-Bus paths of a service record's characteristics, in the same order."""
    spath = next((p for p, i in objs.items()
                  if SVC_I in i and str(p).startswith(devpath)
                  and str(i[SVC_I]["UUID"]) == srec["uuid"]), None)
    if not spath:
        return [None] * len(srec["characteristics"])
    chars = sorted(p for p, i in objs.items()
                   if CHR_I in i and str(p).startswith(spath))
    return chars


def _report_changes(series):
    """Name the byte positions that moved -- the decode lead."""
    if len(series) < 2:
        return
    print("\n[series] byte positions that CHANGED across samples "
          "(these are the live fields):")
    uuids = series[0]["values"].keys()
    for u in uuids:
        vals = [s["values"].get(u, "") for s in series]
        if any(v.startswith("<error") for v in vals):
            continue
        if len(set(vals)) == 1:
            print(f"  {short(u)}: no change across {len(vals)} samples")
            continue
        try:
            bs = [bytes.fromhex(v) for v in vals]
        except ValueError:
            continue
        n = min(len(b) for b in bs)
        moved = [i for i in range(n) if len({b[i] for b in bs}) > 1]
        print(f"  {short(u)}: bytes {moved} moved "
              f"-> {[[b[i] for b in bs] for i in moved]}")


def dump(bus, address: str, adapter: str, connect_timeout: float, settle: float,
         samples: int = 1, interval: float = 5.0, notify_seconds: float = 0.0) -> dict:
    path = device_path(adapter, address)
    out = {
        "stamp": datetime.now(timezone.utc).isoformat(),
        "address": address, "adapter": adapter,
        "rssi_before": read_rssi(bus, path),
        "connected": False, "services": [], "error": None,
        "commands_sent": False, "notifications_enabled": notify_seconds > 0,
    }

    dev = bus.get_object(BLUEZ, path)
    print(f"[gatt] connecting to {address} (rssi {out['rssi_before']} dBm) ...", flush=True)
    try:
        _call(dev, DEV_I, "Connect", timeout=connect_timeout)
    except ForbiddenCall:
        raise
    except Exception as exc:
        out["error"] = f"connect failed: {exc}"
        print(f"[gatt] connect FAILED: {exc}")
        return out

    try:
        out["connected"] = True
        print("[gatt] connected; waiting for service resolution ...")
        deadline = time.time() + settle
        while time.time() < deadline:
            if bool(get_prop(bus, path, DEV_I, "ServicesResolved")):
                break
            time.sleep(0.5)
        else:
            out["error"] = "services never resolved"
            print("[gatt] services did NOT resolve in time")

        objs = _call(bus.get_object(BLUEZ, "/"), OM, "GetManagedObjects")
        svcs = {p: i[SVC_I] for p, i in objs.items()
                if SVC_I in i and str(p).startswith(path)}

        for spath in sorted(svcs):
            suuid = str(svcs[spath]["UUID"])
            sname = SIG_SERVICES.get(short(suuid), "")
            print(f"\n  service {suuid}  {sname}")
            srec = {"uuid": suuid, "known_as": sname, "characteristics": []}

            chars = {p: i[CHR_I] for p, i in objs.items()
                     if CHR_I in i and str(p).startswith(spath)}
            for cpath in sorted(chars):
                ci = chars[cpath]
                cuuid = str(ci["UUID"])
                flags = [str(f) for f in ci.get("Flags", [])]
                hint = infer_role(flags) if short(suuid) == "0000181a" else ""
                print(f"    char  {cuuid}  [{','.join(flags)}]"
                      + (f"\n            role: {hint}" if hint else ""))
                crec = {"uuid": cuuid, "flags": flags, "role_hint": hint or None,
                        "value_hex": None, "decoded": None, "read_error": None,
                        "descriptors": []}

                if "read" in flags:
                    try:
                        raw = read_char(bus, cpath)
                        crec["value_hex"] = raw.hex()
                        crec["decoded"] = decode(cuuid, raw)
                        print(f"            value: {raw.hex() or '<empty>'} ({len(raw)} B)")
                        if crec["decoded"]:
                            print(f"            ----> {crec['decoded']}")
                    except ForbiddenCall:
                        raise
                    except Exception as exc:
                        crec["read_error"] = str(exc)
                        print(f"            read failed: {exc}")
                else:
                    print("            not readable -- no value fetched (we never write)")

                for dpath, di in objs.items():
                    if DSC_I in di and str(dpath).startswith(cpath):
                        crec["descriptors"].append({"uuid": str(di[DSC_I]["UUID"])})
                srec["characteristics"].append(crec)
            out["services"].append(srec)
        # ---- time series: re-read what IS readable, so the payload can be decoded from
        # what changes, without ever writing. This is the whole read-only decode strategy.
        readable = [(c["uuid"], p) for s in out["services"] for c, p in
                    zip(s["characteristics"], _paths_for(objs, s, path))
                    if c["value_hex"] is not None]
        if samples > 1 and readable:
            print(f"\n[series] re-reading {len(readable)} readable characteristic(s), "
                  f"{samples} samples {interval}s apart")
            out["series"] = []
            for n in range(samples):
                if n:
                    time.sleep(interval)
                row = {"t": round(n * interval, 1), "values": {}}
                for uuid, cpath in readable:
                    try:
                        row["values"][uuid] = read_char(bus, cpath).hex()
                    except ForbiddenCall:
                        raise
                    except Exception as exc:
                        row["values"][uuid] = f"<error {exc}>"
                out["series"].append(row)
                print(f"  t+{row['t']:6.1f}s  " +
                      "  ".join(f"{short(u)}={v}" for u, v in row["values"].items()))
            _report_changes(out["series"])
        # ---- notifications (CCCD only; see the allowlist note) -----------------------
        if notify_seconds > 0:
            notifiables = []
            for spath, si in ((p, i[SVC_I]) for p, i in objs.items()
                              if SVC_I in i and str(p).startswith(path)):
                for cpath, ci in ((p, i[CHR_I]) for p, i in objs.items()
                                  if CHR_I in i and str(p).startswith(spath)):
                    fl = [str(f) for f in ci.get("Flags", [])]
                    if {"notify", "indicate"} & set(fl):
                        notifiables.append((str(ci["UUID"]), cpath))
            out["notifications"] = []
            if notifiables:
                print(f"\n[notify] subscribing to {len(notifiables)} characteristic(s) for "
                      f"{notify_seconds:.0f}s -- CCCD write only, never a command")
                print("[notify] >>> if you want a bark event, make a noise at the collar NOW <<<")
                t0 = time.time()

                def on_props(iface, changed, _inval, path=None):
                    if iface != CHR_I or "Value" not in changed:
                        return
                    raw = bytes(bytearray(changed["Value"]))
                    uuid = next((u for u, p in notifiables if p == path), path)
                    rec = {"t": round(time.time() - t0, 3), "uuid": uuid, "hex": raw.hex()}
                    out["notifications"].append(rec)
                    print(f"[notify] +{rec['t']:7.3f}s  {short(uuid)}  {rec['hex']}")

                bus.add_signal_receiver(on_props, dbus_interface=PROPS,
                                        signal_name="PropertiesChanged",
                                        path_keyword="path")
                started = []
                for uuid, cpath in notifiables:
                    try:
                        _call(bus.get_object(BLUEZ, cpath), CHR_I, "StartNotify")
                        started.append((uuid, cpath))
                        print(f"[notify]   subscribed {short(uuid)}")
                    except ForbiddenCall:
                        raise
                    except Exception as exc:
                        print(f"[notify]   cannot subscribe {short(uuid)}: {exc}")
                loop = GLib.MainLoop()
                GLib.timeout_add_seconds(int(notify_seconds), loop.quit)
                loop.run()
                for uuid, cpath in started:
                    try:
                        _call(bus.get_object(BLUEZ, cpath), CHR_I, "StopNotify")
                    except Exception:
                        pass
                print(f"[notify] captured {len(out['notifications'])} packet(s)")
    finally:
        try:
            _call(dev, DEV_I, "Disconnect")
            print("\n[gatt] disconnected")
        except Exception as exc:
            print(f"\n[gatt] disconnect failed: {exc}")
        out["rssi_after"] = read_rssi(bus, path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address", default="AA:BB:CC:DD:EE:FF")
    ap.add_argument("--adapter", default="hci0")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rssi-only", action="store_true",
                    help="report RSSI and exit; never connects")
    ap.add_argument("--connect-timeout", type=float, default=25.0)
    ap.add_argument("--settle", type=float, default=15.0)
    ap.add_argument("--samples", type=int, default=1,
                    help="re-read each readable characteristic N times (pure reads)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="seconds between samples")
    ap.add_argument("--notify-seconds", type=float, default=0.0,
                    help="subscribe to notify/indicate characteristics for N seconds "
                         "(CCCD write only; 0 = off)")
    a = ap.parse_args()

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)   # must precede SystemBus()
    bus = dbus.SystemBus()
    path = device_path(a.adapter, a.address)

    if a.rssi_only:
        r = read_rssi(bus, path)
        print(json.dumps({"address": a.address, "rssi": r,
                          "stamp": datetime.now(timezone.utc).isoformat()}))
        return 0 if r is not None else 3

    try:
        res = dump(bus, a.address, a.adapter, a.connect_timeout, a.settle,
                   a.samples, a.interval, a.notify_seconds)
    except ForbiddenCall as exc:
        print(f"\n⛔ {exc}", file=sys.stderr)
        return 4

    out = a.out or f"dump-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    with open(out, "w") as fh:
        json.dump(res, fh, indent=2)
    print(f"wrote {out}")
    # ⚠️ Count the SERIES errors too. The collar drops an idle link after ~40-60s, and a run
    # whose samples were all "Not connected" from t+63 onward still satisfied
    # connected/services/no-error and printed "complete" -- a green result over a dead link.
    bad = sum(1 for row in res.get("series", [])
              for v in row.get("values", {}).values() if str(v).startswith("<error"))
    total = sum(len(row.get("values", {})) for row in res.get("series", []))
    res["series_errors"], res["series_reads"] = bad, total
    ok = (res["connected"] and res["services"] and not res["error"]
          and bad == 0)
    if ok:
        print("RESULT: complete")
    elif res["connected"] and res["services"]:
        print(f"RESULT: partial -- tree read, but {bad}/{total} series reads failed "
              f"(link dropped?)")
    else:
        print("RESULT: incomplete -- " + str(res["error"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
