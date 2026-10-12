# REMEMBER: this is python 2.7
"""ST-only export: the structured text of an application as .st files.

Only objects with a textual body are written - POUs, methods, actions,
transitions, properties, DUTs and GVLs - plus visualisations as .vis.xml.
Everything else (graphical POUs, task configuration, EVCs, managers, unmapped
objects) is left to the project template and listed in the summary so the
export never drops something silently.
"""
from __future__ import print_function

import os

from entrypoint import find_application, get_device_entrypoints
from import_export import write_native, write_st
from object_type import ObjectType, get_object_type
import st_files
from util import open_utf8

MAX_SUMMARY_NAMES = 8


def has_textual_declaration(obj):
    try:
        return bool(getattr(obj, "has_textual_declaration", False))
    except Exception:
        return False


def has_textual_implementation(obj):
    # A .NET property that exists can still throw when read, and the property
    # set varies between CODESYS versions.
    try:
        return bool(getattr(obj, "has_textual_implementation", False))
    except Exception:
        return False


def property_accessors(property_obj):
    return [c for c in property_obj.get_children() if get_object_type(c) == ObjectType.PROPERTY_METHOD]


def property_is_textual(property_obj):
    """True when the property and all of its accessors can be written as text."""
    if not has_textual_declaration(property_obj):
        return False
    for accessor in property_accessors(property_obj):
        if not has_textual_implementation(accessor):
            return False
    return True


class ExportStats(object):
    def __init__(self):
        self.st_files = 0
        self.visualisations = 0
        self.graphical = []  # objects with no textual body
        self.other = []  # (name, type) of objects that are not structured text

    def summary(self):
        lines = ["Wrote " + str(self.st_files) + " .st file(s) and " + str(self.visualisations) + " visualisation(s)."]
        if self.graphical:
            lines.append("Not exported, no ST body (graphical): " + _joined(self.graphical))
        if self.other:
            lines.append("Not exported, not ST: " + _joined([name + " (" + kind + ")" for name, kind in self.other]))
        return "\n".join(lines)


def _joined(names):
    shown = ", ".join(names[:MAX_SUMMARY_NAMES])
    if len(names) > MAX_SUMMARY_NAMES:
        shown += ", ... and " + str(len(names) - MAX_SUMMARY_NAMES) + " more (see the message view)"
    return shown


def export_project(project, staging_folder):
    """Write every device's application into staging_folder; returns the ExportStats.

    One device: its application's contents go straight into the folder. More
    than one: a folder per device (see st_files.device_export_folder). Nothing
    else is written - no devices, no communication, no native xml beyond the
    visualisations.
    """
    exporter = StExporter()
    devices = list(get_device_entrypoints(project))
    for device_obj in devices:
        application = find_application(device_obj)
        folder = st_files.device_export_folder(staging_folder, device_obj.get_name(), len(devices) == 1)
        if folder != staging_folder:
            os.mkdir(folder)
        exporter.export_children(application, folder)
    return exporter.stats


class StExporter(object):
    def __init__(self):
        self.stats = ExportStats()

    def export_children(self, parent_obj, folder):
        for child in parent_obj.get_children():
            self.export_child(child, parent_obj, folder)

    def export_child(self, child_obj, parent_obj, folder):
        obj_type = get_object_type(child_obj)
        name = child_obj.get_name()

        if obj_type == ObjectType.FOLDER:
            child_folder = os.path.join(folder, name)
            os.mkdir(child_folder)
            self.export_children(child_obj, child_folder)
            return

        if obj_type == ObjectType.POU:
            if has_textual_implementation(child_obj):
                with open_utf8(os.path.join(folder, st_files.pou_file_name(name)), "w") as f:
                    write_st(child_obj, f)
                self.stats.st_files += 1
            else:
                self._skip_graphical(name)
            # Members are written beside their POU, whether or not the POU
            # itself has a textual body: an ST method of a ladder POU is
            # still structured text.
            self.export_children(child_obj, folder)
            return

        if obj_type == ObjectType.DUT:
            with open_utf8(os.path.join(folder, st_files.dut_file_name(name)), "w") as f:
                f.write(child_obj.textual_declaration.text)
            self.stats.st_files += 1
            return

        if obj_type in (ObjectType.GVL, ObjectType.GVL_PERSISTENT):
            with open_utf8(os.path.join(folder, st_files.gvl_file_name(name)), "w") as f:
                f.write(child_obj.textual_declaration.text)
            self.stats.st_files += 1
            return

        if obj_type == ObjectType.VISUALISATION:
            # The one xml the ST-only export keeps: visualisations have no
            # textual form. Export-only; the import never reads it.
            write_native(child_obj, os.path.join(folder, st_files.visualisation_file_name(name)), recursive=False)
            self.stats.visualisations += 1
            return

        member_types = (
            ObjectType.METHOD,
            ObjectType.METHOD_NORET,
            ObjectType.ACTION,
            ObjectType.TRANSITION,
            ObjectType.PROPERTY,
        )
        if obj_type in member_types:
            if parent_obj is None:
                self._skip_other(name, obj_type)
                return
            self._export_member(child_obj, parent_obj, folder, obj_type)
            return

        self._skip_other(name, obj_type)

    def _export_member(self, child_obj, parent_obj, folder, obj_type):
        pou_name = parent_obj.get_name()
        name = child_obj.get_name()
        label = pou_name + "." + name

        if obj_type == ObjectType.PROPERTY:
            if not property_is_textual(child_obj):
                self._skip_graphical(label)
                return
            accessors = [
                (a.get_name(), a.textual_declaration.text, a.textual_implementation.text)
                for a in property_accessors(child_obj)
            ]
            with open_utf8(os.path.join(folder, st_files.property_file_name(pou_name, name)), "w") as f:
                f.write(st_files.format_property_file(child_obj.textual_declaration.text, accessors))
            self.stats.st_files += 1
            return

        if not has_textual_implementation(child_obj):
            self._skip_graphical(label)
            return

        if obj_type in (ObjectType.METHOD, ObjectType.METHOD_NORET):
            with open_utf8(os.path.join(folder, st_files.method_file_name(pou_name, name)), "w") as f:
                write_st(child_obj, f)
        elif obj_type == ObjectType.ACTION:
            # Actions and transitions have no textual declaration: the file is
            # the implementation alone, the kind is in the file name.
            with open_utf8(os.path.join(folder, st_files.action_file_name(pou_name, name)), "w") as f:
                f.write(child_obj.textual_implementation.text)
        else:
            with open_utf8(os.path.join(folder, st_files.transition_file_name(pou_name, name)), "w") as f:
                f.write(child_obj.textual_implementation.text)
        self.stats.st_files += 1

    def _skip_graphical(self, label):
        print("Not exported (no ST body): " + label)
        self.stats.graphical.append(label)

    def _skip_other(self, name, obj_type):
        print("Not exported (not ST): " + name + " (" + obj_type + ")")
        self.stats.other.append((name, obj_type))
