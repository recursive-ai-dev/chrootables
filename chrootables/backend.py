"""Validated desktop API for the existing image manager. No shell evaluation."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "chrootable-manager"
DEFAULT_STORE = Path("/var/lib/chrootable-manager")
PROFILES = {
    "minimal": ("Clean slate", "A minimal Debian system. Make it your own.", 2),
    "build": ("Build studio", "C / C++, make, Git, curl and pkg-config.", 4),
    "python": ("Python lab", "Python 3, virtual environments, pip and Git.", 4),
    "network": ("Network toolkit", "DNS tools, ping, curl and an SSH client.", 2),
    "rust": ("Rust studio", "Rust toolchain, cargo and Git.", 6),
    "go": ("Go studio", "Go toolchain and Git.", 6),
    "web": ("Web lab", "Node.js, npm and Git.", 4),
}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MUTATIONS = {"create", "clone", "snapshot", "restore", "drop-snapshot", "resize", "mode", "repair", "delete", "export", "setup"}


def name(value):
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ValueError("Use 1–64 letters, digits, dots, underscores or hyphens; start with a letter or digit.")
    return value


def size(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 512 <= value <= 1048576:
        raise ValueError("Capacity must be between 512 MiB and 1 TiB.")
    return str(value)


def build_command(action, params):
    """Return an allowlisted argv and environment additions, never a command string."""
    if not isinstance(params, dict):
        raise ValueError("Parameters must be an object.")
    args = [str(MANAGER)]
    env = {}
    if action == "setup":
        return args + ["--setup"], {"CHROOTABLE_AUTO_INSTALL": "1"}
    if action not in MUTATIONS:
        raise ValueError("Unsupported operation.")
    target = name(params.get("name"))
    args += ["--" + action, target]
    if action == "create":
        mode = params.get("lifecycle", "persistent")
        profile = params.get("profile", "minimal")
        suite = params.get("suite", "trixie")
        if mode not in ("persistent", "ephemeral") or profile not in PROFILES or suite not in ("trixie", "bookworm"):
            raise ValueError("Unsupported lifecycle, profile or Debian release.")
        args += [size(params.get("size_mib")), mode, profile]
        env["CHROOTABLE_DEBIAN_SUITE"] = suite
    elif action in ("clone", "snapshot", "restore", "drop-snapshot"):
        args.append(name(params.get("target")))
    elif action == "resize":
        args.append(size(params.get("size_mib")))
    elif action == "mode":
        if params.get("lifecycle") not in ("persistent", "ephemeral"):
            raise ValueError("Choose persistent or ephemeral.")
        args.append(params["lifecycle"])
    if action in ("restore", "drop-snapshot", "repair", "delete"):
        if params.get("confirmed") is not True:
            raise ValueError("This operation requires confirmation.")
        args.append("--yes")
    return args, env


def read_regular(path, limit=256 * 1024, offset=None):
    """Read bounded regular files without following a final symlink."""
    return _read_regular_chunk(path, limit, offset)[0]


def _read_regular_chunk(path, limit=256 * 1024, offset=None):
    """Return bounded text and the byte position actually consumed."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Expected a regular file.")
        start = max(0, info.st_size - limit)
        if offset is not None and 0 <= offset <= info.st_size:
            start = max(start, offset)
        stream.seek(start)
        data = stream.read(max(0, min(limit, info.st_size - start)))
        return data.decode("utf-8", errors="replace"), stream.tell()


def _probe_lock_status(lock_path):
    try:
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "ready"
    except OSError:
        return "ready" if os.path.islink(lock_path) else "unknown"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "ready"
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "busy"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "ready"
    finally:
        os.close(fd)


def deliver_export(archive, destination):
    """Open the archive as root; create the user's destination after dropping privileges."""
    target = Path(destination)
    if not target.is_absolute():
        raise ValueError("Export destination must be an absolute path.")
    uid = int(os.environ.get("PKEXEC_UID", os.getuid()))
    if uid == 0:
        raise ValueError("Desktop exports need the invoking user's identity.")
    account = pwd.getpwuid(uid)
    source = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
    script = '''import os, shutil, sys
source, destination = int(sys.argv[1]), sys.argv[2]
created = False
try:
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    created = True
    with os.fdopen(fd, "wb") as output, os.fdopen(source, "rb") as archive:
        try:
            while True:
                if os.copy_file_range(archive.fileno(), output.fileno(), 1 << 30) == 0:
                    break
        except (AttributeError, OSError):
            shutil.copyfileobj(archive, output, 1024 * 1024)
except BaseException:
    if created:
        os.unlink(destination)
    raise
'''
    try:
        options = {"user": uid, "group": account.pw_gid, "extra_groups": []} if os.geteuid() == 0 else {}
        result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-c", script, str(source), str(target)],
                                pass_fds=(source,), capture_output=True, text=True, **options)
        if result.returncode:
            raise RuntimeError("Archive created, but saving the chosen copy failed: " + result.stderr.strip())
    finally:
        os.close(source)


def metadata(path):
    try:
        return dict(line.split("=", 1) for line in read_regular(path).splitlines() if "=" in line)
    except FileNotFoundError:
        return {}


class Store:
    def __init__(self, path=DEFAULT_STORE):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path == Path("/"):
            raise ValueError("Store must be an absolute directory other than /.")
        self._images_sig = None
        self._images_cache = []
        self._snap_meta_cache = {}
        self._activity_sig = None
        self._activity_cache = []
        self._inventory_lock = threading.Lock()

    def environment(self, extra=None):
        result = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8", "HOME": "/root" if os.geteuid() == 0 else str(Path.home()),
            "CHROOTABLE_HOME": str(self.path), "CHROOTABLE_AUTO_INSTALL": "0",
        }
        result.update(extra or {})
        return result

    def inventory(self):
        # Broker reads may overlap; publish each cache signature with its data.
        with self._inventory_lock:
            return self._inventory()

    def _inventory(self):
        # Check directories explicitly: glob can silently conceal permission errors.
        image_dir_entries = None
        if self.path.exists():
            for part in (self.path, self.path / "images", self.path / "snapshots", self.path / "logs"):
                if part.is_symlink():
                    raise ValueError("Store directories must not be symlinks.")
                if part.exists():
                    with os.scandir(part) as entries:
                        if part.name == "images":
                            image_dir_entries = []
                            for entry in entries:
                                if entry.is_symlink():
                                    image_dir_entries.append((entry.name, True, 0, 0, 0, 0))
                                elif entry.name.endswith((".img", ".meta", ".mode")) and entry.is_file(follow_symlinks=False):
                                    st = entry.stat(follow_symlinks=False)
                                    image_dir_entries.append((entry.name, False, st.st_ino, st.st_mtime_ns, st.st_size, st.st_blocks))
                            image_dir_entries.sort()
        snapshots_root = self.path / "snapshots"
        raw_snapshots = []
        snap_counts = {}
        if snapshots_root.is_dir() and not snapshots_root.is_symlink():
            with os.scandir(snapshots_root) as folder_entries:
                folders = sorted(
                    (e for e in folder_entries if not e.is_symlink() and e.is_dir(follow_symlinks=False) and NAME_RE.fullmatch(e.name)),
                    key=lambda e: e.name,
                )
            for folder in folders:
                imgs = []
                meta_stats = {}
                with os.scandir(folder.path) as img_entries:
                    for entry in img_entries:
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        if entry.name.endswith(".img") and NAME_RE.fullmatch(entry.name[:-4]):
                            imgs.append(entry)
                        elif entry.name.endswith(".img.meta"):
                            meta_stats[entry.name] = entry.stat(follow_symlinks=False)
                imgs.sort(key=lambda e: e.name)
                snap_counts[folder.name] = len(imgs)
                for image in imgs:
                    img_st = image.stat(follow_symlinks=False)
                    meta_st = meta_stats.get(image.name + ".meta")
                    cache_key = (image.path, meta_st.st_ino, meta_st.st_mtime_ns, meta_st.st_size) if meta_st else None
                    if cache_key is not None and cache_key in self._snap_meta_cache:
                        created_at = self._snap_meta_cache[cache_key]
                    else:
                        meta = metadata(Path(image.path + ".meta")) if meta_st else {}
                        created_at = meta.get("snapshot_at", "unknown")
                        if cache_key is not None:
                            if len(self._snap_meta_cache) >= 2048:
                                self._snap_meta_cache.clear()
                            self._snap_meta_cache[cache_key] = created_at
                    raw_snapshots.append((folder.name, image.name[:-4], img_st.st_size // 1048576, created_at))
        images_sig = (tuple(image_dir_entries), tuple(sorted(snap_counts.items()))) if image_dir_entries is not None else None
        if self._images_sig is not None and images_sig is not None and images_sig == self._images_sig:
            images = []
            images_root = self.path / "images"
            for item in self._images_cache:
                status = _probe_lock_status(images_root / (item["name"] + ".img.lock"))
                images.append(item if item["status"] == status else {**item, "status": status})
        else:
            result = subprocess.run([str(MANAGER), "--list", "--json"], env=self.environment(), capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise PermissionError(result.stderr.strip() or "Cannot read the workspace store.")
            images = json.loads(result.stdout)
            self._images_sig = images_sig
            self._images_cache = images
        image_names = {x["name"] for x in images}
        snapshots = [
            {"name": ws_name, "label": label, "size_mib": size_mib, "created_at": created_at, "orphan": ws_name not in image_names}
            for ws_name, label, size_mib, created_at in raw_snapshots
        ]
        activity_path = self.path / "activity.tsv"
        try:
            act_st = activity_path.lstat()
            act_sig = (act_st.st_ino, act_st.st_mtime_ns, act_st.st_size)
            if act_sig == self._activity_sig:
                activity = self._activity_cache
            else:
                parsed = []
                for line in read_regular(activity_path).splitlines()[-100:]:
                    fields = line.split("\t")
                    if len(fields) == 5:
                        parsed.append(dict(zip(("time", "action", "name", "status", "log"), fields)))
                activity = list(reversed(parsed))
                self._activity_sig = act_sig
                self._activity_cache = activity
        except FileNotFoundError:
            self._activity_sig = None
            self._activity_cache = []
            activity = []
        existing = self.path
        while not existing.exists():
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        return {"images": images, "snapshots": snapshots, "activity": activity,
                "store": str(self.path), "free_bytes": usage.free, "total_bytes": usage.total}

    def logs(self, target, selected_log=None):
        name(target)
        if selected_log:
            path = Path(selected_log)
            if path.parent != self.path / "logs":
                raise ValueError("Log is outside this workspace store.")
            target_path = str(path)
            if not any(
                len(fields := line.split("\t")) == 5 and fields[2] == target and fields[4] == target_path
                for line in read_regular(self.path / "activity.tsv").splitlines()
            ):
                raise ValueError("Log does not belong to the selected workspace operation.")
            return "Log: " + target_path + "\n\n" + read_regular(path)
        result = subprocess.run([str(MANAGER), "--logs", name(target)], env=self.environment(), capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def doctor(self):
        result = subprocess.run([str(MANAGER), "--doctor", "--json"], env=self.environment(), capture_output=True, text=True, timeout=15)
        try:
            report = json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise RuntimeError(result.stderr.strip() or "Host health returned an invalid report.") from error
        if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
                or report["schema_version"] != 1
                or type(report.get("ready")) is not bool
                or not all(isinstance(report.get(key), str)
                           for key in ("output", "host", "architecture", "version", "store"))
                or not all(type(report.get(key)) is int and report[key] >= 0
                           for key in ("errors", "warnings"))
                or not isinstance(report.get("checks"), list)
                or result.returncode not in (0, 1)
                or report["ready"] != (result.returncode == 0)):
            raise RuntimeError("Host health returned an inconsistent report.")
        statuses = {"ok", "info", "warning", "error"}
        seen = set()
        for check in report["checks"]:
            if (not isinstance(check, dict)
                    or not all(isinstance(check.get(key), str)
                               for key in ("id", "status", "message", "remedy"))
                    or check["status"] not in statuses or not check["id"]
                    or check["id"] in seen):
                raise RuntimeError("Host health returned an invalid check.")
            seen.add(check["id"])
        if (report["errors"] != sum(check["status"] == "error" for check in report["checks"])
                or report["warnings"] != sum(check["status"] == "warning" for check in report["checks"])
                or report["ready"] != (report["errors"] == 0)):
            raise RuntimeError("Host health returned inconsistent issue counts.")
        storage = report.get("storage")
        if (not isinstance(storage, dict) or type(storage.get("available")) is not bool
                or not all(type(storage.get(key)) is int and storage[key] >= 0
                           for key in ("free_bytes", "total_bytes"))
                or storage["free_bytes"] > storage["total_bytes"]):
            raise RuntimeError("Host health returned invalid storage capacity.")
        return report

    def operate(self, action, params, output):
        args, extra = build_command(action, params)
        env = self.environment(extra)
        started = time.time()
        # A reader thread drains the child while the main thread tails its operation log.
        process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        archives = []
        def drain():
            for line in process.stdout:
                candidate = Path(line.strip())
                if action == "export" and candidate.parent == self.path / "exports" and candidate.suffixes[-2:] == [".tar", ".gz"]:
                    archives.append(candidate)
                output(line)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        offsets = {}
        stats = {}
        logs_dir = self.path / "logs"
        def tail_file(log_path, info):
            if not stat.S_ISREG(info.st_mode) or info.st_mtime < started:
                return
            sig = (info.st_mtime_ns, info.st_size)
            if stats.get(log_path) == sig:
                return
            prev_offset = offsets.get(log_path, 0)
            if info.st_size < prev_offset:
                prev_offset = 0
            chunk, offsets[log_path] = _read_regular_chunk(log_path, offset=prev_offset)
            stats[log_path] = sig
            if chunk:
                output(chunk)
        def tail():
            if logs_dir.is_dir() and not logs_dir.is_symlink():
                try:
                    with os.scandir(logs_dir) as entries:
                        for entry in entries:
                            if entry.name.endswith(".log") and not entry.is_symlink():
                                try:
                                    tail_file(entry.path, entry.stat(follow_symlinks=False))
                                except (FileNotFoundError, OSError):
                                    continue
                except OSError:
                    pass
            if action == "setup":
                dep_log = "/var/log/chrootable-manager-dependencies.log"
                try:
                    tail_file(dep_log, os.lstat(dep_log))
                except (FileNotFoundError, OSError):
                    pass
        while process.poll() is None:
            tail()
            time.sleep(0.2)
        reader.join()
        process.stdout.close()
        tail()
        if process.returncode == 0 and action == "export" and params.get("destination"):
            if len(archives) != 1:
                raise RuntimeError("Could not identify the completed export archive.")
            deliver_export(archives[0], params["destination"])
            output("Saved export: " + params["destination"] + "\n")
        return process.returncode


def shared_folder(value):
    """Validate and resolve one host directory before handing it to sudo."""
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ValueError("Choose an existing host folder with an absolute path.")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Shared folder must be an absolute path.")
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Shared folder must be an existing directory.")
    except (OSError, RuntimeError) as error:
        raise ValueError("Shared folder is unavailable. Choose an existing host folder.") from error
    if path == Path("/"):
        raise ValueError("Choose a folder other than the host filesystem root.")
    if any(c in str(path) for c in "\r\n"):
        raise ValueError("Shared folder paths cannot contain line breaks.")
    return str(path)


def session_command(target, store=DEFAULT_STORE, *, disposable=False, isolated=False,
                    share=None, read_only=False, restricted_dev=False):
    """The validated sudo argv that starts a workspace session."""
    target = name(target)
    if not isinstance(read_only, bool) or not isinstance(restricted_dev, bool):
        raise ValueError("Read-only and restricted-devices must be true or false.")
    if read_only and share is None:
        raise ValueError("Choose a shared folder before enabling read-only access.")
    action = "--try" if disposable else "--enter-isolated" if isolated else "--enter"
    command = ["sudo", "/usr/bin/env", "CHROOTABLE_AUTO_INSTALL=0", "CHROOTABLE_HOME=" + str(store), str(MANAGER), action, target]
    if disposable and isolated:
        command.append("--isolated")
    if restricted_dev:
        command.append("--restricted-dev")
    if share is not None:
        command += ["--share", shared_folder(share)]
        if read_only:
            command.append("--read-only")
    return command


def terminal_command(target, store=DEFAULT_STORE, *, disposable=False, isolated=False,
                     share=None, read_only=False, restricted_dev=False):
    command = session_command(target, store, disposable=disposable, isolated=isolated,
                              share=share, read_only=read_only, restricted_dev=restricted_dev)
    if shutil.which("konsole"):
        return ["konsole", "--separate", "--hold", "-p", "tabtitle=" + target, "-e"] + command
    if shutil.which("gnome-terminal"):
        return ["gnome-terminal", "--wait", "--"] + command
    if shutil.which("xterm"):
        return ["xterm", "-hold", "-T", "Chrootables · " + target, "-e"] + command
    raise RuntimeError("Install Konsole, GNOME Terminal or xterm to open workspace shells.")
