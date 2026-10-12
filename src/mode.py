# REMEMBER: this is python 2.7
"""Which export mode a project uses.

The mode is read from a ``codescribe.json`` file in the same folder as the
``.project`` file, so it travels with the project and its export in git:

    {"mode": "st-only"}   the default when the file is absent
    {"mode": "full"}      the native-xml export of earlier versions

``st-only`` exports the application's structured text as .st files (plus the
visualisations as .vis.xml) and imports only .st files, updating the objects
that differ in place. ``full`` exports and imports everything the project
carries - devices, communication, task configuration, graphical POUs - as
native xml alongside the .st files, replacing every tracked object on import.
"""
import json
import os

from util import open_utf8

CONFIG_FILE_NAME = "codescribe.json"
MODE_ST_ONLY = "st-only"
MODE_FULL = "full"
MODES = (MODE_ST_ONLY, MODE_FULL)
DEFAULT_MODE = MODE_ST_ONLY


def config_path(project):
    return os.path.join(os.path.dirname(project.path), CONFIG_FILE_NAME)


def read_mode(project):
    path = config_path(project)
    if not os.path.exists(path):
        return DEFAULT_MODE

    with open_utf8(path, "r") as f:
        text = f.read()
    # Notepad writes a BOM, which json.loads rejects.
    if text.startswith(u"\ufeff"):
        text = text[1:]

    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError(path + " is not valid JSON: " + str(error))
    if not isinstance(data, dict):
        raise ValueError(path + ' must hold a JSON object such as {"mode": "st-only"}')

    mode = data.get("mode", DEFAULT_MODE)
    if mode not in MODES:
        raise ValueError(path + ": unknown mode " + repr(mode) + "; expected one of: " + ", ".join(MODES))
    return mode


def is_st_only(project):
    return read_mode(project) == MODE_ST_ONLY
