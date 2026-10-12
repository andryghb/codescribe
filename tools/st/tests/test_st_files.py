# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Tests for the plaintext side of the ST-only mode: file names, formats and
the comparison that decides whether a file differs from the project.

    python tools/st/tests/test_st_files.py
"""

from __future__ import print_function, unicode_literals

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(REPO, "src"))

import st_files  # noqa: E402

failures = []


def check(name, condition, detail=""):
    if condition:
        print("OK      " + name)
        return
    failures.append(name)
    print("FAIL    " + name + ((": " + detail) if detail else ""))


def check_equal(name, actual, expected):
    check(name, actual == expected, "expected %r, got %r" % (expected, actual))


def check_raises(name, fn, *args):
    try:
        fn(*args)
    except ValueError:
        print("OK      " + name)
        return
    failures.append(name)
    print("FAIL    " + name + ": no ValueError")


# --- file names -------------------------------------------------------------

check_equal(
    "plain name is an object", st_files.classify_file_name("FB_Motor.st"), (st_files.KIND_OBJECT, None, "FB_Motor")
)
check_equal("gvl suffix", st_files.classify_file_name("GVL.gvl.st"), (st_files.KIND_GVL, None, "GVL"))
check_equal("method", st_files.classify_file_name("FB_Motor.Start.st"), (st_files.KIND_METHOD, "FB_Motor", "Start"))
check_equal(
    "action", st_files.classify_file_name("FB_Motor.Reset.action.st"), (st_files.KIND_ACTION, "FB_Motor", "Reset")
)
check_equal(
    "transition",
    st_files.classify_file_name("FB_Motor.Done.transition.st"),
    (st_files.KIND_TRANSITION, "FB_Motor", "Done"),
)
check_equal(
    "property",
    st_files.classify_file_name("FB_Motor.Speed.property.st"),
    (st_files.KIND_PROPERTY, "FB_Motor", "Speed"),
)
check_raises("not a .st file", st_files.classify_file_name, "FB_Motor.xml")
check_raises("too many dots", st_files.classify_file_name, "A.B.C.st")
check_raises("member suffix without a parent", st_files.classify_file_name, "Reset.action.st")
check_raises("empty name", st_files.classify_file_name, ".st")

check_equal("export and import agree on the method name", st_files.method_file_name("A", "B"), "A.B.st")

check_equal("one device exports into the folder itself", st_files.device_export_folder("root", "PLC", True), "root")
check_equal(
    "several devices export into a folder each",
    st_files.device_export_folder("root", "PLC", False),
    os.path.join("root", "PLC"),
)
check_equal("import of the flat layout", st_files.device_import_folder("nowhere", "PLC", True), ("nowhere", False))
check_equal("visualisation name", st_files.visualisation_file_name("Visu"), "Visu.vis.xml")

# --- POU or DUT -------------------------------------------------------------

check_equal("program", st_files.detect_object_kind("PROGRAM Main\nVAR\nEND_VAR"), st_files.KIND_POU)
check_equal(
    "function block after a pragma",
    st_files.detect_object_kind("{attribute 'x'}\nFUNCTION_BLOCK FB\n"),
    st_files.KIND_POU,
)
check_equal(
    "function after a comment line", st_files.detect_object_kind("(* adds *)\nFUNCTION F : INT\n"), st_files.KIND_POU
)
check_equal("struct", st_files.detect_object_kind("TYPE ST_Point :\nSTRUCT\nEND_STRUCT\nEND_TYPE"), st_files.KIND_DUT)
check_equal("lower case keyword", st_files.detect_object_kind("type ST_Point :\n"), st_files.KIND_DUT)
check_equal("interface", st_files.detect_object_kind("INTERFACE IFoo\n"), st_files.KIND_INTERFACE)
check_equal("no keyword", st_files.detect_object_kind("VAR\nEND_VAR\n"), None)

# --- declaration / implementation ------------------------------------------

declaration, implementation = st_files.split_declaration_implementation(
    "PROGRAM Main\nVAR\nEND_VAR\n" + st_files.IMPLEMENTATION_DELIMITER_INSERT + "x := 1;\n"
)
check_equal("split declaration", declaration, "PROGRAM Main\nVAR\nEND_VAR\n\n")
check_equal("split implementation", implementation, "\n\nx := 1;\n")
check_raises("missing delimiter", st_files.split_declaration_implementation, "PROGRAM Main\n")
check_raises(
    "two delimiters",
    st_files.split_declaration_implementation,
    "a" + st_files.IMPLEMENTATION_DELIMITER + "b" + st_files.IMPLEMENTATION_DELIMITER + "c",
)

# --- comparison ---------------------------------------------------------------

check("CRLF is not a change", st_files.same_text("a\r\nb\r\n", "a\nb"))
check("trailing whitespace at the end is not a change", st_files.same_text("a\nb\n\n\t ", "a\nb"))
check("a BOM is not a change", st_files.same_text("\ufeffa\nb", "a\nb"))
check("None reads as empty", st_files.same_text(None, "  \n"))
check("content is a change", not st_files.same_text("a\nb", "a\nc"))
check("whitespace inside is a change", not st_files.same_text("a\n\nb", "a\nb"))
check_equal("imported text is stripped with one newline", st_files.imported_text("\r\n x := 1;\r\n\r\n"), "x := 1;\n")

# --- property files -----------------------------------------------------------

accessors = [("Get", "VAR\nEND_VAR\n", "Speed := x;\n"), ("Set", "VAR\nEND_VAR\n", "x := Speed;\n")]
text = st_files.format_property_file("PROPERTY PUBLIC Speed : INT\n", accessors)
check_equal(
    "property file layout",
    text,
    "PROPERTY PUBLIC Speed : INT\n"
    "\n// --- BEGIN Get ---\n"
    "VAR\nEND_VAR\n"
    "\n// --- BEGIN IMPLEMENTATION ---\n\n"
    "Speed := x;\n"
    "\n// --- BEGIN Set ---\n"
    "VAR\nEND_VAR\n"
    "\n// --- BEGIN IMPLEMENTATION ---\n\n"
    "x := Speed;\n",
)
parsed_declaration, parsed_accessors = st_files.parse_property_file(text)
check("parsed declaration", st_files.same_text(parsed_declaration, "PROPERTY PUBLIC Speed : INT"))
check_equal("parsed accessor names", [a[0] for a in parsed_accessors], ["Get", "Set"])
check("parsed Get declaration", st_files.same_text(parsed_accessors[0][1], "VAR\nEND_VAR"))
check("parsed Get implementation", st_files.same_text(parsed_accessors[0][2], "Speed := x;"))
check("parsed Set implementation", st_files.same_text(parsed_accessors[1][2], "x := Speed;"))

parsed_declaration, parsed_accessors = st_files.parse_property_file(
    "PROPERTY P : INT\r\n// --- BEGIN Get ---\r\nVAR\r\nEND_VAR\r\n// --- BEGIN IMPLEMENTATION ---\r\nP := 1;\r\n"
)
check_equal("CRLF property file parses", [a[0] for a in parsed_accessors], ["Get"])
check("CRLF property Get implementation", st_files.same_text(parsed_accessors[0][2], "P := 1;"))

parsed_declaration, parsed_accessors = st_files.parse_property_file("PROPERTY P : INT\n")
check_equal("property without accessors", parsed_accessors, [])
check_raises(
    "accessor without delimiter", st_files.parse_property_file, "PROPERTY P : INT\n// --- BEGIN Get ---\nVAR\nEND_VAR\n"
)
check_raises(
    "delimiter before any accessor", st_files.parse_property_file, "PROPERTY P : INT\n// --- BEGIN IMPLEMENTATION ---\n"
)

check_equal("return type", st_files.property_return_type("PROPERTY PUBLIC Speed : INT"), "INT")
check_equal(
    "return type after a pragma, before a comment",
    st_files.property_return_type(
        "{attribute 'monitoring' := 'variable'}\nPROPERTY Ref : REFERENCE TO ST_Foo // the ref"
    ),
    "REFERENCE TO ST_Foo",
)
check_equal(
    "array return type with a block comment",
    st_files.property_return_type("PROPERTY Values : ARRAY[0..3] OF INT (* four *)"),
    "ARRAY[0..3] OF INT",
)
check_equal("no colon", st_files.property_return_type("PROPERTY Broken"), None)
check_equal("no property line", st_files.property_return_type("VAR\nEND_VAR"), None)
check_equal(
    "commented out property line is not the header",
    st_files.property_return_type("(* PROPERTY X : INT *)\nPROPERTY Y : BOOL"),
    "BOOL",
)

print()
if failures:
    print("%d failure(s): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all passed")
