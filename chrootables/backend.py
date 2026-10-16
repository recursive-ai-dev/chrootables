"""Validated desktop API for the existing image manager. No shell evaluation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
MANAGER = ROOT / "chrootable-manager"
DEFAULT_STORE = Path("/var/lib/chrootable-manager")
PROFILES = {
    "minimal": ("Clean slate", "A minimal Debian system. Make it your own.", 2),
    "build": ("Build studio", "C / C++, make, Git, curl and pkg-config.", 4),
    "python": ("Python lab", "Python 3, virtual environments, pip and Git.", 4),
    "network": ("Network toolkit", "DNS tools, ping, curl and an SSH client.", 2),
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


def read_regular(path, limit=256 * 1024):
    """Read bounded regular files without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular file.")
        stream.seek(max(0, os.fstat(stream.fileno()).st_size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


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
        shutil.copyfileobj(archive, output)
except BaseException:
    if created:
        os.unlink(destination)
    raise
'''
    try:
        options = {"user": uid, "group": account.pw_gid, "extra_groups": []} if os.geteuid() == 0 else {}
        result = subprocess.run(["/usr/bin/python3", "-I", "-c", script, str(source), str(target)],
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

    def environment(self, extra=None):
        result = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8", "HOME": "/root" if os.geteuid() == 0 else str(Path.home()),
            "CHROOTABLE_HOME": str(self.path), "CHROOTABLE_AUTO_INSTALL": "0",
        }
        result.update(extra or {})
        return result

    def inventory(self):
        # Check directories explicitly: glob can silently conceal permission errors.
        if self.path.exists():
            for part in (self.path, self.path / "images", self.path / "snapshots", self.path / "logs"):
                if part.is_symlink():
                    raise ValueError("Store directories must not be symlinks.")
                if part.exists():
                    with os.scandir(part):
                        pass
        result = subprocess.run([str(MANAGER), "--list", "--json"], env=self.environment(), capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise PermissionError(result.stderr.strip() or "Cannot read the workspace store.")
        images = json.loads(result.stdout)
        image_names = {x["name"] for x in images}
        snapshots = []
        snapshots_root = self.path / "snapshots"
        if snapshots_root.is_dir() and not snapshots_root.is_symlink():
            with os.scandir(snapshots_root) as folder_entries:
                folders = sorted(
                    (e for e in folder_entries if not e.is_symlink() and e.is_dir(follow_symlinks=False) and NAME_RE.fullmatch(e.name)),
                    key=lambda e: e.name,
                )
            for folder in folders:
                is_orphan = folder.name not in image_names
                with os.scandir(folder.path) as img_entries:
                    imgs = sorted(
                        (e for e in img_entries if e.name.endswith(".img") and not e.is_symlink() and e.is_file(follow_symlinks=False) and NAME_RE.fullmatch(e.name[:-4])),
                        key=lambda e: e.name,
                    )
                for image in imgs:
                    meta = metadata(Path(image.path + ".meta"))
                    snapshots.append({
                        "name": folder.name,
                        "label": image.name[:-4],
                        "size_mib": image.stat(follow_symlinks=False).st_size // 1048576,
                        "created_at": meta.get("snapshot_at", "unknown"),
                        "orphan": is_orphan,
                    })
        activity = []
        try:
            for line in read_regular(self.path / "activity.tsv").splitlines()[-100:]:
                fields = line.split("\t")
                if len(fields) == 5:
                    activity.append(dict(zip(("time", "action", "name", "status", "log"), fields)))
        except FileNotFoundError:
            pass
        existing = self.path
        while not existing.exists():
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        return {"images": images, "snapshots": snapshots, "activity": list(reversed(activity)),
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
        result = subprocess.run([str(MANAGER), "--doctor"], env=self.environment(), capture_output=True, text=True, timeout=15)
        return {"ready": result.returncode == 0, "output": result.stdout + result.stderr}

    def operate(self, action, params, output):
        args, extra = build_command(action, params)
        env = self.environment(extra)
        started = time.time()
        # A reader thread drains the child while the main thread tails its operation log.
        import threading
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
        def tail():
            candidates = list((self.path / "logs").glob("*.log"))
            if action == "setup":
                candidates.append(Path("/var/log/chrootable-manager-dependencies.log"))
            for log in candidates:
                try:
                    info = log.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_mtime < started:
                        continue
                    sig = (info.st_mtime_ns, info.st_size)
                    if stats.get(log) == sig:
                        continue
                    stats[log] = sig
                    data = read_regular(log)
                    previous = offsets.get(log, "")
                    if data != previous:
                        output(data[len(previous):] if data.startswith(previous) else data)
                        offsets[log] = data
                except (FileNotFoundError, OSError):
                    continue
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


def terminal_command(target, store=DEFAULT_STORE, *, disposable=False, isolated=False,
                     share=None, read_only=False):
    target = name(target)
    if not isinstance(read_only, bool):
        raise ValueError("Read-only must be true or false.")
    if read_only and share is None:
        raise ValueError("Choose a shared folder before enabling read-only access.")
    action = "--try" if disposable else "--enter-isolated" if isolated else "--enter"
    command = ["sudo", "/usr/bin/env", "CHROOTABLE_AUTO_INSTALL=0", "CHROOTABLE_HOME=" + str(store), str(MANAGER), action, target]
    if disposable and isolated:
        command.append("--isolated")
    if share is not None:
        command += ["--share", shared_folder(share)]
        if read_only:
            command.append("--read-only")
    if shutil.which("konsole"):
        return ["konsole", "--separate", "--hold", "-p", "tabtitle=" + target, "-e"] + command
    if shutil.which("gnome-terminal"):
        return ["gnome-terminal", "--wait", "--"] + command
    if shutil.which("xterm"):
        return ["xterm", "-hold", "-T", "Chrootables · " + target, "-e"] + command
    raise RuntimeError("Install Konsole, GNOME Terminal or xterm to open workspace shells.")
