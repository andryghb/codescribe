# REMEMBER: this is python 2.7
"""ST-only export: the structured text of an application as .st files.

Only objects with a textual body are written - POUs, methods, actions,
transitions, properties, DUTs and GVLs - plus export-only xml the import
never reads: visualisations as .vis.xml, the task configuration as
<name>.xml and each CANopen manager and slave device as _CANOpen/<name>.xml,
so all of them can be reviewed in git while the project template keeps the
real objects. Everything else (graphical POUs, EVCs, managers, unmapped objects,
every other device) is left to the template and listed in the summary so
the export never drops something silently.
"""
from __future__ import print_function

import os

from entrypoint import find_application, get_device_entrypoints
from import_export import write_native, write_st
from object_type import ObjectType, get_object_type
import st_files
from util import open_utf8

MAX_SUMMARY_NAMES = 8

# The connector a CANopen slave plugs into a CANopen manager with. The manager
# carries it as a parent connector and plugs into the CAN bus with a
# Common.CANbus child connector, so only the managers have it as a parent
# connector and only the slaves (remote devices) as a child connector.
CANOPEN_INTERFACE = "Common.CANopen"
CONNECTOR_ROLE_PARENT = 0
CONNECTOR_ROLE_CHILD = 1


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
        self.task_configurations = 0
        self.canopen_managers = 0
        self.canopen_slaves = 0
        self.graphical = []  # objects with no textual body
        self.other = []  # (name, type) of objects that are not structured text

    def summary(self):
        lines = [
            "Wrote "
            + str(self.st_files)
            + " .st file(s), "
            + str(self.visualisations)
            + " visualisation(s), "
            + str(self.task_configurations)
            + " task configuration(s), "
            + str(self.canopen_managers)
            + " CANopen manager(s) and "
            + str(self.canopen_slaves)
            + " CANopen slave(s)."
        ]
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


def _has_role(role, role_name, role_value):
    # ConnectorRole is a .NET enum: str() gives its name under IronPython and
    # int() its value. Either is enough.
    if str(role).split(".")[-1] == role_name:
        return True
    try:
        return int(role) == role_value
    except (TypeError, ValueError):
        return False


def _connector_interfaces(obj, role_name, role_value):
    try:
        connectors = obj.connectors
    except Exception:
        return []
    interfaces = []
    for connector in connectors or []:
        try:
            if _has_role(connector.connector_role, role_name, role_value):
                interfaces.append(u"%s" % connector.interface)
        except Exception:
            continue
    return interfaces


def child_interfaces(obj):
    """The interfaces of the child connectors of a device; empty for anything else."""
    return _connector_interfaces(obj, "Child", CONNECTOR_ROLE_CHILD)


def parent_interfaces(obj):
    """The interfaces of the parent connectors of a device; empty for anything else."""
    return _connector_interfaces(obj, "Parent", CONNECTOR_ROLE_PARENT)


def _is_application(obj, application):
    if obj == application:
        return True
    try:
        return bool(getattr(obj, "is_application", False))
    except Exception:
        return False


def find_canopen_devices(device_obj, application):
    """([(CANopen device, is_manager)] anywhere under device_obj, child connector interfaces seen).

    In tree order, so each manager comes before its slaves. The application
    is not walked, as no device lives in it. A slave is not walked either:
    its modules are part of its own recursive export.
    """
    devices = []
    seen = set()

    def walk(obj):
        for child in obj.get_children():
            if _is_application(child, application):
                continue
            interfaces = child_interfaces(child)
            seen.update(interfaces)
            if CANOPEN_INTERFACE in interfaces:
                devices.append((child, False))
                continue
            if CANOPEN_INTERFACE in parent_interfaces(child):
                devices.append((child, True))
            walk(child)

    walk(device_obj)
    return devices, seen


def export_project(project, staging_folder):
    """Write every device's application into staging_folder; returns the ExportStats.

    One device: its application's contents go straight into the folder. More
    than one: a folder per device (see st_files.device_export_folder). Beside
    them, in a _CANOpen folder, go the device's CANopen managers and slaves;
    nothing else is written - no other devices, no communication.
    """
    exporter = StExporter()
    devices = list(get_device_entrypoints(project))
    for device_obj in devices:
        application = find_application(device_obj)
        for child in application.get_children():
            # The import skips that folder, so application code in a folder
            # of the same name would never come back. Refuse before writing.
            if st_files.is_canopen_folder(child.get_name()):
                raise ValueError(
                    "The application of "
                    + device_obj.get_name()
                    + " holds '"
                    + child.get_name()
                    + "', a name the export reserves for the CANopen devices and the import skips."
                    + " Rename it in CODESYS and export again."
                )
        folder = st_files.device_export_folder(staging_folder, device_obj.get_name(), len(devices) == 1)
        if folder != staging_folder:
            os.mkdir(folder)
        exporter.export_children(application, folder)
        exporter.export_canopen(device_obj, application, folder)
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
            # Export-only xml: visualisations have no textual form. The import
            # never reads it; the template carries the real object.
            write_native(child_obj, os.path.join(folder, st_files.visualisation_file_name(name)), recursive=False)
            self.stats.visualisations += 1
            return

        if obj_type == ObjectType.TASK_CONFIGURATION:
            # Export-only xml as well, named as full mode names it so a mode
            # switch does not rename the file in git. Recursive: the tasks
            # are children of the configuration object.
            write_native(child_obj, os.path.join(folder, st_files.task_configuration_file_name(name)), recursive=True)
            self.stats.task_configurations += 1
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

    def export_canopen(self, device_obj, application, folder):
        """Write each CANopen manager and slave under device_obj as _CANOpen/<name>.xml in folder.

        Export-only, like the visualisations: the import skips the folder and
        the template carries the real devices. A manager is written alone, as
        its slaves have files of their own. A slave is written recursively, so
        the modules of a modular slave come with it; the node ID, SDOs and PDO
        mappings are in the slave's own entry. The folder is made only when
        there is a manager or a slave.
        """
        devices, seen = find_canopen_devices(device_obj, application)
        if not devices:
            print(
                "No CANopen manager or slave found under "
                + device_obj.get_name()
                + "; child connector interfaces seen: "
                + (", ".join(sorted(seen)) or "none")
            )
            return
        canopen_folder = os.path.join(folder, st_files.CANOPEN_FOLDER)
        os.mkdir(canopen_folder)
        written = set()
        for device, is_manager in devices:
            name = device.get_name()
            file_name = st_files.canopen_device_file_name(name)
            if file_name in written:
                # Two devices of one name, such as slaves on different
                # managers: name the second by its parent too, rather than
                # overwrite the first.
                file_name = st_files.canopen_device_file_name(device.parent.get_name() + "." + name)
            write_native(device, os.path.join(canopen_folder, file_name), recursive=not is_manager)
            written.add(file_name)
            kind = "manager" if is_manager else "slave"
            print("Exported CANopen " + kind + " " + name + " to " + st_files.CANOPEN_FOLDER + "/" + file_name)
            if is_manager:
                self.stats.canopen_managers += 1
            else:
                self.stats.canopen_slaves += 1

    def _skip_graphical(self, label):
        print("Not exported (no ST body): " + label)
        self.stats.graphical.append(label)

    def _skip_other(self, name, obj_type):
        print("Not exported (not ST): " + name + " (" + obj_type + ")")
        self.stats.other.append((name, obj_type))
