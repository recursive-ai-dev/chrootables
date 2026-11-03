#!/usr/bin/env python3
"""Check an extracted AppImage using its bundled interpreter, never host Python."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile


def check(stage, *, export=False):
    application = stage / "usr/lib/chrootables"
    runtime = application / "runtime"
    if not Path(sys.executable).resolve().is_relative_to(runtime.resolve()):
        raise RuntimeError("Run this checker with the bundled runtime/bin/python3 -I -B.")
    if not sys.flags.isolated or not sys.flags.dont_write_bytecode:
        raise RuntimeError("The bundled runtime must run in isolated mode without bytecode writes (-I -B).")
    import PySide6
    from PySide6 import QtCore, QtGui, QtWidgets
    for module in (PySide6, QtCore, QtGui, QtWidgets):
        if not Path(module.__file__).resolve().is_relative_to(runtime.resolve()):
            raise RuntimeError("A Qt module escaped the bundled runtime.")
    for library in sorted((runtime / "lib/python3.13/site-packages").rglob("*.so*")):
        result = subprocess.run(["ldd", str(library)], capture_output=True, text=True, check=True)
        if "not found" in result.stdout:
            raise RuntimeError(f"Missing graphics dependency for {library}:\n{result.stdout}")
        for line in result.stdout.splitlines():
            if line.strip().startswith(("libQt6", "libpyside", "libshiboken", "libicu")) and "=>" in line:
                resolved = line.split("=>", 1)[1].strip().split(" (", 1)[0]
                if not Path(resolved).resolve().is_relative_to(runtime.resolve()):
                    raise RuntimeError(f"A Qt dependency escaped the bundled runtime: {line.strip()}")
    with tempfile.TemporaryDirectory(prefix="chrootables-portable-check-") as temporary:
        work = Path(temporary)
        injected = work / "sitecustomize.py"
        marker = work / "injected"
        injected.write_text(f"open({str(marker)!r}, 'w').write('unsafe')\n")
        environment = {**os.environ, "PYTHONPATH": str(work), "PYTHONHOME": str(work)}
        result = subprocess.run([sys.executable, "-I", "-S", "-B", str(application / "chrootables/broker.py"),
                                 "--store", str(work / "store")], env=environment,
                                input='{"id":1,"action":"inventory"}\n', capture_output=True,
                                text=True, check=True, timeout=30)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        if not replies[-1]["ok"] or replies[-1]["data"]["images"] or marker.exists():
            raise RuntimeError("Bundled helper isolation/inventory failed.")
        if export:
            sys.path.insert(0, str(application))
            from chrootables.backend import deliver_export
            source = work / "source.tar.gz"
            source.write_bytes(b"portable export payload")
            target = work / "exports"
            target.mkdir()
            if os.geteuid() == 0:
                account = pwd.getpwnam("nobody")
                work.chmod(0o755)
                os.chown(target, account.pw_uid, account.pw_gid)
                os.environ["PKEXEC_UID"] = str(account.pw_uid)
            deliver_export(source, target / "saved.tar.gz")
            if (target / "saved.tar.gz").read_bytes() != source.read_bytes():
                raise RuntimeError("Bundled export writer did not preserve data.")
            if list(target.glob(".chrootables-export-*")):
                raise RuntimeError("Bundled export writer leaked a staged file.")
    cache = next((path for path in stage.rglob("*")
                  if path.name == "__pycache__" or path.suffix == ".pyc"), None)
    if cache is not None:
        raise RuntimeError(f"Bundled helper/export wrote bytecode into the distribution: {cache}")
    extra = ", export" if export else ""
    print(f"Bundled Python {sys.version.split()[0]}, Qt {PySide6.__version__}: isolation, dependencies, helper{extra} passed.")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve(), export="--export" in sys.argv[2:])
