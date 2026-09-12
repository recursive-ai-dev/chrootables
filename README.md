# Chrootable Manager

`chrootable-manager` is a root-only Bash TUI for Debian image chroots. **new
chrootable** creates a fixed-size ext4 `.img` and bootstraps Debian with
`debootstrap --variant=minbase`. **select chrootable** opens an image details
screen, where it can be entered, cloned, expanded, repaired, exported, or
deleted.

Images live in `/var/lib/chrootable-manager/images`; metadata, operation logs,
and compressed exports are stored under the same parent. Use another location
with `CHROOTABLE_HOME`:

```bash
CHROOTABLE_HOME=/srv/chrootables sudo ./chrootable-manager
```

Run from a terminal as root:

```bash
sudo ./chrootable-manager
```

It also has a scriptable interface:

```bash
sudo ./chrootable-manager --create build-root 8192 persistent
sudo ./chrootable-manager --list
sudo ./chrootable-manager --enter build-root
sudo ./chrootable-manager --enter-isolated build-root
```

At startup the manager detects missing host commands and automatically runs
`apt-get update` and `apt-get install` for their Debian packages. The package
log is `/var/log/chrootable-manager-dependencies.log`.

Creation offers two lifecycles. **Persistent** sessions write directly to the
image. **Ephemeral** sessions mount the image read-only as an OverlayFS lower
layer and put all writes in a temporary memory-backed upper layer. The base
image is reusable; all session changes disappear on `exit`.

Every image records its Debian suite, architecture, configured size, lifecycle,
creation time, and last-used time in adjacent metadata. The details screen can
clone it, expand it (shrinking is intentionally unsupported), run a forced
ext4 repair, or export its filesystem as a `.tar.gz` archive.

Sessions start with `unshare --mount --uts --ipc --pid --fork`, then mount the
image, `/proc`, `/dev`, and a private `/run` before opening a root Bash shell.
The details screen and `--enter-isolated` command can add a private network
namespace with no host network interfaces. `/sys` is deliberately not mounted.
This is convenient isolation rather than a security sandbox: it runs as host
root and exposes host devices through `/dev`.

An opt-in end-to-end test is available at `tests/integration.sh`. It downloads
two minimal Debian roots, so run it only as root on a system where using loop
mounts and temporary 1 GiB images is acceptable.
