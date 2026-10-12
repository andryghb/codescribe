# REMEMBER: this is python 2.7
"""ST-only import: bring the project in line with the .st files on disk.

Unlike the full-mode import, nothing is removed and recreated wholesale. Each
.st file is compared with the object it describes; an object whose text
already matches is left alone, one that differs has its declaration and/or
implementation replaced in place, and one that does not exist is created.
Files that are not .st are ignored, and objects in the project with no file
on disk are reported, not deleted.

Updating in place is what keeps everything the .st file does not carry -
object GUIDs, network variable list settings, the accessors of a property,
graphical members of a POU - exactly as it was.
"""
from __future__ import print_function

import os
import traceback

import scriptengine  # type: ignore

from object_type import ObjectType, get_object_type
from st_export import has_textual_declaration, has_textual_implementation, property_accessors, property_is_textual
import st_files
from util import open_utf8

ADDED = "added"
UPDATED = "updated"
UNCHANGED = "unchanged"

MAX_REPORT_ITEMS = 8

METHOD_TYPES = (ObjectType.METHOD, ObjectType.METHOD_NORET)
GVL_TYPES = (ObjectType.GVL, ObjectType.GVL_PERSISTENT)


class SyncReport(object):
    def __init__(self):
        self.added = []
        self.updated = []
        self.unchanged = []
        self.not_on_disk = []
        self.failed = []  # (label, traceback text)
        self.ignored = []  # files that are not .st

    def record(self, status, label):
        if status == ADDED:
            self.added.append(label)
        elif status == UPDATED:
            self.updated.append(label)
        else:
            self.unchanged.append(label)

    def fail(self, label, error_text):
        print("FAILED  " + label + "\n" + error_text)
        self.failed.append((label, error_text))

    def has_failures(self):
        return len(self.failed) > 0

    def summary(self, max_items=MAX_REPORT_ITEMS):
        """Short report for the dialog. The full lists go to the message view."""
        lines = []
        lines.append("Added: " + str(len(self.added)))
        lines.extend(_listed(self.added, max_items))
        lines.append("Updated: " + str(len(self.updated)))
        lines.extend(_listed(self.updated, max_items))
        lines.append("Unchanged: " + str(len(self.unchanged)))
        if self.not_on_disk:
            lines.append("In the project but not on disk (left untouched): " + str(len(self.not_on_disk)))
            lines.extend(_listed(self.not_on_disk, max_items))
        if self.failed:
            lines.append("Failed: " + str(len(self.failed)))
            lines.extend(_listed([label + ": " + _last_line(error) for label, error in self.failed], max_items))
        if self.ignored:
            lines.append("Ignored (not .st): " + str(len(self.ignored)))
        return "\n".join(lines)

    def details(self):
        """Everything, for the message view."""
        lines = ["Import report"]
        for title, items in (
            ("Added", self.added),
            ("Updated", self.updated),
            ("Unchanged", self.unchanged),
            ("In the project but not on disk (left untouched)", self.not_on_disk),
            ("Ignored (not .st)", self.ignored),
        ):
            lines.append(title + ": " + str(len(items)))
            lines.extend(["    " + item for item in items])
        lines.append("Failed: " + str(len(self.failed)))
        for label, error in self.failed:
            lines.append("    " + label)
            lines.extend(["        " + line for line in error.strip().splitlines()])
        return "\n".join(lines)

    def show_dialog(self, title):
        print(self.details())
        if self.has_failures():
            scriptengine.system.ui.error(title + " finished with errors.\n\n" + self.summary())
        else:
            scriptengine.system.ui.info(title + " complete.\n\n" + self.summary())


def _listed(items, max_items):
    shown = ["    " + item for item in items[:max_items]]
    if len(items) > max_items:
        shown.append("    ... and " + str(len(items) - max_items) + " more (see the message view)")
    return shown


def _last_line(error_text):
    lines = error_text.strip().splitlines()
    return lines[-1] if lines else error_text


# ---------------------------------------------------------------------------
# Walking the export folder
# ---------------------------------------------------------------------------


def sync_directory(dir_path, parent_obj, label_prefix, report):
    """Apply the .st files under dir_path to the children of parent_obj."""
    # Parents before their members: a POU (one dot in A.st) before its methods
    # (A.B.st), those before A.B.action.st. Directories first, then by name,
    # so the report reads the same on every run.
    entries = sorted(os.listdir(dir_path), key=lambda e: (e.count("."), e.lower()))

    for entry in entries:
        full_path = os.path.join(dir_path, entry)
        label = label_prefix + entry

        if os.path.isdir(full_path):
            try:
                folder_obj, created = _ensure_folder(parent_obj, entry)
            except Exception:
                report.fail(label + "/", traceback.format_exc())
                continue
            if created:
                report.added.append(label + "/")
            sync_directory(full_path, folder_obj, label + "/", report)
            continue

        if not st_files.is_st_file_name(entry):
            report.ignored.append(label)
            continue

        try:
            status = _sync_st_file(entry, dir_path, parent_obj)
        except Exception:
            report.fail(label, traceback.format_exc())
            continue
        report.record(status, label)

    _report_not_on_disk(parent_obj, entries, label_prefix, report)


def _sync_st_file(file_name, dir_path, parent_obj):
    kind, pou_name, name = st_files.classify_file_name(file_name)
    content = _read(os.path.join(dir_path, file_name))

    if kind == st_files.KIND_OBJECT:
        kind = st_files.detect_object_kind(content)
        if kind == st_files.KIND_POU:
            return _sync_pou(parent_obj, name, content)
        if kind == st_files.KIND_DUT:
            return _sync_dut(parent_obj, name, content)
        if kind == st_files.KIND_INTERFACE:
            raise ValueError("interfaces are not supported by the ST-only import")
        raise ValueError("cannot tell a POU from a DUT: no PROGRAM, FUNCTION_BLOCK, FUNCTION or TYPE line")

    if kind == st_files.KIND_GVL:
        return _sync_gvl(parent_obj, name, content)

    pou_obj = _find_child(parent_obj, pou_name, (ObjectType.POU,))
    if pou_obj is None:
        raise ValueError("parent POU '" + pou_name + "' is not in the project")

    if kind == st_files.KIND_METHOD:
        return _sync_method(pou_obj, name, content)
    if kind == st_files.KIND_ACTION:
        return _sync_member_implementation(
            pou_obj,
            name,
            content,
            ObjectType.ACTION,
            lambda: pou_obj.create_action(name, scriptengine.ImplementationLanguages.st),
        )
    if kind == st_files.KIND_TRANSITION:
        return _sync_member_implementation(
            pou_obj,
            name,
            content,
            ObjectType.TRANSITION,
            lambda: pou_obj.create_transition(name, scriptengine.ImplementationLanguages.st),
        )
    if kind == st_files.KIND_PROPERTY:
        return _sync_property(pou_obj, name, content)
    raise ValueError("unsupported file kind " + repr(kind))


def _read(path):
    # Read and close before touching the project: IronPython's io.open holds a
    # .NET file lock, so nothing else may open the file meanwhile.
    with open_utf8(path, "r") as f:
        content = f.read()
    # An editor that saves with a BOM would otherwise hide the first keyword.
    if content.startswith(u"\ufeff"):
        content = content[1:]
    return content


# ---------------------------------------------------------------------------
# One object at a time
# ---------------------------------------------------------------------------


def _sync_pou(parent_obj, name, content):
    declaration, implementation = st_files.split_declaration_implementation(content)
    obj = _find_child(parent_obj, name, (ObjectType.POU,))
    if obj is None:
        _assert_name_free(parent_obj, name)
        obj = parent_obj.create_pou(name)
        _apply(obj.textual_declaration, declaration)
        _apply(obj.textual_implementation, implementation)
        return ADDED
    if not has_textual_implementation(obj):
        raise ValueError("'" + name + "' is a graphical POU in the project; the .st file was not applied")
    changed = _apply(obj.textual_declaration, declaration)
    if _apply(obj.textual_implementation, implementation):
        changed = True
    return UPDATED if changed else UNCHANGED


def _sync_dut(parent_obj, name, content):
    obj = _find_child(parent_obj, name, (ObjectType.DUT,))
    if obj is None:
        _assert_name_free(parent_obj, name)
        obj = parent_obj.create_dut(name)
        _apply(obj.textual_declaration, content)
        return ADDED
    return UPDATED if _apply(obj.textual_declaration, content) else UNCHANGED


def _sync_gvl(parent_obj, name, content):
    # A persistent variable list has its own GUID, so a freshly imported GVL
    # can be either type. One that has to be created becomes a plain GVL; the
    # declaration text decides what it holds.
    obj = _find_child(parent_obj, name, GVL_TYPES)
    if obj is None:
        _assert_name_free(parent_obj, name)
        obj = parent_obj.create_gvl(name)
        _apply(obj.textual_declaration, content)
        return ADDED
    return UPDATED if _apply(obj.textual_declaration, content) else UNCHANGED


def _sync_method(pou_obj, name, content):
    declaration, implementation = st_files.split_declaration_implementation(content)
    obj = _find_child(pou_obj, name, METHOD_TYPES)
    if obj is None:
        _assert_name_free(pou_obj, name)
        # The return type and access come from the declaration replaced below.
        obj = pou_obj.create_method(name)
        _apply(obj.textual_declaration, declaration)
        _apply(obj.textual_implementation, implementation)
        return ADDED
    if not has_textual_implementation(obj):
        raise ValueError("'" + name + "' is a graphical method in the project; the .st file was not applied")
    changed = _apply(obj.textual_declaration, declaration)
    if _apply(obj.textual_implementation, implementation):
        changed = True
    return UPDATED if changed else UNCHANGED


def _sync_member_implementation(pou_obj, name, content, obj_type, create):
    # Actions and transitions: implementation text only.
    obj = _find_child(pou_obj, name, (obj_type,))
    if obj is None:
        _assert_name_free(pou_obj, name)
        obj = create()
        _apply(obj.textual_implementation, content)
        return ADDED
    if not has_textual_implementation(obj):
        raise ValueError("'" + name + "' is graphical in the project; the .st file was not applied")
    return UPDATED if _apply(obj.textual_implementation, content) else UNCHANGED


def _sync_property(pou_obj, name, content):
    declaration, accessors = st_files.parse_property_file(content)
    obj = _find_child(pou_obj, name, (ObjectType.PROPERTY,))
    created = False
    if obj is None:
        _assert_name_free(pou_obj, name)
        return_type = st_files.property_return_type(declaration)
        if return_type is None:
            raise ValueError("no 'PROPERTY " + name + " : <type>' line in the file, so the property cannot be created")
        obj = pou_obj.create_property(name, return_type, scriptengine.ImplementationLanguages.st)
        created = True

    changed = False
    if has_textual_declaration(obj):
        changed = _apply(obj.textual_declaration, declaration)

    wanted = dict((accessor_name, True) for accessor_name, _d, _i in accessors)
    for accessor in list(property_accessors(obj)):
        accessor_name = accessor.get_name()
        if accessor_name in wanted:
            continue
        # The file is the whole property: an accessor it does not list was
        # removed in the source. Only a textual one is removed, as a graphical
        # accessor was never in the file to begin with.
        if not has_textual_implementation(accessor):
            raise ValueError("accessor '" + accessor_name + "' is graphical in the project and not in the file")
        accessor.remove()
        changed = True

    for accessor_name, accessor_declaration, accessor_implementation in accessors:
        accessor = _find_child(obj, accessor_name, (ObjectType.PROPERTY_METHOD,))
        if accessor is None:
            raise ValueError(
                "property '"
                + name
                + "' has no '"
                + accessor_name
                + "' accessor in the project, and the ScriptEngine cannot add one: add it in CODESYS and import again"
            )
        if not has_textual_implementation(accessor):
            raise ValueError(
                "accessor '" + accessor_name + "' is graphical in the project; the .st file was not applied"
            )
        if _apply(accessor.textual_declaration, accessor_declaration):
            changed = True
        if _apply(accessor.textual_implementation, accessor_implementation):
            changed = True

    if created:
        return ADDED
    return UPDATED if changed else UNCHANGED


def _apply(document, text):
    """Replace the document's text unless it already matches. Returns True on change."""
    if st_files.same_text(document.text, text):
        return False
    document.replace(st_files.imported_text(text))
    return True


# ---------------------------------------------------------------------------
# Finding objects
# ---------------------------------------------------------------------------


def _find_child(parent_obj, name, types):
    for obj in parent_obj.find(name):
        if get_object_type(obj) in types:
            return obj
    return None


def _assert_name_free(parent_obj, name):
    # Creating an object over another of the same name fails inside CODESYS
    # with a less useful message, and it is a conflict worth naming: the file
    # says one kind, the project holds another.
    for obj in parent_obj.find(name):
        raise ValueError(
            "'" + name + "' exists in the project as " + get_object_type(obj) + ", which this file does not describe"
        )


def _ensure_folder(parent_obj, name):
    existing = _find_child(parent_obj, name, (ObjectType.FOLDER,))
    if existing is not None:
        return existing, False
    _assert_name_free(parent_obj, name)
    parent_obj.create_folder(name)
    created = _find_child(parent_obj, name, (ObjectType.FOLDER,))
    if created is None:
        raise ValueError("folder '" + name + "' should have been created, but cannot be found")
    return created, True


# ---------------------------------------------------------------------------
# What the export writes for an object, used to spot objects with no file
# and to strip a template
# ---------------------------------------------------------------------------


def is_tracked_member(obj):
    """A member of a POU that the ST-only export writes as its own .st file."""
    obj_type = get_object_type(obj)
    if obj_type in METHOD_TYPES or obj_type in (ObjectType.ACTION, ObjectType.TRANSITION):
        return has_textual_implementation(obj)
    if obj_type == ObjectType.PROPERTY:
        return property_is_textual(obj)
    return False


def member_file_name(pou_name, obj):
    obj_type = get_object_type(obj)
    name = obj.get_name()
    if obj_type in METHOD_TYPES:
        return st_files.method_file_name(pou_name, name)
    if obj_type == ObjectType.ACTION:
        return st_files.action_file_name(pou_name, name)
    if obj_type == ObjectType.TRANSITION:
        return st_files.transition_file_name(pou_name, name)
    return st_files.property_file_name(pou_name, name)


def expected_entries(obj):
    """[(directory entry the export writes, description)] for one object.

    Members of a POU are included (they sit beside the POU's file), its
    other descendants are not. An object the ST-only export skips yields
    nothing, so a graphical POU is never reported as missing.
    """
    obj_type = get_object_type(obj)
    name = obj.get_name()
    if obj_type == ObjectType.FOLDER:
        return [(name, name + "/ (folder)")]
    if obj_type == ObjectType.DUT:
        return [(st_files.dut_file_name(name), name + " (DUT)")]
    if obj_type in GVL_TYPES:
        return [(st_files.gvl_file_name(name), name + " (GVL)")]
    if obj_type != ObjectType.POU:
        return []
    entries = []
    if has_textual_implementation(obj):
        entries.append((st_files.pou_file_name(name), name + " (POU)"))
    for member in obj.get_children():
        if is_tracked_member(member):
            member_type = get_object_type(member)
            entries.append((member_file_name(name, member), name + "." + member.get_name() + " (" + member_type + ")"))
    return entries


def _report_not_on_disk(parent_obj, entries, label_prefix, report):
    present = set(entries)
    for obj in parent_obj.get_children():
        for entry, description in expected_entries(obj):
            if entry not in present:
                report.not_on_disk.append(label_prefix + description)


def remove_tracked_objects(children):
    """Strip a template copy of every object the ST-only import can recreate.

    Returns the names removed. A POU is removed only when all of its members
    are tracked, otherwise just its tracked members go and the POU shell
    stays for the import to update in place - removing it would take a
    graphical member with it that nothing could bring back. A folder goes
    once nothing is left in it.
    """
    removed = []
    for obj in list(children):
        obj_type = get_object_type(obj)
        name = obj.get_name()

        if obj_type == ObjectType.FOLDER:
            removed.extend([name + "/" + child for child in remove_tracked_objects(obj.get_children())])
            if len(obj.get_children()) == 0:
                obj.remove()
                removed.append(name + "/")
            continue

        if obj_type in (ObjectType.DUT,) + GVL_TYPES:
            obj.remove()
            removed.append(name)
            continue

        if obj_type != ObjectType.POU:
            continue

        members = list(obj.get_children())
        fully_tracked = has_textual_implementation(obj)
        for member in members:
            if not is_tracked_member(member):
                fully_tracked = False
        if fully_tracked:
            obj.remove()
            removed.append(name)
            continue
        for member in members:
            if is_tracked_member(member):
                member.remove()
                removed.append(name + "." + member.get_name())
    return removed
