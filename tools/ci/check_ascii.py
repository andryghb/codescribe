"""Fail if any Python file in src/ or tools/ contains a non-ASCII byte.

CODESYS ScriptEngine is IronPython 2.7, which enforces PEP 263: source files
must be pure ASCII unless an encoding is declared. A stray em-dash or smart
quote in a comment makes CODESYS refuse to load the file at all. Python 3
py_compile cannot catch this because Python 3 source defaults to UTF-8.

The test suites under tools/ run under IronPython in CI as well, so they
are held to the same rule - and this check runs on Linux, long before the
Windows job would find out.

Runs under Python 3 (CI) or Python 2 - byte scanning only, no imports.
"""

import os
import sys

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
ROOTS = ("src", "tools")


def python_files():
    for root in ROOTS:
        for dir_path, _dir_names, file_names in os.walk(os.path.join(REPO, root)):
            for name in sorted(file_names):
                if name.endswith(".py"):
                    yield os.path.join(dir_path, name)


failures = []
for path in sorted(python_files()):
    name = os.path.relpath(path, REPO)
    with open(path, "rb") as f:
        data = f.read()
    line = 1
    for i in range(len(data)):
        b = data[i] if isinstance(data[i], int) else ord(data[i])
        if b == 0x0A:
            line += 1
        elif b > 0x7F:
            failures.append("%s:%d: non-ASCII byte 0x%02X" % (name, line, b))

for failure in failures:
    print("FAIL    " + failure)
if not failures:
    print("OK      all Python files under src/ and tools/ are pure ASCII")
sys.exit(1 if failures else 0)
