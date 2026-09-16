#!/usr/bin/env bash
# No root, downloads or mounts. Real files, flock, ext4 checks, and fault injection.
set -Eeuo pipefail
umask 077
manager_path=$(readlink -f "$(dirname "$0")/../chrootable-manager")
# shellcheck source=../chrootable-manager
source "$manager_path"

assert_eq() { [[ $1 == "$2" ]] || { printf 'Expected <%s>, got <%s>\n' "$1" "$2" >&2; exit 1; }; }
assert_contains() { [[ $1 == *"$2"* ]] || { printf 'Missing <%s> in <%s>\n' "$2" "$1" >&2; exit 1; }; }
expect_status() {
    local expected=$1 actual=0
    shift
    "$@" >"$STORE_DIR/stdout" 2>"$STORE_DIR/stderr" || actual=$?
    assert_eq "$expected" "$actual"
}
fixture() {
    local name=${1:-lab} lifecycle=${2:-persistent}
    truncate -s 16M "$IMAGE_DIR/$name.img"
    write_metadata "$IMAGE_DIR/$name.img" "$lifecycle" stable amd64 16 2025-01-01T00:00:00+00:00 never build
}
no_partials() {
    local partial
    while IFS= read -r partial; do fail "Leaked temporary file: $partial"; done < <(find "$STORE_DIR" -name '*.partial*')
}
test_validation() {
    local value
    for value in a build-root A.1_x; do valid_name "$value"; done
    for value in '' ../escape /tmp/foo -flag 'two words' '.' '..' $'line\nfeed'; do expect_status 1 valid_name "$value"; done
    valid_size_mib 1; valid_size_mib 1048576
    for value in 0 01 -1 1.5 1048577 999999999999999999999999; do expect_status 1 valid_size_mib "$value"; done
}
test_cli_validation_before_writes() {
    expect_status 2 env CHROOTABLE_HOME="$STORE_DIR/untouched" "$manager_path" --delete lab
    expect_status 2 env CHROOTABLE_HOME="$STORE_DIR/untouched" "$manager_path" --create lab 0 persistent
    expect_status 2 env CHROOTABLE_HOME="$STORE_DIR/untouched" "$manager_path" --exec lab --
    expect_status 2 env CHROOTABLE_HOME="$STORE_DIR/untouched" "$manager_path" --enter ../escape
    expect_status 2 env CHROOTABLE_HOME="$STORE_DIR/untouched" "$manager_path" --list --bogus
    [[ ! -e $STORE_DIR/untouched ]]
}
test_empty_readonly_store() {
    local output
    output=$(CHROOTABLE_HOME="$STORE_DIR/not-created" "$manager_path" --list --json)
    assert_eq '[]' "$output"
    CHROOTABLE_HOME="$STORE_DIR/not-created" "$manager_path" --dashboard >/dev/null
    [[ ! -e $STORE_DIR/not-created ]]
}
test_json_inventory() {
    fixture
    metadata_set "$IMAGE_DIR/lab.img" suite 'a"b\c'
    export CHROOTABLE_HOME="$STORE_DIR"
    "$manager_path" --list --json | python3 -c 'import json,sys; data=json.load(sys.stdin); assert len(data)==1; x=data[0]; assert x["name"]=="lab"; assert x["suite"]=="a\"b\\c"; assert x["size_mib"]==16; assert x["snapshots"]==0; assert x["status"]=="ready"'
    "$manager_path" --inspect lab --json | python3 -c 'import json,sys; assert json.load(sys.stdin)["profile"]=="build"'
    assert_eq $'lab\tpersistent\t16 MiB' "$("$manager_path" --list)"
}
test_json_control_characters() {
    json_string $'quote" slash\\\ttab\rcarriage\nnewline\001' | python3 -c 'import json,sys; assert json.load(sys.stdin)=="quote\" slash\\\ttab\rcarriage\nnewline\x01"'
}
test_metadata_not_executed() {
    fixture
    printf 'suite=$(touch %s/pwned)\n' "$STORE_DIR" >>"$IMAGE_DIR/lab.img.meta"
    image_details "$IMAGE_DIR/lab.img" >/dev/null
    image_json "$IMAGE_DIR/lab.img" >/dev/null
    [[ ! -e $STORE_DIR/pwned ]]
}
test_legacy_metadata() {
    truncate -s 16M "$IMAGE_DIR/legacy.img"
    printf 'ephemeral\n' >"$IMAGE_DIR/legacy.img.mode"
    assert_eq ephemeral "$(image_lifecycle "$IMAGE_DIR/legacy.img")"
    metadata_set "$IMAGE_DIR/legacy.img" size_mib 32
    assert_eq ephemeral "$(image_lifecycle "$IMAGE_DIR/legacy.img")"
    metadata_set "$IMAGE_DIR/legacy.img" lifecycle nonsense
    assert_eq persistent "$(image_lifecycle "$IMAGE_DIR/legacy.img")"
}
test_metadata_atomic_failure() {
    fixture
    local before
    before=$(sha256sum "$IMAGE_DIR/lab.img.meta")
    mv() { return 71; }
    expect_status 1 metadata_set "$IMAGE_DIR/lab.img" lifecycle ephemeral
    unset -f mv
    assert_eq "$before" "$(sha256sum "$IMAGE_DIR/lab.img.meta")"
    assert_eq 1 "$(find "$IMAGE_DIR" -name '*.meta*' | wc -l)"
}
test_lock_exclusion_and_release() {
    fixture
    (
        local descriptor
        lock_image "$IMAGE_DIR/lab.img" descriptor
        assert_eq busy "$(image_status "$IMAGE_DIR/lab.img")"
        expect_status 1 clone_image "$IMAGE_DIR/lab.img" blocked
        [[ ! -e $IMAGE_DIR/blocked.img ]]
    )
    assert_eq ready "$(image_status "$IMAGE_DIR/lab.img")"
    clone_image "$IMAGE_DIR/lab.img" allowed
    [[ -f $IMAGE_DIR/allowed.img ]]
}
test_symlink_and_traversal_rejection() {
    fixture
    ln -s "$IMAGE_DIR/lab.img" "$IMAGE_DIR/link.img"
    expect_status 1 resolve_image link
    expect_status 1 resolve_image ../lab
    ln -s "$STORE_DIR/target" "$IMAGE_DIR/new.img.lock"
    expect_status 1 clone_image "$IMAGE_DIR/lab.img" new
    [[ ! -e $STORE_DIR/target ]]
    ln -s "$STORE_DIR" "$SNAPSHOT_DIR/lab"
    expect_status 1 snapshot_image "$IMAGE_DIR/lab.img" safe
}
test_clone_and_mode() {
    fixture lab ephemeral
    clone_image "$IMAGE_DIR/lab.img" copy
    cmp "$IMAGE_DIR/lab.img" "$IMAGE_DIR/copy.img"
    assert_eq ephemeral "$(image_lifecycle "$IMAGE_DIR/copy.img")"
    assert_eq lab "$(metadata_value "$IMAGE_DIR/copy.img" origin)"
    assert_eq never "$(metadata_value "$IMAGE_DIR/copy.img" last_used_at)"
    set_lifecycle "$IMAGE_DIR/copy.img" persistent
    assert_eq persistent "$(image_lifecycle "$IMAGE_DIR/copy.img")"
    assert_eq ephemeral "$(image_lifecycle "$IMAGE_DIR/lab.img")"
    expect_status 1 clone_image "$IMAGE_DIR/lab.img" copy
    no_partials
}
test_clone_failure_cleanup() {
    fixture
    cp() { return 73; }
    expect_status 73 clone_image "$IMAGE_DIR/lab.img" failed
    unset -f cp
    [[ ! -e $IMAGE_DIR/failed.img ]]
    assert_contains "$(activity)" failed
    no_partials
    assert_eq ready "$(image_status "$IMAGE_DIR/lab.img")"
}
test_snapshot_restore_and_recovery() {
    fixture
    printf 'before' | dd of="$IMAGE_DIR/lab.img" conv=notrunc status=none
    snapshot_image "$IMAGE_DIR/lab.img" clean
    printf 'after!' | dd of="$IMAGE_DIR/lab.img" conv=notrunc status=none
    set_lifecycle "$IMAGE_DIR/lab.img" ephemeral
    local changed recovery
    changed=$(sha256sum "$IMAGE_DIR/lab.img" | awk '{print $1}')
    restore_image lab clean
    cmp "$IMAGE_DIR/lab.img" "$SNAPSHOT_DIR/lab/clean.img"
    assert_eq persistent "$(image_lifecycle "$IMAGE_DIR/lab.img")"
    assert_eq 2 "$(snapshot_count lab)"
    for recovery in "$SNAPSHOT_DIR/lab/"before-restore-*.img; do
        assert_eq "$changed" "$(sha256sum "$recovery" | awk '{print $1}')"
        assert_eq ephemeral "$(image_lifecycle "$recovery")"
    done
    no_partials
}
test_restore_backup_failure_preserves_image() {
    fixture
    snapshot_image "$IMAGE_DIR/lab.img" clean
    local before
    before=$(sha256sum "$IMAGE_DIR/lab.img")
    cp() { return 73; }
    expect_status 73 restore_image lab clean
    unset -f cp
    assert_eq "$before" "$(sha256sum "$IMAGE_DIR/lab.img")"
    no_partials
}
test_delete_and_recover() {
    fixture
    snapshot_image "$IMAGE_DIR/lab.img" clean
    local inode
    inode=$(stat -c%i "$IMAGE_DIR/lab.img.lock")
    delete_image "$IMAGE_DIR/lab.img"
    [[ ! -e $IMAGE_DIR/lab.img && ! -e $IMAGE_DIR/lab.img.meta ]]
    assert_eq "$inode" "$(stat -c%i "$IMAGE_DIR/lab.img.lock")"
    assert_contains "$(list_snapshots lab)" clean
    restore_image lab clean
    cmp "$IMAGE_DIR/lab.img" "$SNAPSHOT_DIR/lab/clean.img"
    drop_snapshot lab clean
    assert_eq 0 "$(snapshot_count lab)"
    [[ -f $IMAGE_DIR/lab.img ]]
}
test_snapshot_label_collision() {
    fixture
    snapshot_image "$IMAGE_DIR/lab.img" new
    expect_status 1 snapshot_image "$IMAGE_DIR/lab.img" new
    assert_eq 1 "$(snapshot_count lab)"
    expect_status 1 restore_image lab ../escape
}
test_resize_real_ext4() {
    fixture
    mkfs.ext4 -q -F "$IMAGE_DIR/lab.img"
    resize_image "$IMAGE_DIR/lab.img" 32
    assert_eq 32 "$(image_size_mib "$IMAGE_DIR/lab.img")"
    assert_eq 32 "$(metadata_value "$IMAGE_DIR/lab.img" size_mib)"
    assert_eq never "$(metadata_value "$IMAGE_DIR/lab.img" last_used_at)"
    e2fsck -f -n "$IMAGE_DIR/lab.img"
    expect_status 1 resize_image "$IMAGE_DIR/lab.img" 16
}
test_failed_fsck_does_not_resize() {
    fixture
    e2fsck() { return 4; }
    expect_status 1 resize_image "$IMAGE_DIR/lab.img" 32
    assert_eq 16 "$(image_size_mib "$IMAGE_DIR/lab.img")"
    assert_eq 16 "$(metadata_value "$IMAGE_DIR/lab.img" size_mib)"
}
test_repair_exit_codes() {
    fixture
    e2fsck() { return 1; }
    repair_image "$IMAGE_DIR/lab.img"
    e2fsck() { return 4; }
    expect_status 4 repair_image "$IMAGE_DIR/lab.img"
}
test_external_mount_refused() {
    fixture
    losetup() { printf '/dev/loop9: fixture\n'; }
    expect_status 1 delete_image "$IMAGE_DIR/lab.img"
    expect_status 1 snapshot_image "$IMAGE_DIR/lab.img" bad
    expect_status 1 resize_image "$IMAGE_DIR/lab.img" 32
    [[ -f $IMAGE_DIR/lab.img ]]
}
test_creation_failure_cleanup() {
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    run_worker() { return 61; }
    expect_status 61 create_image broken 16 persistent minimal
    [[ ! -e $IMAGE_DIR/broken.img && ! -e $IMAGE_DIR/broken.img.meta ]]
    assert_contains "$(activity)" failed
    no_partials
}
test_creation_success_contract() {
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    run_worker() { assert_eq create "$1"; assert_eq build "$6"; }
    create_image fresh 16 ephemeral build
    [[ -f $IMAGE_DIR/fresh.img ]]
    assert_eq ephemeral "$(image_lifecycle "$IMAGE_DIR/fresh.img")"
    assert_eq build "$(metadata_value "$IMAGE_DIR/fresh.img" profile)"
    no_partials
}
test_session_exit_and_arguments() {
    fixture
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    unshare() {
        printf '%s\n' "$@" >"$STORE_DIR/session-args"
        printf 'started\n' >"$WORK/started"
        printf 'guest-output\n'
        return 42
    }
    expect_status 42 run_cli --try lab --isolated -- printf '%s' 'two words' '$(false)'
    assert_eq guest-output "$(<"$STORE_DIR/stdout")"
    assert_contains "$(<"$STORE_DIR/session-args")" $'--net\n'
    assert_contains "$(<"$STORE_DIR/session-args")" $'ephemeral\nisolated\nprintf\n%s\ntwo words\n$(false)'
    [[ $(metadata_value "$IMAGE_DIR/lab.img" last_used_at) != never ]]
    assert_eq persistent "$(image_lifecycle "$IMAGE_DIR/lab.img")"
    assert_eq ready "$(image_status "$IMAGE_DIR/lab.img")"
}
test_session_start_failure_does_not_touch_last_used() {
    fixture
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    unshare() { return 1; }
    expect_status 1 enter_chrootable "$IMAGE_DIR/lab.img" persistent shared
    assert_eq never "$(metadata_value "$IMAGE_DIR/lab.img" last_used_at)"
}
test_export_failure_cleanup() {
    fixture
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    run_worker() { return 62; }
    expect_status 62 export_image "$IMAGE_DIR/lab.img"
    assert_eq 0 "$(find "$EXPORT_DIR" -type f | wc -l)"
    assert_eq ready "$(image_status "$IMAGE_DIR/lab.img")"
}
test_log_name_boundaries() {
    fixture lab
    fixture lab-extra
    set_lifecycle "$IMAGE_DIR/lab.img" ephemeral
    set_lifecycle "$IMAGE_DIR/lab-extra.img" ephemeral
    assert_contains "$(show_logs lab)" 'mode  lab'
    [[ $(show_logs lab) != *'mode  lab-extra'* ]]
}
test_demo_cleanup() {
    local output path
    output=$("$manager_path" --demo --dashboard)
    assert_contains "$output" '3 workspaces'
    assert_contains "$output" 'DEMO / sample workspaces'
    path=$(awk '/^Store: / {print $2}' <<<"$output")
    [[ $path == /tmp/chrootable-demo.* && ! -e $path ]]
}
test_allocate_image_strategy() {
    OP_LOG="$STORE_DIR/op.log"
    local falloc_called=0 trunc_called=0
    fallocate() { falloc_called=1; touch "$3"; return 0; }
    allocate_image "$STORE_DIR/falloc.img" 16
    assert_eq 1 "$falloc_called"
    unset -f fallocate

    fallocate() { return 1; }
    truncate() { trunc_called=1; touch "$3"; return 0; }
    allocate_image "$STORE_DIR/trunc.img" 16
    assert_eq 1 "$trunc_called"
    unset -f fallocate truncate
}
test_setup_resolv_conf_systemd_stub() {
    local fake_work fake_root
    fake_work=$(mktemp -d "$STORE_DIR/work.XXXXXX")
    fake_root=$(mktemp -d "$STORE_DIR/root.XXXXXX")
    mkdir -p "$fake_root/etc"

    mount() { return 0; }
    setup_resolv_conf "$fake_work" "$fake_root"
    unset -f mount

    [[ -f $fake_work/resolv.conf ]]
    assert_eq 0 "$(grep -c '127\.0\.0\.' "$fake_work/resolv.conf" || true)"
    assert_contains "$(<"$fake_work/resolv.conf")" "nameserver"
}
test_worker_cleanup_busy_handling() {
    WORKER_ROOT="$STORE_DIR/root"
    WORKER_LOWER="$STORE_DIR/lower"
    WORKER_OVERLAY="$STORE_DIR/overlay"
    mkdir -p "$WORKER_ROOT" "$WORKER_LOWER" "$WORKER_OVERLAY"

    mountpoint() { return 0; }
    local umount_calls=()
    umount() {
        umount_calls+=("$*")
        if [[ $1 == -R ]]; then
            return 32
        fi
        return 0
    }
    kill() { return 0; }
    local rc=0
    ( worker_cleanup ) || rc=$?
    unset -f mountpoint umount kill

    assert_eq 0 "$rc"
    local called="${umount_calls[*]}"
    assert_contains "$called" "-l $WORKER_ROOT"
}
test_session_locale_and_devpts() {
    local fake_work fake_root
    fake_work=$(mktemp -d "$STORE_DIR/work.XXXXXX")
    fake_root=$(mktemp -d "$STORE_DIR/root.XXXXXX")
    WORKER_ROOT="$fake_root"

    local mounted_devpts=0
    mount() {
        if [[ $* == *"devpts"*"-o newinstance"* ]]; then
            mounted_devpts=1
        fi
        return 0
    }
    mount_session_devices
    unset -f mount
    assert_eq 1 "$mounted_devpts"
}
test_isolated_network_brings_up_lo() {
    local fake_work fake_root
    fake_work=$(mktemp -d "$STORE_DIR/work.XXXXXX")
    fake_root=$(mktemp -d "$STORE_DIR/root.XXXXXX")
    mkdir -p "$fake_root"

    local lo_up=0 lang_set=0
    ip() {
        if [[ $* == "link set lo up" ]]; then
            lo_up=1
        fi
        return 0
    }
    mount() { return 0; }
    hostname() { return 0; }
    chroot() {
        local env_str="$*"
        if [[ $env_str == *"LANG=C.UTF-8"* ]]; then
            lang_set=1
        fi
        return 0
    }

    require_root() { return 0; }
    unshare() { return 0; }
    (
        WORKER_ROOT="$fake_root"; WORKER_LOWER="$fake_work/lower"; WORKER_OVERLAY="$fake_work/overlay"
        worker_main enter "$STORE_DIR/dummy.img" "$fake_work" test-box persistent isolated echo hi
    )
    unset -f ip mount hostname chroot require_root unshare
    assert_eq 1 "$lo_up"
    assert_eq 1 "$lang_set"
}
test_pending_cleared_on_publish() {
    make_workdir() { WORK=$(mktemp -d "$STORE_DIR/work.XXXXXX"); }
    run_worker() { return 0; }
    create_image publish-check 16 ephemeral minimal
    assert_eq "" "${PENDING:-}"
    clone_image "$IMAGE_DIR/publish-check.img" clone-check
    assert_eq "" "${PENDING:-}"
}

if [[ ${1:-} == --case ]]; then
    STORE_DIR=$(mktemp -d /tmp/chrootable-regression.XXXXXX)
    configure_paths
    cleanup_test() { [[ $STORE_DIR == /tmp/chrootable-regression.* ]] && rm -rf -- "$STORE_DIR"; }
    trap cleanup_test EXIT
    init_store
    "$2"
    exit
fi

passed=0; failed=0
while IFS= read -r test_name; do
    output_file=$(mktemp /tmp/chrootable-test-output.XXXXXX)
    if bash "$0" --case "$test_name" >"$output_file" 2>&1; then
        printf 'PASS %s\n' "${test_name#test_}"
        passed=$((passed + 1))
    else
        printf 'FAIL %s\n' "${test_name#test_}"
        sed -n '1,100p' "$output_file"
        failed=$((failed + 1))
    fi
    rm -f -- "$output_file"
done < <(declare -F | awk '$3 ~ /^test_/ {print $3}')
printf '\n%s passed; %s failed\n' "$passed" "$failed"
(( failed == 0 ))
