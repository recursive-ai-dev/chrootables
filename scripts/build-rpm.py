#!/usr/bin/python3
"""Build a reproducible Fedora RPM package with installed rpmbuild; no root needed."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_support import (APPLICATION_ID, ROOT, build_metadata, license_identifier,
                             publish_artifact, require_tool, run_tool, source_archive,
                             stage_payload, upstream_version)


def package_spec(version, revision, date, stage):
    docs = stage / "usr/share/doc/chrootables"
    license_files = "\n".join(f"%license /usr/share/doc/chrootables/{name}"
                              for name in ("copyright", "NOTICE") if (docs / name).is_file())
    return f"""%global _buildhost reproducible
%global use_source_date_epoch_as_buildtime 1
%global clamp_mtime_to_source_date_epoch 1
%global _binary_payload w6.xzdio
%global _build_id_links none
%global debug_package %{{nil}}
%global __os_install_post %{{nil}}

Name: chrootables
Version: {version}
Release: {revision}
Summary: Desktop workbench for local Debian image environments
License: {license_identifier()}
BuildArch: noarch
Source0: payload.tar.gz
AutoReqProv: no

Requires: python3 >= 3.10
Requires: python3-pyside6 >= 6.8
Requires: bash, coreutils, util-linux, e2fsprogs
Requires: debootstrap, debian-keyring, ca-certificates, dpkg
Requires: findutils, gnupg2, gzip, hostname, iproute, gawk
Requires: polkit, sudo, tar, wget, newt, xz

%description
Create Debian workspaces, open terminals, use disposable sessions,
and manage snapshots using a native Qt desktop or a Bash interface.

%prep
%setup -q -c -T
%{{__tar}} -xf "%{{SOURCE0}}"

%build

%install
mkdir -p "%{{buildroot}}"
cp -a usr "%{{buildroot}}/"

%files
%defattr(-,root,root,-)
/usr/lib/chrootables
/usr/bin/chrootable-manager
/usr/bin/chrootables-desktop
/usr/share/applications/{APPLICATION_ID}.desktop
/usr/share/icons/hicolor/scalable/apps/{APPLICATION_ID}.svg
%dir /usr/share/doc/chrootables
%doc /usr/share/doc/chrootables/README.md.gz
%doc /usr/share/doc/chrootables/changelog.gz
{license_files}
/usr/share/man/man1/chrootables-desktop.1.gz
/usr/share/man/man1/chrootable-manager.1.gz

%changelog
* {date.strftime('%a %b %d %Y')} Chrootables contributors <chrootables@users.noreply.github.com> - {version}-{revision}
- Package the desktop, helper and CLI with distribution dependencies
"""


def build(output, revision="1", force=False, rpmbuild=None):
    version, epoch, date = build_metadata(revision)
    tool = require_tool("rpmbuild", rpmbuild or os.environ.get("RPMBUILD"),
                        "On Fedora install rpm-build; on Debian/Ubuntu install rpm.")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"chrootables-{version}-{revision}.noarch.rpm"
    with tempfile.TemporaryDirectory(prefix=".chrootables-rpmbuild-", dir=output) as temporary:
        work = Path(temporary)
        for name in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS", "tmp"):
            (work / name).mkdir()
        stage = stage_payload(work / "stage", epoch)
        source_archive(stage, work / "SOURCES/payload.tar.gz", epoch)
        spec = work / "SPECS/chrootables.spec"
        spec.write_text(package_spec(version, revision, date, stage), encoding="utf-8")
        # RPM generates shell commands with unquoted build paths. A short alias
        # lets callers choose an output directory containing spaces, while the
        # real build remains on the output filesystem for atomic publication.
        with tempfile.TemporaryDirectory(prefix="chrootables-rpm-path-", dir="/tmp") as alias:
            rpm_work = Path(alias) / "build"
            rpm_work.symlink_to(work, target_is_directory=True)
            run_tool([tool, "--define", f"_topdir {rpm_work}", "--define", f"_tmppath {rpm_work / 'tmp'}",
                      "--define", f"_dbpath {rpm_work / 'rpmdb'}",
                      "--define", "_buildhost reproducible", "-bb", str(rpm_work / "SPECS/chrootables.spec")],
                     cwd=alias,
                     env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "TZ": "UTC", "LC_ALL": "C"})
        packages = list((work / "RPMS").rglob("*.rpm"))
        if len(packages) != 1:
            raise ValueError(f"Expected one RPM artifact, found {len(packages)}.")
        return publish_artifact(packages[0], package, force=force)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="artifact directory (default: dist)")
    parser.add_argument("--revision", default="1", help="RPM release appended to the application version")
    parser.add_argument("--force", action="store_true", help="replace an existing package whose contents differ")
    parser.add_argument("--rpmbuild", help="rpmbuild executable (default: RPMBUILD or PATH)")
    args = parser.parse_args()
    try:
        print(build(args.output, args.revision, args.force, args.rpmbuild))
    except (ValueError, OSError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
