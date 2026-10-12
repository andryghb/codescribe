# REMEMBER: this is python 2.7
from __future__ import print_function

import scriptengine  # type: ignore

from import_from_files import import_from_files
from mode import is_st_only
from util import *

report = None
try:
    print_python_version()
    assert_project_open()

    if is_st_only(scriptengine.projects.primary):
        question = (
            "Import From Files will update the project from the .st files on disk."
            + " Objects whose file differs are overwritten, unexported changes included;"
            + " objects whose file matches are left alone.\n\nDo you want to continue?"
        )
    else:
        question = (
            "Import From Files will overwrite unexported changes in the current project!\n\nDo you want to continue?"
        )

    ui_continue = scriptengine.system.ui.prompt(
        question,
        choice=scriptengine.PromptChoice.YesNo,
        default_result=scriptengine.PromptResult.No,
        store_description="Don't show again",
        store_key="import_from_files_confirm",
    )

    if ui_continue == scriptengine.PromptResult.Yes:
        report = import_from_files(scriptengine.projects.primary)
        if report is None:
            ui_info("Import From Files complete.")
        else:
            report.show_dialog("Import From Files")
    else:
        print("Import cancelled.")
except Exception as e:
    print(e)
    ui_error_with_traceback("Import From Files failed!")
    raise e

if report is not None and report.has_failures():
    # Outside the try: the report dialog has already said what failed, and
    # CODESYS must still see the script as failed.
    raise ValueError(str(len(report.failed)) + " file(s) could not be imported; see the message view")

print("Done!")
