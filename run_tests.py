"""Run portable public regressions once per unique test identity.

No downloaded mod JARs or installed Minecraft instances are required. Synthetic
fixtures and output writes use temporary folders. Tk tests require a desktop.
"""
from __future__ import annotations
import argparse
import importlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
GUI_MODULES = {"moddb.test_offline_ui", "moddb.test_art_library_ui",
               "moddb.test_art_animation_preview", "moddb.test_epic_controls"}


def _cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _cases(test)
        else:
            yield test


def suite(include_gui: bool = True) -> unittest.TestSuite:
    modules = [p.stem for p in sorted(ROOT.glob("test_*.py"))]
    modules += ["moddb." + p.stem for p in sorted((ROOT / "moddb").glob("test_*.py"))]
    unique = {}
    loader = unittest.defaultTestLoader
    for name in modules:
        if not include_gui and name in GUI_MODULES:
            continue
        for case in _cases(loader.loadTestsFromModule(importlib.import_module(name))):
            # Fixture imports can expose TestCase classes twice. Count by ID.
            if not include_gui and type(case).__module__ in GUI_MODULES:
                continue
            unique.setdefault(case.id(), case)
    return unittest.TestSuite(unique[key] for key in sorted(unique))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-gui", action="store_true", help="skip tests that create Tk windows")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    build_dir = ROOT / "build"
    build_dir.mkdir(exist_ok=True)
    # Artwork export tests write full resource packs. Keep their temporary
    # space on the checkout's drive, including child-process temporary files.
    with tempfile.TemporaryDirectory(prefix="public-tests-", dir=build_dir) as temporary, \
            patch.object(tempfile, "tempdir", temporary), \
            patch.dict(os.environ, {"TEMP": temporary, "TMP": temporary, "TMPDIR": temporary}), \
            patch.object(socket.socket, "connect", side_effect=AssertionError("Tests must stay offline")), \
            patch.object(socket, "create_connection", side_effect=AssertionError("Tests must stay offline")):
        tests = suite(not args.no_gui)
        print(f"Running {tests.countTestCases()} unique public tests", flush=True)
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(tests)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
