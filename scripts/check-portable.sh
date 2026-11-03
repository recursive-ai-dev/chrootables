#!/bin/bash
# Run only in a fresh private mount namespace: host Python/Qt are masked there.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { printf '%s\n' 'Run this portable check with sudo (private mount namespace required).' >&2; exit 1; }
if [[ ${CHROOTABLE_PORTABLE_PRIVATE_NAMESPACE:-} != 1 ]]; then
    exec unshare --mount --propagation private env CHROOTABLE_PORTABLE_PRIVATE_NAMESPACE=1 bash "$0" "$@"
fi
[[ $(readlink /proc/self/ns/mnt) != $(readlink /proc/1/ns/mnt) ]] || {
    printf '%s\n' 'Refusing to mask host files outside a private mount namespace.' >&2; exit 1;
}
mount --make-rprivate /
[[ $# == 1 ]] || { printf '%s\n' 'Usage: check-portable.sh APPIMAGE' >&2; exit 2; }
artifact=$(readlink -f -- "$1")
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d /tmp/chrootables-portable.XXXXXX)
weston_pid=
cleanup() {
    if [[ -n $weston_pid ]]; then
        kill "$weston_pid" 2>/dev/null || :
        for ((attempt=0; attempt<30; attempt++)); do
            kill -0 "$weston_pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL "$weston_pid" 2>/dev/null || :
        wait "$weston_pid" 2>/dev/null || :
    fi
    rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
chmod 0755 "$work"
# Upstream extract-and-run uses a shared content-named directory and can leave
# it behind. Keep all runtime extraction inside this invocation's cleanup scope.
export TMPDIR="$work"
(cd -- "$work"; "$artifact" --appimage-extract >/dev/null)
stage="$work/squashfs-root"
python="$stage/usr/lib/chrootables/runtime/bin/python3"
[[ -x $python ]] || { printf '%s\n' 'This is not a bundled-runtime AppImage.' >&2; exit 1; }
# The checker extracts as root, whereas normal launches extract as the invoking
# user. Permit the export test's dropped user to traverse this root-owned fixture
# without giving that user write access to code subsequently executed as root.
find "$stage" -type d -exec chmod 0755 {} +
mkdir "$work/empty"
shopt -s nullglob
declare -A masked=()
for candidate in /usr/bin/python3 /usr/bin/python3.[0-9]* /usr/local/bin/python3 /usr/local/bin/python3.[0-9]*; do
    [[ -f $candidate && -x $candidate ]] || continue
    resolved=$(readlink -f -- "$candidate")
    [[ ${masked[$resolved]:-} != 1 ]] || continue
    mount --bind /bin/false "$resolved"
    masked[$resolved]=1
done
for directory in /usr/lib/python3/dist-packages/PySide6 /usr/lib/python3.*/site-packages/PySide6 \
        /usr/local/lib/python3.*/site-packages/PySide6 /usr/local/lib/python3.*/dist-packages/PySide6; do
    [[ ! -d $directory ]] || mount --bind "$work/empty" "$directory"
done
for candidate in /usr/lib/*/libQt6*.so* /usr/lib/libQt6*.so* /usr/lib64/libQt6*.so* /usr/local/lib/libQt6*.so*; do
    [[ -f $candidate ]] || continue
    resolved=$(readlink -f -- "$candidate")
    [[ ${masked[$resolved]:-} != 1 ]] || continue
    mount --bind /bin/false "$resolved"
    masked[$resolved]=1
done
if [[ -x /usr/bin/python3 ]] && /usr/bin/python3 -c 'raise SystemExit(0)' 2>/dev/null; then
    printf '%s\n' 'Host Python was not masked.' >&2; exit 1
fi
export QT_QPA_PLATFORM=offscreen APPIMAGE_EXTRACT_AND_RUN=1
"$python" -I -B "$scripts/check-portable.py" "$stage" --export
"$python" -I -B "$scripts/check-installed.py" --prefix "$stage/usr"
if [[ -n $(find "$stage" \( -type d -name __pycache__ -o -type f -name '*.pyc' \) -print -quit) ]]; then
    printf '%s\n' 'Installed helper/desktop wrote bytecode into the extracted distribution.' >&2
    exit 1
fi
# Exercise the normal AppRun extraction supervisor even on hosts without FUSE.
APPIMAGE="$artifact" env -u APPIMAGE_EXTRACT_AND_RUN -u CHROOTABLE_APPIMAGE_EXTRACTED \
    timeout 30 "$stage/AppRun" --demo --screenshot "$work/offscreen.png"
if compgen -G "$work/chrootables-appimage.*" >/dev/null; then
    printf '%s\n' 'Successful AppRun launch did not clean its private extraction.' >&2
    exit 1
fi
QT_QPA_PLATFORM=xcb timeout 30 xvfb-run -a "$artifact" --demo --screenshot "$work/xcb.png"
mkdir -m 0700 "$work/wayland"
export XDG_RUNTIME_DIR="$work/wayland" WAYLAND_DISPLAY=chrootables-check
weston --backend=headless --renderer=pixman --socket="$WAYLAND_DISPLAY" --idle-time=0 --no-config \
    >"$work/weston.log" 2>&1 &
weston_pid=$!
for ((attempt=0; attempt<100; attempt++)); do
    [[ ! -S $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ]] || break
    if ! kill -0 "$weston_pid" 2>/dev/null; then
        cat "$work/weston.log" >&2
        exit 1
    fi
    sleep 0.1
done
[[ -S $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY ]] || {
    cat "$work/weston.log" >&2
    printf '%s\n' 'Headless Weston did not become ready within 10 seconds.' >&2
    exit 1
}
if ! QT_QPA_PLATFORM=wayland timeout 30 "$artifact" --demo --screenshot "$work/wayland.png" \
        >"$work/wayland.log" 2>&1; then
    cat "$work/wayland.log" >&2
    exit 1
fi
if grep -qi 'No decoration plugins available' "$work/wayland.log"; then
    cat "$work/wayland.log" >&2
    exit 1
fi
[[ -s $work/offscreen.png && -s $work/xcb.png && -s $work/wayland.png ]]
printf '%s\n' 'Portable AppImage passed with host Python/Qt masked: offscreen, X11/Xvfb and Wayland/Weston rendered.'
