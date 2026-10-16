#!/usr/bin/env bash
# Opt-in: real signed Debian download, loop mounts, namespaces, and recovery.
set -Eeuo pipefail

manager_source="$(readlink -f "$(dirname "$0")/../chrootable-manager")"
test_root="$(mktemp -d /tmp/chrootable-manager-test.XXXXXX)"
manager_path="$test_root/chrootable-manager"
inspect_mount="$test_root/inspect"

[[ $EUID -eq 0 ]] || { printf 'Run this test as root.\n' >&2; exit 1; }

cleanup() {
    local status=$?
    mountpoint -q "$inspect_mount" && umount "$inspect_mount" || true
    if (( status != 0 )); then
        printf 'Integration failure; operation logs follow.\n' >&2
        tail -n 35 "$test_root/store/logs/"*.log >&2 || true
    fi
    rm -rf -- "$test_root"
}
trap cleanup EXIT

# Keep shell reads stable if the development checkout changes during a long download.
cp -- "$manager_source" "$manager_path"
chmod 0755 "$manager_path"
mkdir -p "$inspect_mount"
export CHROOTABLE_HOME="$test_root/store"
export CHROOTABLE_DEBIAN_SUITE=trixie
export CHROOTABLE_AUTO_INSTALL=0
profile=${1:-minimal}
[[ $profile == minimal || $profile == python ]] || { printf 'Use minimal or python profile.\n' >&2; exit 2; }
size=1024
[[ $profile != python ]] || size=2048

"$manager_path" --create persistent-test "$size" persistent "$profile"
if [[ $profile == python ]]; then
    "$manager_path" --exec persistent-test -- python3 -c 'import venv, ssl; print("Python profile ready")'
fi
"$manager_path" --exec persistent-test -- /bin/sh -c 'printf original > /persistent-marker'
mount -o loop,ro "$CHROOTABLE_HOME/images/persistent-test.img" "$inspect_mount"
test "$(cat "$inspect_mount/persistent-marker")" = original
umount "$inspect_mount"

"$manager_path" --snapshot persistent-test clean
"$manager_path" --exec persistent-test -- /bin/sh -c 'printf changed > /persistent-marker'
"$manager_path" --restore persistent-test clean --yes
test "$("$manager_path" --exec persistent-test -- cat /persistent-marker)" = original
test "$("$manager_path" --snapshots persistent-test | wc -l)" -eq 2

"$manager_path" --try persistent-test -- touch /disposable-marker
"$manager_path" --exec persistent-test -- test ! -e /disposable-marker
"$manager_path" --exec persistent-test --isolated -- /bin/sh -c 'test "$(ls /sys 2>/dev/null | wc -l)" -eq 0; ! grep -q eth /proc/net/dev'

"$manager_path" --clone persistent-test ephemeral-test
"$manager_path" --mode ephemeral-test ephemeral
"$manager_path" --exec ephemeral-test -- touch /ephemeral-marker
mount -o loop,ro "$CHROOTABLE_HOME/images/ephemeral-test.img" "$inspect_mount"
test ! -e "$inspect_mount/ephemeral-marker"
umount "$inspect_mount"

"$manager_path" --resize persistent-test "$((size + 256))"
test "$(stat -c%s "$CHROOTABLE_HOME/images/persistent-test.img")" -eq $(((size + 256) * 1048576))
"$manager_path" --repair persistent-test --yes
python3 - "$manager_source" "$CHROOTABLE_HOME" <<'PY'
import json, pathlib, subprocess, sys
broker = pathlib.Path(sys.argv[1]).parent / 'chrootables/broker.py'
request = {'id': 1, 'action': 'snapshot', 'params': {'name': 'persistent-test', 'target': 'broker-check'}}
result = subprocess.run([sys.executable, str(broker), '--store', sys.argv[2], '--writable'], input=json.dumps(request)+'\n', capture_output=True, text=True, check=True)
events = [json.loads(line) for line in result.stdout.splitlines()]
assert events[0]['writable']
assert events[-1]['ok'], events
print('Privileged desktop broker snapshot passed')
PY
archive=$("$manager_path" --export persistent-test)
tar -tzf "$archive" >"$test_root/archive-files"
grep -qx './persistent-marker' "$test_root/archive-files"

"$manager_path" --delete persistent-test --yes
test ! -e "$CHROOTABLE_HOME/images/persistent-test.img"
test -e "$CHROOTABLE_HOME/snapshots/persistent-test/clean.img"
"$manager_path" --restore persistent-test clean --yes
"$manager_path" --exec persistent-test -- test -e /persistent-marker
"$manager_path" --drop-snapshot persistent-test clean --yes
test ! -e "$CHROOTABLE_HOME/snapshots/persistent-test/clean.img"

printf 'integration checks passed\n'
