"""Static check for typos in the ``namespace.append_obj("ClassName", ...,
module_name="pkg.module")`` factory-registration pattern used throughout
``eco/bernina/bernina.py`` and its delegated sibling modules (e.g.
``bernina_front_end.py``).

That pattern exists specifically to keep device modules unimported until
first use (see the module-level docstring discussion this was built from) -
which is exactly why a typo in either string doesn't fail until someone
happens to access that particular lazy device, possibly mid-experiment, as a
confusing error deep inside a proxy. This script catches that immediately
instead, by actually importing every referenced module and checking the
referenced class/function exists on it - without ever instantiating a
device or touching EPICS (importing an eco device module is required by
eco's own lazy-import design to never have import-time side effects; see
``eco.xoptics.beamline_bernina``'s module docstring for the same claim about
its own device imports).

Not wired into anything - this only runs when you explicitly invoke it. It
is not imported by ``bernina.py``, ``eco/__init__.py``, or any part of the
normal startup path, so it adds no overhead or risk to actually using eco.

Usage::

    python -m eco.utilities.validate_namespace_factories
    python -m eco.utilities.validate_namespace_factories path/to/file.py [more.py ...]

With no arguments, scans ``eco/bernina/bernina.py`` plus every sibling
``eco/bernina/bernina_*.py`` module (the delegated-registration files, e.g.
``bernina_front_end.py`` and any future ``bernina_optics_hutch.py`` /
``bernina_hutch.py``).

Exit code 0 if every reference resolves, 1 otherwise (so it's usable as a
pre-commit/CI gate later, if wanted - not set up here).
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

_MISSING = object()


class UnparsableFile(Exception):
    pass


def _find_factory_calls(path: Path):
    """Yield (lineno, module_name, factory_name) for every
    ``<something>.append_obj("FactoryName", ..., module_name="pkg.mod", ...)``
    call in ``path`` - found via ast.parse, nothing executed or imported.

    Raises UnparsableFile if the file itself isn't valid Python (e.g. a
    known-broken draft) - the caller treats that as a single reportable
    problem rather than letting it crash the whole run."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as exc:
        raise UnparsableFile(f"{path} is not valid Python: {exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "append_obj"):
            continue
        module_name = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "module_name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ),
            None,
        )
        if module_name is None or not node.args:
            continue
        factory_arg = node.args[0]
        if isinstance(factory_arg, ast.Constant) and isinstance(factory_arg.value, str):
            yield node.lineno, module_name, factory_arg.value


def validate_file(path: Path):
    """Return a list of (lineno, module_name, factory_name, error_message)
    for every reference in ``path`` that doesn't actually resolve. A file
    that isn't valid Python at all is reported as a single such entry
    (lineno=None) rather than raising."""
    errors = []
    checked = 0
    try:
        calls = list(_find_factory_calls(path))
    except UnparsableFile as exc:
        return 0, [(None, None, None, str(exc))]
    for lineno, module_name, factory_name in calls:
        checked += 1
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            errors.append(
                (lineno, module_name, factory_name, f"module import failed: {exc!r}")
            )
            continue
        if getattr(module, factory_name, _MISSING) is _MISSING:
            errors.append(
                (
                    lineno,
                    module_name,
                    factory_name,
                    f"'{factory_name}' not found in {module_name}",
                )
            )
    return checked, errors


def _default_files():
    bernina_dir = Path(__file__).resolve().parent.parent / "bernina"
    files = []
    main = bernina_dir / "bernina.py"
    if main.exists():
        files.append(main)
    files.extend(sorted(bernina_dir.glob("bernina_*.py")))
    return files


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    files = [Path(p) for p in argv] if argv else _default_files()
    if not files:
        print("No files to check.")
        return 0

    total_checked = 0
    total_errors = []
    for path in files:
        checked, errors = validate_file(path)
        total_checked += checked
        for lineno, module_name, factory_name, msg in errors:
            total_errors.append((path, lineno, module_name, factory_name, msg))

    print(f"Checked {total_checked} module_name= factory reference(s) across {len(files)} file(s).")
    if not total_errors:
        print("All resolved cleanly.")
        return 0

    print(f"\n{len(total_errors)} problem(s) found:\n")
    for path, lineno, module_name, factory_name, msg in total_errors:
        if lineno is None:
            print(f"  {path}")
            print(f"      -> {msg}")
            continue
        print(f"  {path}:{lineno}  \"{factory_name}\" module_name=\"{module_name}\"")
        print(f"      -> {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
