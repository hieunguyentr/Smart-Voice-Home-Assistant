#!/usr/bin/env python3
import argparse, glob, json, os, sys
import serial
MAP = {2: "hello robot", 141: "open the door"}
CAND = ["/dev/serial0", "/dev/ttyS0", "/dev/ttyAMA0"]
def devs():
    found = [d for d in CAND if os.path.exists(d)]
    return found or sorted(glob.glob("/dev/tty*"))
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true")
    p.add_argument("--device")
    p.add_argument("--baud", type=int, default=9600)
    a = p.parse_args()
    if a.check:
        print(json.dumps({"serial_module": True, "devices": devs()}, separators=(",", ":")))
        raise SystemExit(0)
    d = a.device or (devs()[0] if devs() else None)
    if not d:
        print("No serial device found", file=sys.stderr)
        raise SystemExit(1)
    with serial.Serial(d, baudrate=a.baud, timeout=1) as s:
        while True:
            b = s.read(1)
            if not b:
                continue
            cid = int.from_bytes(b, "big")
            print(json.dumps({"source":"gravity_voice_sensor","command_id":cid,"command":MAP.get(cid, f"unknown-{cid}")}, separators=(",", ":")), flush=True)
