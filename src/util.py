# REMEMBER: this is python 2.7
import io
import os
import shutil
import sys
import traceback

import scriptengine  # type: ignore

from object_type import get_object_type

EXPORT_STAGING_SUFFIX = ".codescribe_staging"
EXPORT_BACKUP_SUFFIX = ".codescribe_backup"

# Entries the export never removes from its folder, so an export folder that
# is (or is linked into) a git checkout keeps the repository itself.
KEPT_NAMES = (".git",)

# Files the export never writes but keeps wherever they are in its folder:
# notes kept beside the exported code (a README.md, per-folder docs).
PRESERVED_EXTENSIONS = (".md",)

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ExportFolderLockedError(EnvironmentError):
    """The target export folder could not be replaced. The completed export is
    preserved in the staging folder."""


class NothingExportedError(EnvironmentError):
    """The export produced no files at all, so the existing export folder was
    left untouched rather than being replaced with an empty one."""


def begin_export_folder(target_folder):
    # The export is written into a sibling staging folder and only swapped into
    # place once it completes, so a locked target folder or a mid-export crash
    # cannot destroy the existing on-disk copy.
    staging_folder = target_folder + EXPORT_STAGING_SUFFIX
    if os.path.exists(staging_folder):
        shutil.rmtree(staging_folder)
    os.mkdir(staging_folder)
    return staging_folder


def _is_kept(relative):
    for part in relative.split(os.sep):
        if part in KEPT_NAMES:
            return True
    return False


def _is_preserved_file(file_name):
    return os.path.splitext(file_name)[1].lower() in PRESERVED_EXTENSIONS


def _holds_kept_entries(folder):
    for _dir_path, dir_names, file_names in os.walk(folder):
        for name in dir_names + file_names:
            if name in KEPT_NAMES:
                return True
    return False


def _carry_preserved_files(target_folder, staging_folder):
    """Copy the target's preserved files into the staging folder before the swap.

    Copied, not moved, so they are still in the target if the swap fails and
    the export falls back to syncing. A file the export wrote itself wins.
    """
    for dir_path, dir_names, file_names in os.walk(target_folder):
        dir_names[:] = [name for name in dir_names if name not in KEPT_NAMES]
        relative = os.path.relpath(dir_path, target_folder)
        destination = staging_folder if relative == "." else os.path.join(staging_folder, relative)
        for file_name in file_names:
            if not _is_preserved_file(file_name) or os.path.exists(os.path.join(destination, file_name)):
                continue
            if not os.path.isdir(destination):
                os.makedirs(destination)
            shutil.copy2(os.path.join(dir_path, file_name), os.path.join(destination, file_name))


def _remove_stale_files(staging_folder, target_folder):
    """Delete target files and directories this export did not write. Returns those that stay.

    The swap path replaces the folder wholesale, so anything the export did
    not produce is gone by definition. The sync path has to do the same, or a
    rendering from a previous export is left beside a new native xml and
    describes a POU that has since changed - with nothing to say so. Renaming
    a folder is what a lock stops; deleting a file inside it usually still
    works, and where it does not the file is named rather than left silent.
    A directory the export did not write goes once it is empty: git keeps no
    empty directory, but the import would read one as a folder. A .git entry
    and the preserved files (.md) are never touched.
    """
    stale = []
    for dir_path, dir_names, file_names in os.walk(target_folder):
        dir_names[:] = [name for name in dir_names if name not in KEPT_NAMES]
        relative = os.path.relpath(dir_path, target_folder)
        source = staging_folder if relative == "." else os.path.join(staging_folder, relative)
        for file_name in file_names:
            if file_name in KEPT_NAMES or _is_preserved_file(file_name):
                continue
            if os.path.exists(os.path.join(source, file_name)):
                continue
            try:
                os.remove(os.path.join(dir_path, file_name))
            except (OSError, IOError):
                stale.append(os.path.join(dir_path, file_name))

    for dir_path, _dir_names, _file_names in os.walk(target_folder, topdown=False):
        relative = os.path.relpath(dir_path, target_folder)
        if relative == "." or _is_kept(relative) or os.path.isdir(os.path.join(staging_folder, relative)):
            continue
        if os.listdir(dir_path):
            continue
        try:
            os.rmdir(dir_path)
        except (OSError, IOError):
            stale.append(dir_path)
    return stale


def _sync_export_files(staging_folder, target_folder):
    # Overwrite sync; anything in the target the export did not write is
    # removed afterwards by _remove_stale_files.
    for dir_path, dir_names, file_names in os.walk(staging_folder):
        relative = os.path.relpath(dir_path, staging_folder)
        destination = target_folder if relative == "." else os.path.join(target_folder, relative)
        if not os.path.isdir(destination):
            os.makedirs(destination)
        for file_name in file_names:
            shutil.copy2(os.path.join(dir_path, file_name), os.path.join(destination, file_name))


def _folder_has_files(folder):
    for _dir_path, _dir_names, file_names in os.walk(folder):
        if file_names:
            return True
    return False


def _is_reparse_point(path):
    """True for a directory junction (mklink /J) or a symbolic link.

    os.path.islink knows nothing of junctions, and under IronPython nothing
    of symbolic links either, so the .NET attributes are asked first; the
    stdlib checks cover CPython, where the tests run.
    """
    try:
        from System.IO import DirectoryInfo, FileAttributes
        import clr  # noqa: F401  IronPython only

        attributes = DirectoryInfo(path).Attributes
        return (attributes & FileAttributes.ReparsePoint) == FileAttributes.ReparsePoint
    except ImportError:
        pass
    except Exception as error:
        print("Warning: could not read the attributes of " + path + ": " + str(error))
    if os.path.islink(path):
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except (OSError, IOError):
        return False
    return (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0


def _sync_through(staging_folder, target_folder):
    """Write the staged files into the existing target folder, in place. Returns the stale entries that stay."""
    _sync_export_files(staging_folder, target_folder)
    stale = _remove_stale_files(staging_folder, target_folder)
    shutil.rmtree(staging_folder)
    return stale


def _warn_stale(stale):
    if stale:
        print(
            "WARNING: "
            + str(len(stale))
            + " entr(y/ies) this export did not write could not be removed and are now stale: "
            + ", ".join(stale[:6])
        )


def finalize_export_folder(target_folder, staging_folder):
    # An export that wrote no files must never replace the previous export:
    # swapping in an empty staging folder silently destroys it. Seen when
    # Export Lib To Files runs on a device project - its walker only exports
    # objects directly under the project root, and a device project keeps
    # everything under Devices, so the walk produces nothing.
    if not _folder_has_files(staging_folder):
        shutil.rmtree(staging_folder)
        raise NothingExportedError(
            "Nothing was exported, so the existing export folder was left untouched: "
            + target_folder
            + ". Export To Files needs a device project (objects under a Device); Export Lib To Files"
            + " needs a library project (objects directly under the project root)."
        )

    is_link = os.path.exists(target_folder) and _is_reparse_point(target_folder)
    if is_link or (os.path.exists(target_folder) and _holds_kept_entries(target_folder)):
        # A junction or symbolic link (mklink /J <project_name> <checkout>)
        # points the export at a folder elsewhere, typically a git checkout.
        # Renaming it would move the link rather than the contents, and
        # deleting the old copy afterwards would follow the link into the
        # checkout - so the export is written through the link instead. An
        # export folder that is itself a checkout (it holds .git) is synced
        # in place too: the swap would delete the repository with the old copy.
        try:
            stale = _sync_through(staging_folder, target_folder)
        except (OSError, IOError) as sync_error:
            raise ExportFolderLockedError(
                "Could not write into "
                + target_folder
                + ". The completed export is preserved in "
                + staging_folder
                + ". Original error: "
                + str(sync_error)
            )
        if is_link:
            print("Export folder " + target_folder + " is a link; synced the staged files through it.")
        else:
            print("Export folder " + target_folder + " holds a .git entry; synced the staged files into it.")
        _warn_stale(stale)
        return

    # Notes kept beside the code must survive the swap, which replaces the
    # folder wholesale.
    if os.path.exists(target_folder):
        _carry_preserved_files(target_folder, staging_folder)

    backup_folder = target_folder + EXPORT_BACKUP_SUFFIX
    try:
        if os.path.exists(target_folder):
            if os.path.exists(backup_folder):
                shutil.rmtree(backup_folder)
            os.rename(target_folder, backup_folder)
        os.rename(staging_folder, target_folder)
    except (OSError, IOError) as rename_error:
        # On Windows these renames fail while another program holds a handle on
        # the target folder. Fall back to copying the staged files into it.
        try:
            stale = _sync_through(staging_folder, target_folder)
        except (OSError, IOError):
            raise ExportFolderLockedError(
                "Could not replace the export folder "
                + target_folder
                + ". It is likely locked by another program (an open Explorer window, IDE, git client or antivirus)."
                + " The completed export is preserved in "
                + staging_folder
                + ". Original error: "
                + str(rename_error)
            )
        print(
            "Export folder "
            + target_folder
            + " is in use; synced the staged files into it instead of swapping folders."
        )
        _warn_stale(stale)
        return
    try:
        if os.path.exists(backup_folder):
            shutil.rmtree(backup_folder)
    except (OSError, IOError) as backup_error:
        # The export itself succeeded at this point; a leftover backup folder is
        # not worth failing it over.
        print("Warning: could not delete backup folder " + backup_folder + ": " + str(backup_error))


def ui_info(message):
    # Blocking dialog; the message view is easy to miss, so entry scripts report
    # their outcome through these as well.
    scriptengine.system.ui.info(message)


def ui_error_with_traceback(message):
    scriptengine.system.ui.error(message + "\n\n" + traceback.format_exc())


def open_utf8(path, mode):
    # ScriptEngine runs on Python 2.7; the builtin open() writes a byte stream and would
    # implicitly encode unicode text as ascii, crashing on Cyrillic / accented chars / smart
    # punctuation. io.open with an explicit encoding keeps everything UTF-8 round-trippable.
    return io.open(path, mode, encoding="utf-8")


def print_python_version():
    print("Python version: " + sys.version)


def assert_project_open():
    if scriptengine.projects.primary is None:
        raise ValueError("You must have a project open!")


def assert_path_exists(path):
    if not os.path.exists(path):
        raise ValueError("Path " + path + " does not exist")


def first_or_none(lst):
    return next(iter(lst), None)


def first_of_type_or_error(lst, obj_type, err):
    for obj in lst:
        if get_object_type(obj) == obj_type:
            return obj
    raise ValueError(err)


def first_of_type_or_none(lst, obj_type):
    for obj in lst:
        if get_object_type(obj) == obj_type:
            return obj
    return None


def first_or_error(lst, err):
    try:
        return next(iter(lst))
    except StopIteration:
        raise ValueError(err)
