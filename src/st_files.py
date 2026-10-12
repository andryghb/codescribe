# REMEMBER: this is python 2.7
"""The plaintext side of the ST-only mode: file names, on-disk formats and the
text comparison the import uses to decide whether a file differs from the
object in the project.

No scriptengine dependency, so it runs under CPython for the tests as well as
under the IronPython that CODESYS embeds.
"""
from __future__ import print_function

import os
import re

ST_EXTENSION = ".st"
GVL_SUFFIX = ".gvl"
ACTION_SUFFIX = ".action"
TRANSITION_SUFFIX = ".transition"
PROPERTY_SUFFIX = ".property"
VISUALISATION_EXTENSION = ".vis.xml"

# unicode literals so they can be written to / split out of the UTF-8 text streams
IMPLEMENTATION_DELIMITER = u"// --- BEGIN IMPLEMENTATION ---"
IMPLEMENTATION_DELIMITER_INSERT = u"\n" + IMPLEMENTATION_DELIMITER + u"\n\n"

# Kinds a .st file can describe. KIND_OBJECT is a plain <name>.st, which is a
# POU or a DUT depending on its first keyword (detect_object_kind).
KIND_OBJECT = "object"
KIND_POU = "pou"
KIND_DUT = "dut"
KIND_GVL = "gvl"
KIND_METHOD = "method"
KIND_ACTION = "action"
KIND_TRANSITION = "transition"
KIND_PROPERTY = "property"
KIND_INTERFACE = "interface"

_SECTION_MARKER = re.compile(r"^// --- BEGIN (.+?) ---\s*$")
_PROPERTY_HEADER = re.compile(r"^\s*PROPERTY\b(.*)$", re.IGNORECASE)
_COMMENT_START = re.compile(r"//|\(\*")


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------


def pou_file_name(name):
    return name + ST_EXTENSION


def dut_file_name(name):
    return name + ST_EXTENSION


def gvl_file_name(name):
    return name + GVL_SUFFIX + ST_EXTENSION


def method_file_name(pou_name, name):
    return pou_name + "." + name + ST_EXTENSION


def action_file_name(pou_name, name):
    return pou_name + "." + name + ACTION_SUFFIX + ST_EXTENSION


def transition_file_name(pou_name, name):
    return pou_name + "." + name + TRANSITION_SUFFIX + ST_EXTENSION


def property_file_name(pou_name, name):
    return pou_name + "." + name + PROPERTY_SUFFIX + ST_EXTENSION


def visualisation_file_name(name):
    return name + VISUALISATION_EXTENSION


def is_st_file_name(file_name):
    return file_name.endswith(ST_EXTENSION)


# ---------------------------------------------------------------------------
# Where a device's application lives in the export folder
# ---------------------------------------------------------------------------
#
# The ST-only export holds the application's contents directly: a project
# with one device writes them into the export folder itself, one with several
# devices writes one folder per device, as two applications cannot share a
# folder. Earlier exports (and full mode) nest them under
# <device>/application; the import still reads that layout, so a checkout
# made before the change imports as it is and the next export rewrites it.

LEGACY_APPLICATION_FOLDER = "application"


def device_export_folder(root, device_name, single_device):
    if single_device:
        return root
    return os.path.join(root, device_name)


def device_import_folder(root, device_name, single_device):
    """(folder holding the device's application, whether it is the legacy layout)"""
    legacy = os.path.join(root, device_name, LEGACY_APPLICATION_FOLDER)
    if os.path.isdir(legacy):
        return legacy, True
    return device_export_folder(root, device_name, single_device), False


def classify_file_name(file_name):
    """(kind, parent POU name or None, object name) from the file name alone.

    Mirrors the dispatch of the full-mode importer: ``<name>.gvl.st`` is a
    GVL, ``<pou>.<name>.action.st`` / ``.transition.st`` / ``.property.st``
    are members with the kind in the suffix, any other ``<pou>.<name>.st`` is
    a method, and a bare ``<name>.st`` is a POU or a DUT (KIND_OBJECT; the
    content decides). Raises ValueError for a name this export never writes.
    """
    if not is_st_file_name(file_name):
        raise ValueError("not a .st file: " + file_name)
    stem = file_name[: -len(ST_EXTENSION)]

    if stem.endswith(GVL_SUFFIX):
        name = stem[: -len(GVL_SUFFIX)]
        if not name or "." in name:
            raise ValueError("unrecognised GVL file name: " + file_name)
        return KIND_GVL, None, name

    for suffix, kind in (
        (ACTION_SUFFIX, KIND_ACTION),
        (TRANSITION_SUFFIX, KIND_TRANSITION),
        (PROPERTY_SUFFIX, KIND_PROPERTY),
    ):
        if stem.endswith(suffix):
            parts = stem[: -len(suffix)].split(".")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError("expected <pou>.<name>" + suffix + ST_EXTENSION + ", got: " + file_name)
            return kind, parts[0], parts[1]

    parts = stem.split(".")
    if len(parts) == 1 and parts[0]:
        return KIND_OBJECT, None, parts[0]
    if len(parts) == 2 and parts[0] and parts[1]:
        return KIND_METHOD, parts[0], parts[1]
    raise ValueError("unrecognised file name: " + file_name)


# ---------------------------------------------------------------------------
# Text comparison
# ---------------------------------------------------------------------------


def normalize_newlines(text):
    return text.replace(u"\r\n", u"\n").replace(u"\r", u"\n")


def canonical(text):
    """The form two texts are compared in: no BOM, LF newlines, ends stripped.

    The importer has always written ``text.strip() + "\\n"``, and CODESYS and
    git disagree about line endings, so neither of those is a difference in
    the code.
    """
    if text is None:
        text = u""
    if text.startswith(u"\ufeff"):
        text = text[1:]
    return normalize_newlines(text).strip()


def same_text(a, b):
    return canonical(a) == canonical(b)


def imported_text(text):
    """What the importer writes into the object for a given file text."""
    return canonical(text) + u"\n"


# ---------------------------------------------------------------------------
# POU / method files: declaration, delimiter, implementation
# ---------------------------------------------------------------------------


def split_declaration_implementation(content):
    parts = content.split(IMPLEMENTATION_DELIMITER)
    if len(parts) != 2:
        raise ValueError(
            "expected exactly one '"
            + IMPLEMENTATION_DELIMITER
            + "' line separating declaration and implementation, found "
            + str(len(parts) - 1)
        )
    return parts[0], parts[1]


def join_declaration_implementation(declaration, implementation):
    return declaration + IMPLEMENTATION_DELIMITER_INSERT + implementation


def detect_object_kind(content):
    """KIND_POU, KIND_DUT or KIND_INTERFACE from the first keyword line, else None."""
    for line in canonical(content).split(u"\n"):
        words = line.strip().split()
        if not words:
            continue
        word = words[0].upper()
        if word == u"TYPE":
            return KIND_DUT
        if word in (u"PROGRAM", u"FUNCTION_BLOCK", u"FUNCTION"):
            return KIND_POU
        if word == u"INTERFACE":
            return KIND_INTERFACE
    return None


# ---------------------------------------------------------------------------
# Property files
# ---------------------------------------------------------------------------
#
# A property is its declaration line plus one section per accessor (Get, Set),
# each a declaration/implementation pair like a method:
#
#     PROPERTY PUBLIC Value : INT
#     // --- BEGIN Get ---
#     VAR
#     END_VAR
#     // --- BEGIN IMPLEMENTATION ---
#
#     Value := _value;
#     // --- BEGIN Set ---
#     VAR
#     END_VAR
#     // --- BEGIN IMPLEMENTATION ---
#
#     _value := Value;
#
# The section name is the accessor object's name in CODESYS.


def format_property_file(declaration, accessors):
    """accessors: list of (name, declaration, implementation) in project order."""
    out = [declaration]
    for name, accessor_declaration, accessor_implementation in accessors:
        out.append(u"\n// --- BEGIN " + name + u" ---\n")
        out.append(join_declaration_implementation(accessor_declaration, accessor_implementation))
    return u"".join(out)


def parse_property_file(content):
    """(declaration, [(name, declaration, implementation), ...])"""
    header = []
    sections = []
    current = None  # [name, declaration lines, implementation lines, seen delimiter]
    for line in normalize_newlines(content).split(u"\n"):
        match = _SECTION_MARKER.match(line)
        if match:
            name = match.group(1)
            if name == u"IMPLEMENTATION":
                if current is None:
                    raise ValueError("'" + IMPLEMENTATION_DELIMITER + "' before any accessor section")
                if current[3]:
                    raise ValueError("two '" + IMPLEMENTATION_DELIMITER + "' lines in accessor " + current[0])
                current[3] = True
            else:
                current = [name, [], [], False]
                sections.append(current)
            continue
        if current is None:
            header.append(line)
        elif current[3]:
            current[2].append(line)
        else:
            current[1].append(line)

    accessors = []
    for name, declaration_lines, implementation_lines, seen_delimiter in sections:
        if not seen_delimiter:
            raise ValueError("accessor " + name + " has no '" + IMPLEMENTATION_DELIMITER + "' line")
        accessors.append((name, u"\n".join(declaration_lines), u"\n".join(implementation_lines)))
    return u"\n".join(header), accessors


def property_return_type(declaration):
    """The type after the colon on the PROPERTY line, or None if there is none."""
    for line in normalize_newlines(declaration).split(u"\n"):
        match = _PROPERTY_HEADER.match(line)
        if not match:
            continue
        rest = _COMMENT_START.split(match.group(1))[0]
        if u":" not in rest:
            return None
        return_type = rest.split(u":", 1)[1].strip()
        return return_type or None
    return None
