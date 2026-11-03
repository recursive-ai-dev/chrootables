#!/usr/bin/env python3
"""Explicitly download checksum-pinned portable Python/Qt and matching sources."""
import argparse
from pathlib import Path
import platform
from appimage_runtime import fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--architecture", choices=("x86_64", "aarch64"), default=platform.machine())
    args = parser.parse_args()
    try:
        fetch(args.output.resolve(), args.architecture)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Runtime download failed: {error}\n")


if __name__ == "__main__":
    main()
