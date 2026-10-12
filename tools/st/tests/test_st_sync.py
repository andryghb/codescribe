# REMEMBER: this must stay valid under IronPython 2.7 as well as Python 3.
"""Tests for the ST-only export, import and template stripping.

CODESYS is not available here, so a small fake stands in for the ScriptEngine
object tree: names, type GUIDs, children, find, the create_* calls, remove,
and textual_declaration / textual_implementation documents. The tests cover
what would break a working project: which files the export writes, that the
import only touches what differs, what it reports, and what a template keeps.

    python tools/st/tests/test_st_sync.py
"""

from __future__ import print_function, unicode_literals

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, os.path.join(REPO, "tools", "ci"))  # stubbed scriptengine

from object_type import GUID_TYPE_MAPPING, ObjectType  # noqa: E402
import st_export  # noqa: E402
import st_files  # noqa: E402
import st_sync  # noqa: E402

failures = []


def check(name, condition, detail=""):
    if condition:
        print("OK      " + name)
        return
    failures.append(name)
    print("FAIL    " + name + ((": " + detail) if detail else ""))


def check_equal(name, actual, expected):
    check(name, actual == expected, "expected %r, got %r" % (expected, actual))


# --- a stand-in for the ScriptEngine object tree -----------------------------

TYPE_TO_GUID = dict((obj_type, guid) for guid, obj_type in GUID_TYPE_MAPPING.items())
TYPE_TO_GUID[ObjectType.UNKNOWN] = "00000000-0000-0000-0000-000000000000"


class FakeText(object):
    def __init__(self, text):
        self.text = text
        self.replace_calls = 0

    def replace(self, text):
        self.text = text
        self.replace_calls += 1


class FakeObject(object):
    def __init__(self, name, obj_type, declaration=None, implementation=None):
        self._name = name
        self.type = TYPE_TO_GUID[obj_type]
        self.children = []
        self.parent = None
        self.removed = False
        self.has_textual_declaration = declaration is not None
        self.has_textual_implementation = implementation is not None
        if declaration is not None:
            self.textual_declaration = FakeText(declaration)
        if implementation is not None:
            self.textual_implementation = FakeText(implementation)

    def get_name(self):
        return self._name

    def get_children(self):
        return list(self.children)

    def find(self, name, recursive=False):
        return [c for c in self.children if c._name == name]

    def add(self, child):
        child.parent = self
        self.children.append(child)
        return child

    def remove(self):
        self.parent.children.remove(self)
        self.removed = True

    def create_folder(self, name):
        self.add(FakeObject(name, ObjectType.FOLDER))

    def create_pou(self, name):
        return self.add(FakeObject(name, ObjectType.POU, "PROGRAM " + name + "\nVAR\nEND_VAR\n", ""))

    def create_dut(self, name):
        return self.add(FakeObject(name, ObjectType.DUT, "TYPE " + name + " :\nSTRUCT\nEND_STRUCT\nEND_TYPE\n"))

    def create_gvl(self, name):
        return self.add(FakeObject(name, ObjectType.GVL, "VAR_GLOBAL\nEND_VAR\n"))

    def create_method(self, name):
        return self.add(FakeObject(name, ObjectType.METHOD, "METHOD " + name + "\nVAR\nEND_VAR\n", ""))

    def create_action(self, name, language):
        return self.add(FakeObject(name, ObjectType.ACTION, None, ""))

    def create_transition(self, name, language):
        return self.add(FakeObject(name, ObjectType.TRANSITION, None, ""))

    def create_property(self, name, return_type, language):
        # Like the Add Object dialog: both accessors come with the property.
        prop = self.add(FakeObject(name, ObjectType.PROPERTY, "PROPERTY " + name + " : " + return_type + "\n"))
        prop.add(FakeObject("Get", ObjectType.PROPERTY_METHOD, "VAR\nEND_VAR\n", ""))
        prop.add(FakeObject("Set", ObjectType.PROPERTY_METHOD, "VAR\nEND_VAR\n", ""))
        return prop

    def export_native(self, destination, recursive=False):
        with io.open(destination, "w", encoding="utf-8") as f:
            # Indented like a real native export; write_native's regex expects it.
            f.write('  <Single Name="Timestamp" Type="long">1234</Single>\n  <Visu name="' + self._name + '"/>\n')


def build_application():
    """The project side of a round trip, with one of everything."""
    app = FakeObject("Application", ObjectType.UNKNOWN)

    motor = app.add(
        FakeObject("FB_Motor", ObjectType.POU, "FUNCTION_BLOCK FB_Motor\nVAR\n\tx : INT;\nEND_VAR\n", "x := x + 1;\n")
    )
    motor.add(FakeObject("Start", ObjectType.METHOD, "METHOD Start : BOOL\nVAR\nEND_VAR\n", "Start := TRUE;\n"))
    motor.add(FakeObject("Reset", ObjectType.ACTION, None, "x := 0;\n"))
    speed = motor.add(FakeObject("Speed", ObjectType.PROPERTY, "PROPERTY PUBLIC Speed : INT\n"))
    speed.add(FakeObject("Get", ObjectType.PROPERTY_METHOD, "VAR\nEND_VAR\n", "Speed := x;\n"))
    speed.add(FakeObject("Set", ObjectType.PROPERTY_METHOD, "VAR\nEND_VAR\n", "x := Speed;\n"))

    app.add(FakeObject("ST_Point", ObjectType.DUT, "TYPE ST_Point :\nSTRUCT\n\tx : INT;\nEND_STRUCT\nEND_TYPE\n"))
    app.add(FakeObject("GVL", ObjectType.GVL, "VAR_GLOBAL\n\tg : INT;\nEND_VAR\n"))
    app.add(
        FakeObject("PersistentVars", ObjectType.GVL_PERSISTENT, "VAR_GLOBAL PERSISTENT RETAIN\n\tp : INT;\nEND_VAR\n")
    )

    utils = app.add(FakeObject("Utils", ObjectType.FOLDER))
    utils.add(
        FakeObject("F_Add", ObjectType.POU, "FUNCTION F_Add : INT\nVAR_INPUT\n\ta : INT;\nEND_VAR\n", "F_Add := a;\n")
    )

    # A ladder POU: declaration only, but an ST method of its own.
    ladder = app.add(FakeObject("LD_Main", ObjectType.POU, "PROGRAM LD_Main\nVAR\nEND_VAR\n", None))
    ladder.add(FakeObject("Step", ObjectType.METHOD, "METHOD Step\nVAR\nEND_VAR\n", "LD_Main();\n"))

    # An ST POU with a ladder action: the POU is text, one member is not.
    mixed = app.add(FakeObject("FB_Mixed", ObjectType.POU, "FUNCTION_BLOCK FB_Mixed\nVAR\nEND_VAR\n", ";\n"))
    mixed.add(FakeObject("Act", ObjectType.ACTION, None, None))
    mixed.add(FakeObject("Tick", ObjectType.METHOD, "METHOD Tick\nVAR\nEND_VAR\n", ";\n"))

    app.add(FakeObject("Visu", ObjectType.VISUALISATION))
    app.add(FakeObject("TaskConfig", ObjectType.TASK_CONFIGURATION))
    app.add(FakeObject("Library Manager", ObjectType.LIBRARY_MANAGER))
    return app


def build_template_application():
    """What a template holds in ST-only mode: everything the export skips."""
    app = FakeObject("Application", ObjectType.UNKNOWN)
    app.add(FakeObject("LD_Main", ObjectType.POU, "PROGRAM LD_Main\nVAR\nEND_VAR\n", None))
    mixed = app.add(FakeObject("FB_Mixed", ObjectType.POU, "FUNCTION_BLOCK FB_Mixed\nVAR\nEND_VAR\n", ";\n"))
    mixed.add(FakeObject("Act", ObjectType.ACTION, None, None))
    app.add(FakeObject("Visu", ObjectType.VISUALISATION))
    app.add(FakeObject("TaskConfig", ObjectType.TASK_CONFIGURATION))
    app.add(FakeObject("Library Manager", ObjectType.LIBRARY_MANAGER))
    return app


def list_files(folder):
    found = []
    for dir_path, _dir_names, file_names in os.walk(folder):
        for file_name in file_names:
            relative = os.path.relpath(os.path.join(dir_path, file_name), folder)
            found.append(relative.replace(os.sep, "/"))
    return sorted(found)


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def find(obj, *names):
    for name in names:
        obj = obj.find(name)[0]
    return obj


def total_replace_calls(obj):
    count = 0
    for attr in ("textual_declaration", "textual_implementation"):
        doc = getattr(obj, attr, None)
        if doc is not None:
            count += doc.replace_calls
    for child in obj.get_children():
        count += total_replace_calls(child)
    return count


EXPECTED_FILES = [
    "FB_Mixed.Tick.st",
    "FB_Mixed.st",
    "FB_Motor.Reset.action.st",
    "FB_Motor.Speed.property.st",
    "FB_Motor.Start.st",
    "FB_Motor.st",
    "GVL.gvl.st",
    "LD_Main.Step.st",
    "PersistentVars.gvl.st",
    "ST_Point.st",
    "Utils/F_Add.st",
    "Visu.vis.xml",
]

work = tempfile.mkdtemp()
try:
    # --- export -------------------------------------------------------------
    export_folder = os.path.join(work, "export")
    os.mkdir(export_folder)
    exporter = st_export.StExporter()
    exporter.export_children(build_application(), export_folder)

    check_equal(
        "export writes the .st files and the visualisation, nothing else", list_files(export_folder), EXPECTED_FILES
    )
    check_equal("export counts .st files", exporter.stats.st_files, 11)
    check_equal("export counts visualisations", exporter.stats.visualisations, 1)
    check_equal("export names what has no ST body", exporter.stats.graphical, ["LD_Main", "FB_Mixed.Act"])
    check_equal(
        "export names what is not ST",
        exporter.stats.other,
        [("TaskConfig", ObjectType.TASK_CONFIGURATION), ("Library Manager", ObjectType.LIBRARY_MANAGER)],
    )
    check_equal(
        "POU file is declaration, delimiter, implementation",
        read(os.path.join(export_folder, "FB_Motor.st")),
        "FUNCTION_BLOCK FB_Motor\nVAR\n\tx : INT;\nEND_VAR\n"
        + st_files.IMPLEMENTATION_DELIMITER_INSERT
        + "x := x + 1;\n",
    )
    check_equal(
        "action file is the implementation alone",
        read(os.path.join(export_folder, "FB_Motor.Reset.action.st")),
        "x := 0;\n",
    )
    check_equal(
        "GVL file is the declaration alone",
        read(os.path.join(export_folder, "GVL.gvl.st")),
        "VAR_GLOBAL\n\tg : INT;\nEND_VAR\n",
    )
    check(
        "visualisation xml has its timestamp zeroed like the full export",
        '<Single Name="Timestamp" Type="long">0</Single>' in read(os.path.join(export_folder, "Visu.vis.xml")),
    )
    summary = exporter.stats.summary()
    check("summary counts", "Wrote 11 .st file(s) and 1 visualisation(s)." in summary, summary)
    check("summary names the graphical POU", "LD_Main" in summary, summary)

    # --- import into a template: everything is added ---------------------------
    app = build_template_application()
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)

    check_equal(
        "fresh import adds every .st file and the folder",
        sorted(report.added),
        sorted(
            [
                "FB_Mixed.Tick.st",
                "FB_Motor.Reset.action.st",
                "FB_Motor.Speed.property.st",
                "FB_Motor.Start.st",
                "FB_Motor.st",
                "GVL.gvl.st",
                "LD_Main.Step.st",
                "PersistentVars.gvl.st",
                "ST_Point.st",
                "Utils/",
                "Utils/F_Add.st",
            ]
        ),
    )
    check_equal("fresh import: the ST POU in the template is unchanged", report.unchanged, ["FB_Mixed.st"])
    check_equal("fresh import updates nothing", report.updated, [])
    check_equal("fresh import ignores the visualisation xml", report.ignored, ["Visu.vis.xml"])
    check_equal("fresh import reports nothing missing", report.not_on_disk, [])
    check_equal("fresh import has no failures", report.failed, [])
    check_equal("imported POU implementation", find(app, "FB_Motor").textual_implementation.text, "x := x + 1;\n")
    check_equal(
        "imported POU declaration",
        find(app, "FB_Motor").textual_declaration.text,
        "FUNCTION_BLOCK FB_Motor\nVAR\n\tx : INT;\nEND_VAR\n",
    )
    check_equal("imported action", find(app, "FB_Motor", "Reset").textual_implementation.text, "x := 0;\n")
    check_equal(
        "imported property keeps both accessors",
        [c.get_name() for c in find(app, "FB_Motor", "Speed").get_children()],
        ["Get", "Set"],
    )
    check_equal(
        "imported property Set", find(app, "FB_Motor", "Speed", "Set").textual_implementation.text, "x := Speed;\n"
    )
    check_equal(
        "imported method of the ladder POU", find(app, "LD_Main", "Step").textual_implementation.text, "LD_Main();\n"
    )
    check("the ladder POU is still graphical", not find(app, "LD_Main").has_textual_implementation)
    check("the ladder action survived", not find(app, "FB_Mixed", "Act").removed)
    check_equal(
        "imported function in its folder", find(app, "Utils", "F_Add").textual_implementation.text, "F_Add := a;\n"
    )
    check("task configuration is untouched", find(app, "TaskConfig") is not None)

    # --- a second export of the imported project is byte-identical --------------
    second_folder = os.path.join(work, "second")
    os.mkdir(second_folder)
    st_export.StExporter().export_children(app, second_folder)
    check_equal("re-export writes the same files", list_files(second_folder), EXPECTED_FILES)
    identical = True
    for relative in EXPECTED_FILES:
        if relative.endswith(".st") and read(os.path.join(export_folder, relative)) != read(
            os.path.join(second_folder, relative)
        ):
            identical = False
            print("        differs: " + relative)
    check("re-export is byte-identical", identical)

    # --- a second import changes nothing -----------------------------------------
    before = total_replace_calls(app)
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check_equal("second import adds nothing", report.added, [])
    check_equal("second import updates nothing", report.updated, [])
    check_equal("second import leaves every file unchanged", len(report.unchanged), 11)
    check_equal("second import writes no text", total_replace_calls(app), before)

    # --- only the file that differs is applied ------------------------------------
    write(
        os.path.join(export_folder, "Utils", "F_Add.st"),
        "FUNCTION F_Add : INT\nVAR_INPUT\n\ta : INT;\nEND_VAR\n"
        + st_files.IMPLEMENTATION_DELIMITER_INSERT
        + "F_Add := a + 1;\n",
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check_equal("edited file is updated", report.updated, ["Utils/F_Add.st"])
    check_equal("other files are unchanged", len(report.unchanged), 10)
    check_equal(
        "edited implementation applied", find(app, "Utils", "F_Add").textual_implementation.text, "F_Add := a + 1;\n"
    )
    check_equal("unedited declaration not rewritten", find(app, "Utils", "F_Add").textual_declaration.replace_calls, 1)

    # --- line endings, a BOM and trailing blank lines are not changes --------------
    write(
        os.path.join(export_folder, "FB_Motor.st"),
        "\ufeffFUNCTION_BLOCK FB_Motor\r\nVAR\r\n\tx : INT;\r\nEND_VAR\r\n\r\n"
        + st_files.IMPLEMENTATION_DELIMITER
        + "\r\n\r\nx := x + 1;\r\n\r\n\r\n",
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check("CRLF, BOM and trailing newlines are not a change", "FB_Motor.st" in report.unchanged, repr(report.updated))

    # --- an object with no file is reported, not removed ----------------------------
    os.remove(os.path.join(export_folder, "GVL.gvl.st"))
    os.remove(os.path.join(export_folder, "FB_Motor.Start.st"))
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check_equal(
        "objects with no file are reported", sorted(report.not_on_disk), ["FB_Motor.Start (METHOD)", "GVL (GVL)"]
    )
    check("the GVL is still in the project", not find(app, "GVL").removed)
    check("the method is still in the project", not find(app, "FB_Motor", "Start").removed)
    check("a graphical POU is never reported missing", "LD_Main (POU)" not in report.not_on_disk)

    # --- a file whose kind clashes with the project fails, nothing is created -------
    write(
        os.path.join(export_folder, "ST_Point.st"),
        "FUNCTION_BLOCK ST_Point\nVAR\nEND_VAR\n" + st_files.IMPLEMENTATION_DELIMITER_INSERT + ";\n",
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check_equal("kind clash is a failure", [label for label, _error in report.failed], ["ST_Point.st"])
    check(
        "kind clash names the existing kind", "exists in the project as DUT" in report.failed[0][1], report.failed[0][1]
    )
    check_equal("kind clash creates nothing", [get.get_name() for get in app.find("ST_Point")], ["ST_Point"])
    check("a failure makes the dialog an error", report.has_failures())
    check("summary lists the failure", "ST_Point.st:" in report.summary(), report.summary())

    # --- a method file whose POU is missing fails ------------------------------------
    write(
        os.path.join(export_folder, "FB_Gone.Run.st"),
        "METHOD Run\nVAR\nEND_VAR\n" + st_files.IMPLEMENTATION_DELIMITER_INSERT + ";\n",
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check("missing parent POU is a failure", "FB_Gone.Run.st" in [label for label, _error in report.failed])
    os.remove(os.path.join(export_folder, "FB_Gone.Run.st"))

    # --- properties -------------------------------------------------------------------
    write(
        os.path.join(export_folder, "FB_Motor.Speed.property.st"),
        st_files.format_property_file(
            "PROPERTY PUBLIC Speed : INT\n", [("Get", "VAR\nEND_VAR\n", "Speed := x * 2;\n")]
        ),
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check(
        "property with a dropped accessor is updated",
        "FB_Motor.Speed.property.st" in report.updated,
        repr(report.failed),
    )
    check_equal(
        "dropped accessor is removed", [c.get_name() for c in find(app, "FB_Motor", "Speed").get_children()], ["Get"]
    )
    check_equal(
        "kept accessor is updated",
        find(app, "FB_Motor", "Speed", "Get").textual_implementation.text,
        "Speed := x * 2;\n",
    )

    write(
        os.path.join(export_folder, "FB_Motor.Speed.property.st"),
        st_files.format_property_file(
            "PROPERTY PUBLIC Speed : INT\n",
            [("Get", "VAR\nEND_VAR\n", "Speed := x * 2;\n"), ("Set", "VAR\nEND_VAR\n", "x := Speed;\n")],
        ),
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check(
        "an accessor the project lacks is a failure",
        "FB_Motor.Speed.property.st" in [label for label, _error in report.failed],
    )
    check(
        "the failure says to add the accessor in CODESYS",
        "add it in CODESYS" in report.failed[-1][1],
        report.failed[-1][1],
    )

    write(
        os.path.join(export_folder, "FB_Motor.Count.property.st"),
        st_files.format_property_file(
            "{attribute 'monitoring' := 'variable'}\nPROPERTY Count : DINT\n",
            [("Get", "VAR\nEND_VAR\n", "Count := 1;\n")],
        ),
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check("a new property is added", "FB_Motor.Count.property.st" in report.added, repr(report.failed))
    check_equal(
        "new property keeps only the listed accessor",
        [c.get_name() for c in find(app, "FB_Motor", "Count").get_children()],
        ["Get"],
    )
    check_equal(
        "new property carries the file's declaration",
        find(app, "FB_Motor", "Count").textual_declaration.text,
        "{attribute 'monitoring' := 'variable'}\nPROPERTY Count : DINT\n",
    )

    write(os.path.join(export_folder, "FB_Motor.Broken.property.st"), "PROPERTY Broken\n")
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check(
        "a property without a return type cannot be created",
        "FB_Motor.Broken.property.st" in [label for label, _error in report.failed],
    )
    check_equal("no property created without a return type", find(app, "FB_Motor").find("Broken"), [])

    # --- a .st file for a graphical POU is refused ---------------------------------------
    write(
        os.path.join(export_folder, "LD_Main.st"),
        "PROGRAM LD_Main\nVAR\nEND_VAR\n" + st_files.IMPLEMENTATION_DELIMITER_INSERT + ";\n",
    )
    report = st_sync.SyncReport()
    st_sync.sync_directory(export_folder, app, "", report)
    check("a .st file for a graphical POU is a failure", "LD_Main.st" in [label for label, _error in report.failed])
    check("the ladder POU stays graphical", not find(app, "LD_Main").has_textual_implementation)

    # --- the report text ------------------------------------------------------------------
    report = st_sync.SyncReport()
    for i in range(12):
        report.added.append("Pou%d.st" % i)
    report.updated.append("X.st")
    report.not_on_disk.append("Old (POU)")
    summary = report.summary()
    check("summary caps long lists", "... and 4 more (see the message view)" in summary, summary)
    check("summary lists what is not on disk", "Old (POU)" in summary, summary)
    check("summary without failures has no failed line", "Failed" not in summary, summary)
    check("details list everything", "Pou11.st" in report.details())

    # --- template stripping ----------------------------------------------------------------
    app = build_application()
    removed = st_sync.remove_tracked_objects(app.get_children())
    check_equal(
        "template loses what the import recreates",
        sorted(removed),
        sorted(
            ["FB_Motor", "ST_Point", "GVL", "PersistentVars", "Utils/F_Add", "Utils/", "LD_Main.Step", "FB_Mixed.Tick"]
        ),
    )
    check_equal(
        "template keeps the rest",
        sorted(c.get_name() for c in app.get_children()),
        sorted(["LD_Main", "FB_Mixed", "Visu", "TaskConfig", "Library Manager"]),
    )
    check_equal("the ladder POU keeps no ST method", [c.get_name() for c in find(app, "LD_Main").get_children()], [])
    check_equal(
        "a POU with a graphical member keeps the member and loses the ST one",
        [c.get_name() for c in find(app, "FB_Mixed").get_children()],
        ["Act"],
    )

    # --- a folder that holds something untracked stays --------------------------------------
    app = FakeObject("Application", ObjectType.UNKNOWN)
    folder = app.add(FakeObject("Diagrams", ObjectType.FOLDER))
    folder.add(FakeObject("LD_One", ObjectType.POU, "PROGRAM LD_One\n", None))
    folder.add(FakeObject("ST_One", ObjectType.POU, "PROGRAM ST_One\n", ";\n"))
    removed = st_sync.remove_tracked_objects(app.get_children())
    check_equal("only the ST POU leaves a mixed folder", removed, ["Diagrams/ST_One"])
    check_equal("the folder with the ladder POU stays", [c.get_name() for c in app.get_children()], ["Diagrams"])
finally:
    shutil.rmtree(work)

print()
if failures:
    print("%d failure(s): %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("all passed")
