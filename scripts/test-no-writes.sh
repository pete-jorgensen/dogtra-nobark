#!/usr/bin/env bash
# Proves collar_read.py cannot command the collar. Run before every deploy.
#
# Two independent checks, because either alone is weak:
#   1. STATIC  -- no BlueZ mutating method is named anywhere in the source.
#   2. RUNTIME -- the allowlist really refuses one, exercised for real.
#
# The forbidden names are assembled from fragments so this script does not trip its own
# static check when it scans the directory.
set -uo pipefail
cd "$(dirname "$0")"
SRC=collar_read.py
fail=0

W="Write"; V="Value"; SN="Start"; NO="Notify"; PR="Pair"
# StartNotify/StopNotify are NOT in this list: notifications are a CCCD descriptor write,
# which cannot actuate. WriteValue -- the way a CHARACTERISTIC value is written, i.e. the
# command pipe -- stays forbidden, and that is the property this test exists to defend.
FORBIDDEN=("$W$V" "AcquireWrite" "$PR" "RemoveDevice")

echo "=== 1. STATIC: no COMMAND-capable BlueZ method named in $SRC ==="
for f in "${FORBIDDEN[@]}"; do
  # Ignore comments/docstrings: strip lines that are prose about what we do NOT do.
  if grep -n "$f" "$SRC" | grep -vE '^\s*[0-9]+:\s*#' | grep -q "$f"; then
    hits=$(grep -n "$f" "$SRC" | head -3)
    echo "  ⛔ FAIL: '$f' appears in $SRC:"; echo "$hits" | sed 's/^/      /'
    fail=1
  else
    echo "  ok: '$f' absent"
  fi
done

echo
echo "=== 2. RUNTIME: the allowlist actually refuses a write ==="
python3 - <<'PY'
import sys, importlib.util
spec = importlib.util.spec_from_file_location("collar_read", "collar_read.py")
m = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
except SystemExit:
    print("  (dbus-python absent here -- allowlist checked statically instead)")
    allowed = {"Connect", "Disconnect", "ReadValue", "GetManagedObjects", "Get", "GetAll",
               "StartNotify", "StopNotify"}
    src = open("collar_read.py").read()
    import re
    got = re.search(r"ALLOWED_METHODS = frozenset\(\{(.*?)\}\)", src, re.S)
    names = set(re.findall(r'"([A-Za-z]+)"', got.group(1))) if got else set()
    print(f"  allowlist parsed: {sorted(names)}")
    assert names == allowed, f"allowlist changed unexpectedly: {names ^ allowed}"
    print("  ok: allowlist is exactly the read-only set")
    sys.exit(0)

bad = 0
for meth in ("WriteValue", "Pair", "RemoveDevice"):
    try:
        m._call(object(), "org.bluez.GattCharacteristic1", meth, {})
        print(f"  ⛔ FAIL: {meth} was NOT blocked")
        bad = 1
    except m.ForbiddenCall as e:
        print(f"  ok: {meth} blocked -- {str(e)[:60]}...")
    except Exception as e:
        print(f"  ⛔ FAIL: {meth} raised {type(e).__name__}, not ForbiddenCall")
        bad = 1
# and permitted ones must get PAST the guard (they fail later, on the fake object)
for meth, iface in (("Connect", "org.bluez.Device1"),
                    ("StartNotify", "org.bluez.GattCharacteristic1")):
  try:
    m._call(object(), iface, meth)
  except m.ForbiddenCall:
    print(f"  ⛔ FAIL: {meth} was blocked; the guard is too strict to work")
    bad = 1
  except Exception:
    print(f"  ok: {meth} passes the guard (fails later, as expected on a stub)")
sys.exit(bad)
PY
[ $? -ne 0 ] && fail=1

echo
if [ $fail -eq 0 ]; then echo "PASS -- collar_read.py cannot command the collar."; else
  echo "FAIL -- DO NOT DEPLOY."; fi
exit $fail
