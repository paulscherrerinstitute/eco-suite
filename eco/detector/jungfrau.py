import ast
import importlib.util as ilu
import inspect
import json
import shutil
import subprocess
import textwrap
import time
import types
from tkinter import W

from eco.base.adjustable import Adjustable
from eco.devices_general.therm import ChillerThermotek
from eco.elements.adj_obj import AdjustableObject
from eco.elements.detector import DetectorGet
from eco.detector.detectors_psi import DetectorBsStream
from eco.utilities.datafiles import ensure_dir, ensure_group_writable
from ..elements.adjustable import AdjustableFS, AdjustableVirtual, AdjustableGetSet
from ..epics_utils.adjustable import AdjustablePv
from ..elements.assembly import Assembly
from ..aliases import Alias
from pathlib import Path
from ..elements import memory
from datetime import datetime
import requests


# Modules a custom DAP script is allowed to import, checked (statically, via
# ast) by check_dap_script_imports before anything is uploaded. sf_daq_broker
# execs an uploaded script unsandboxed against live detector data
# (dap.algos.custom.calc_custom -> load_custom -> exec_module, see dap's
# CUSTOM_SCRIPTS.md) -- this is eco's only gate against e.g. "import os"
# ending up in something that then runs unattended on the pipeline. numpy and
# scipy are what CUSTOM_SCRIPTS.md documents as actually available there --
# NOT independently verified against a live dap worker (see
# list_dap_env_packages/check_dap_env_modules below, which check that
# directly and, as of 2026-09, found no "scipy" in either candidate conda
# env). Treat this default as documentation-derived, not confirmed.
DEFAULT_ALLOWED_DAP_MODULES = ("numpy", "scipy", "math")

# Conda envs seen under /sf/jungfrau/applications/miniconda3/envs (only
# reachable from a filesystem with /sf/jungfrau mounted, e.g. a Bernina
# console -- not an arbitrary dev checkout) that could plausibly be what a
# live dap worker actually runs. "dap" is the one dap's own README names
# (`conda activate dap`); "sf-dap" also exists alongside it and was checked
# only because it was there, not because anything documents it as the live
# one. Neither could be confirmed against an actual running worker process
# (no reachable shell on the daq node itself, sf-daq-11.psi.ch, as of
# 2026-09) -- both are also missing packages ("bsread", "logzero",
# "streak_finder") the current dap git source imports, so either may simply
# be stale relative to whatever is really deployed.
DAP_CONDA_ENVS = {
    "dap": Path("/sf/jungfrau/applications/miniconda3/envs/dap/bin/python"),
    "sf-dap": Path("/sf/jungfrau/applications/miniconda3/envs/sf-dap/bin/python"),
}


def list_dap_env_packages(env="dap"):
    """
    List every package actually installed in a dap conda env, by asking that
    env's own `python -m pip list` -- works without sourcing/activating
    conda, since pip only looks at its own interpreter's site-packages.
    Ground truth for "what can a custom DAP script import", as opposed to
    CUSTOM_SCRIPTS.md's prose or DEFAULT_ALLOWED_DAP_MODULES' assumption
    (see the comment above it).

    `env` is a key into DAP_CONDA_ENVS, or a direct path to a python
    executable.

    Only runs where /sf/jungfrau is mounted (a Bernina console) -- raises
    FileNotFoundError there, not just an empty/wrong result, if it isn't.
    Returns {package_name: version}.
    """
    python = DAP_CONDA_ENVS.get(env, env)
    python = Path(python)
    if not python.exists():
        raise FileNotFoundError(
            f"{python} does not exist -- this only works on a filesystem "
            "with /sf/jungfrau mounted (e.g. a Bernina console), not here"
        )
    proc = subprocess.run(
        [str(python), "-m", "pip", "list", "--format=json"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return {pkg["name"]: pkg["version"] for pkg in json.loads(proc.stdout)}


def check_dap_env_modules(modules=DEFAULT_ALLOWED_DAP_MODULES, env="dap"):
    """
    Actually try `import <module>` in a dap conda env's own interpreter, for
    each of `modules` -- more direct than list_dap_env_packages (a pip
    package name doesn't always match its import name, e.g.
    "jungfrau_utils" vs "jungfrau-utils" on PyPI), and exactly the question
    check_dap_script_imports' allow-list needs answered: "can a script
    running there import this".

    `env` is a key into DAP_CONDA_ENVS, or a direct path to a python
    executable. Only runs where /sf/jungfrau is mounted (a Bernina console)
    -- raises FileNotFoundError there if it isn't, same as
    list_dap_env_packages.

    Returns {module_name: True/False}.
    """
    python = DAP_CONDA_ENVS.get(env, env)
    python = Path(python)
    if not python.exists():
        raise FileNotFoundError(
            f"{python} does not exist -- this only works on a filesystem "
            "with /sf/jungfrau mounted (e.g. a Bernina console), not here"
        )
    results = {}
    for mod in modules:
        proc = subprocess.run(
            [str(python), "-c", f"import {mod}"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        results[mod] = proc.returncode == 0
    return results


def check_dap_script_imports(source, allowed_modules=DEFAULT_ALLOWED_DAP_MODULES):
    """
    Raise ValueError if `source` imports anything outside `allowed_modules`.

    Static (ast-based): it only catches literal `import`/`from ... import`
    statements, not e.g. `importlib.import_module("os")` -- a guardrail
    against honest mistakes, not a sandbox (same spirit as
    eco.elements.access's write-access gate).
    """
    tree = ast.parse(source)
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            used.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            used.add(node.module.split(".")[0])
    disallowed = used - set(allowed_modules)
    if disallowed:
        raise ValueError(
            f"custom DAP script imports disallowed module(s) {sorted(disallowed)} "
            f"-- only {sorted(allowed_modules)} are allowed"
        )
    return used


def _dap_script_source_from_function(func):
    """
    Reconstruct a standalone .py source string for a live function (e.g. one
    just defined at the prompt, or pulled off the eco namespace) so it can be
    uploaded as a custom DAP script -- sf_daq_broker execs the uploaded code
    as its own module server-side, so none of the caller's other imports
    travel with the function automatically. An `import ... as ...` line is
    prepended for every module-valued global the function actually
    references (via `func.__code__.co_names` against `func.__globals__`),
    matching however the caller imported it (`import numpy as np` stays
    `np`).
    """
    src = textwrap.dedent(inspect.getsource(func))

    import_lines = []
    for name in func.__code__.co_names:
        val = func.__globals__.get(name)
        if isinstance(val, types.ModuleType):
            modname = val.__name__
            if modname == name:
                import_lines.append(f"import {modname}")
            else:
                import_lines.append(f"import {modname} as {name}")

    if import_lines:
        return "\n".join(import_lines) + "\n\n" + src
    return src


def _load_dap_script_function_from_file(fpath):
    """
    Load the `proc` (or `<file stem>`-named) function out of a custom DAP
    script file -- same convention as
    slic.core.acquisition.broker.customdap.load_proc_from_file / dap's
    CUSTOM_SCRIPTS.md.
    """
    module_name = fpath.stem
    spec = ilu.spec_from_file_location(module_name, fpath)
    module = ilu.module_from_spec(spec)
    spec.loader.exec_module(module)

    func = getattr(module, "proc", None) or getattr(module, module_name, None)
    if func is None:
        raise AttributeError(
            f'"{fpath}" defines neither a "proc" nor a "{module_name}" function'
        )
    return func


def _test_dap_script_function(func, max_time=0.1):
    """
    Run a candidate custom-DAP-script function once against synthetic
    (meta, image, mask), matching the shape/call convention
    dap.algos.custom.calc_custom actually uses. Mirrors
    slic.core.acquisition.broker.customdap.test_run -- minus its
    LineProfiler-based profiling output, which isn't a dependency of eco --
    refusing anything that mutates its inputs (only the return value is
    allowed to carry the result) or that is too slow to run inline per
    frame.
    """
    import numpy as np

    shape = (1024, 512)
    image = np.random.random(shape)
    mask = image < 0.5
    meta = {}

    orig_meta = meta.copy()
    orig_image = image.copy()
    orig_mask = mask.copy()

    t0 = time.time()
    result = func(meta, image, mask)
    dt = time.time() - t0

    name = getattr(func, "__name__", "custom_dap_script")

    if meta != orig_meta:
        raise RuntimeError(
            f'function "{name}" modifies the metadata dict -- this is not '
            "allowed, return the result(s) instead"
        )
    if not np.array_equal(image, orig_image, equal_nan=True):
        raise RuntimeError(f'function "{name}" modifies the image in place')
    if not np.array_equal(mask, orig_mask, equal_nan=True):
        raise RuntimeError(f'function "{name}" modifies the mask in place')
    if dt > max_time:
        raise RuntimeError(
            f'function "{name}" took {dt:.3g}s on a single test frame -- '
            f"this is too slow to run inline per frame (limit {max_time}s)"
        )
    return result


class JungfrauChannel(Assembly):
    def __init__(
        self,
        jf_id,
        name=None,
    ):
        super().__init__(name=name)
        self.alias = Alias(name, channel=jf_id, channeltype="JF")


class Jungfrau(Assembly):
    def __init__(
        self,
        jf_id,
        pv_trigger="SAR-CVME-TIFALL5-EVG0:SoftEvt-EvtCode-SP",
        trigger_on=254,
        trigger_off=255,
        broker_address="http://sf-daq:10002",
        broker_address_aux="http://sf-daq:10003",
        pgroup_adj=None,
        config_adj=None,
        chiller_thermotek="SARES20-CHIL",
        event_master=None,
        detectors_event_code=None,
        name=None,
    ):
        super().__init__(name=name)
        # self.alias = Alias(name, channel=jf_id, channeltype="JF")
        self._event_master = event_master
        self._detectors_event_code = detectors_event_code
        self.pgroup = pgroup_adj
        self.jf_id = jf_id
        self.broker_address = broker_address
        self.broker_address_aux = broker_address_aux
        self._append(
            DetectorGet, lambda: f"http://{self.get_vis_url()}", name="visulization_url"
        )
        self._append(JungfrauChannel, jf_id, name="data")
        self._append(JungfrauChannel, jf_id + "_rawdata", name="data_raw")
        self._append(
            JungfrauChannel, jf_id + "_dap_col4", name="data_online_processing"
        )
        for n in range(10):
            self._append(
                JungfrauChannel,
                jf_id + f"_dap_col{n+4}",
                name=f"data_online_processing_roi{n}",
                is_display=False,
            )
        self._append(
            JungfrauChannel, jf_id + "_dap_col3", name="ppref_online_processing"
        )
        self._append(
            DetectorBsStream,
            f"{jf_id}:roi_intensities",
            cachannel=None,
            name="intensity_roi",
            optional=True,
        )
        self._append(
            AdjustablePv,
            pv_trigger,
            is_display=True,
            is_setting=False,
            name="trigger",
        )
        self._trigger_on = trigger_on
        self._trigger_off = trigger_off
        self._append(
            AdjustableVirtual,
            [self.trigger],
            lambda value: value == self._trigger_on,
            self._set_trigger_enable,
            name="trigger_enable",
            append_aliases=False,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_pedestal_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="pedestal_file",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_gain_filename,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="gain_file",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_pedestal_filename_in_run,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="pedestal_file_in_run",
            is_display=True,
        )
        self._append(
            AdjustableGetSet,
            self.get_present_gain_filename_in_run,
            lambda value: NotImplementedError(
                "Can not set the pedestal file manually yet."
            ),
            name="gain_file_in_run",
            is_display=True,
        )
        self._last_dap_req_time = 0
        self._append(
            AdjustableFS,
            f"/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/dap_settings_{self.jf_id:s}.json",
            name="_dap_settings_storage",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableGetSet,
            self.get_dap_settings,
            self.set_dap_settings,
            name="_dap_settings",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableObject,
            self._dap_settings,
            is_setting_children=True,
            name="settings_dap",
        )

        # Hardware settings (delay/detector_mode/exptime/gain_mode) served by
        # sf_daq_broker's "slow" broker, at broker_address_aux -- same
        # request/caching pattern as _dap_settings above.
        self._last_detector_settings_req_time = 0
        self._append(
            AdjustableFS,
            f"/sf/bernina/code/gac-bernina/eco_cnf_bernina/reference_values/detector_settings_{self.jf_id:s}.json",
            name="_detector_settings_storage",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableGetSet,
            self.get_detector_settings,
            self.set_detector_settings,
            name="_detector_settings",
            is_display=False,
            is_setting=False,
        )
        self._append(
            AdjustableObject,
            self._detector_settings,
            is_setting_children=True,
            name="settings_detector",
        )

        if config_adj:
            self._append(
                JungfrauDaqConfig,
                jf_id,
                config_adj,
                name="config_daq",
                is_setting=True,
                is_status=True,
                is_display="recursive",
            )
        if chiller_thermotek:
            self._append(
                ChillerThermotek,
                pvbase=chiller_thermotek,
                name="chiller",
                is_display="recursive",
            )

    def set_dap_rois(self, *rois):
        tmp = self.settings_dap._base_dict()
        tmp["roi_x1"] = [roi[0] for roi in rois if roi]
        tmp["roi_x2"] = [roi[1] for roi in rois if roi]
        tmp["roi_y1"] = [roi[2] for roi in rois if roi]
        tmp["roi_y2"] = [roi[3] for roi in rois if roi]
        self.settings_dap._base_dict(tmp)

    def _set_trigger_enable(self, value):
        if value:
            self.trigger.set_target_value(self._trigger_on).wait()
        else:
            self.trigger.set_target_value(self._trigger_off).wait()

    def get_present_gain_filename(self):
        filepath = Path(f"/sf/jungfrau/config/gainMaps/{self.jf_id}/gains.h5")

        if filepath.exists():
            return filepath.as_posix()
        else:
            raise Exception(f"File {filepath.as_posix()} seems not to exist!")

    def get_present_gain_filename_in_run(self, intempdir=False):
        f = Path(self.get_present_gain_filename())
        dest = Path(
            f"/sf/bernina/data/{self.pgroup()}/res/tmp/gainmaps_{self.jf_id}.h5"
        )

        try:
            if not dest.exists():
                ensure_dir(dest.parent)
                shutil.copyfile(f, dest)
                # copyfile creates with the umask (0o644): the copy has to be
                # rewritable by the rest of the pgroup too.
                ensure_group_writable(dest)

        except PermissionError:
            return "No permissions to res directory!"

        if intempdir:
            return dest.as_posix()
        else:
            return f"aux/{dest.name}"

    def get_present_pedestal_filename(self):
        searchpath = Path(f"/sf/jungfrau/data/pedestal/{self.jf_id}")
        filelist = list(searchpath.glob("*.h5"))
        times = [datetime.strptime(f.stem, "%Y%m%d_%H%M%S") for f in filelist]
        if len(times) == 0:
            return ''
        else:
            return filelist[times.index(max(times))].as_posix()

    def get_present_pedestal_filename_in_run(self, intempdir=False):
        f = Path(self.get_present_pedestal_filename())
        dest = Path(
            f"/sf/bernina/data/{self.pgroup()}/res/tmp/pedestal_{self.jf_id}_{f.stem}.h5"
        )
        try:
            if not dest.exists():
                ensure_dir(dest.parent)
                shutil.copyfile(f, dest)
                # copyfile creates with the umask (0o644): the copy has to be
                # rewritable by the rest of the pgroup too.
                ensure_group_writable(dest)
        except PermissionError:
            return "No poermissions to res directory!"

        if intempdir:
            return dest.as_posix()
        else:
            return f"aux/{dest.name}"

    def get_dap_settings(self, force=False):
        """
        Read this detector's DAP (online analysis pipeline) parameters.

        REST: GET {broker_address_aux}/get_dap_settings. broker_address_aux
        is sf_daq_broker's "slow" broker (DEFAULT_BROKER_SLOW_REST_PORT,
        served by broker_slow.py) despite the "aux" name used in eco --
        confirmed against sf_daq_broker/config.py, where both
        eco.acquisition.daq_client.Daq.broker_address_aux and this class's
        broker_address_aux default to port 10003, matching
        DEFAULT_BROKER_SLOW_REST_PORT exactly. It reads
        pipeline_parameters.{detector_name}.json off GPFS server-side.

        force=False (default) returns the last cached value from
        _dap_settings_storage without touching the network. force=True
        re-queries the broker, throttled to once per 5 s via
        _last_dap_req_time -- because the slow broker is single-threaded
        (bottle's default wsgiref server handles one HTTP request at a time,
        broker process-wide), so hammering this stalls every other client of
        broker_address_aux for as long as each call takes. get_detector_settings
        below follows the same pattern for the same reason.
        """
        if force:
            if 5 < (time.time() - self._last_dap_req_time):
                self._last_dap_message = requests.get(
                    f"{self.broker_address_aux}/get_dap_settings",
                    json={"detector_name": self.jf_id},
                ).json()
                self._last_dap_req_time = time.time()

            if self._last_dap_message["status"] == "ok":
                self._dap_settings_storage.set_target_value(
                    self._last_dap_message["parameters"]
                ).wait()
                return self._last_dap_message["parameters"]
        else:
            val = self._dap_settings_storage.get_current_value()
            if not val:
                val = self.get_dap_settings(force=True)
            return val

    def set_dap_settings(self, dap_setting_dict):
        """
        Change this detector's DAP parameters.

        REST: POST {broker_address_aux}/set_dap_settings, body
        {"detector_name": jf_id, "parameters": dap_setting_dict}. The
        broker diffs against the current file, keeps a timestamped backup,
        and rolls back automatically if the write fails.
        """
        m = requests.post(
            f"{self.broker_address_aux}/set_dap_settings",
            json={"detector_name": self.jf_id, "parameters": dap_setting_dict},
        ).json()
        if m["status"] == "ok":
            self._dap_settings_storage.set_target_value(dap_setting_dict).wait()
            return m

    def upload_custom_dap_script(
        self,
        source,
        name=None,
        allowed_modules=DEFAULT_ALLOWED_DAP_MODULES,
        max_time=0.1,
    ):
        """
        Upload a custom online-analysis script to run on this detector's
        stream, via sf_daq_broker's slow-broker "upload_custom_dap_script"
        endpoint (same one slic.core.acquisition.broker.customdap /
        BrokerClient.upload_custom_dap_script use -- see dap's
        CUSTOM_SCRIPTS.md for the server-side contract).

        `source` is either:
          - a path (str/Path) to an existing .py file defining a
            `proc(meta, image, mask)` function (or one named after the
            file's stem) -- same file convention slic uses; or
          - a live function already defined in the caller's namespace
            (e.g. written at the prompt, or pulled off a namespace
            component) with that same `(meta, image, mask) -> result`
            signature. Its source is reconstructed into a standalone
            module via `_dap_script_source_from_function` so it can be
            exec'd server-side on its own -- see that function's
            docstring for what it can and can't pick up automatically.

        Before anything is sent, the resulting source is (1) checked via
        `check_dap_script_imports` to only import modules in
        `allowed_modules` -- sf_daq_broker execs custom scripts
        unsandboxed against live detector data, so this is the only gate
        against e.g. "import os" landing in something that then runs
        unattended on the pipeline -- and (2) actually run once against
        synthetic (meta, image, mask) via `_test_dap_script_function`,
        which refuses anything that mutates its inputs or runs too slowly
        to do inline per frame.

        `result` may be a single value (uploaded as channel
        "{jf_id}:{name}") or a dict (one channel per key) -- see dap's
        CUSTOM_SCRIPTS.md. Once uploaded, enable it with e.g.
        `self.set_dap_settings({"custom_script": f"<beamline>:{name}"})`.

        Keeping state across pulses: the uploaded script is NOT re-imported
        every frame. dap.algos.custom.load_custom(script) (server-side) is
        `@functools.cache`d, keyed on the "beamline:name" string -- the
        first call execs the module and the very same function object is
        then reused for every later frame that worker process handles,
        so ordinary Python persistence works: a module-level global mutated
        via `global` inside your function, an object built once at module
        import time and captured in a closure, or even an attribute set on
        the function itself (`proc.count = ...`) will all carry over
        pulse-to-pulse for as long as that worker process runs. (The
        `@cooldown(60)` wrapping it only throttles retries after a load
        *failure* -- a successful load is cached indefinitely, not just for
        60s -- which is also why disable_custom_script()'s docstring warns
        that re-uploading a fix under the same name won't reach an
        already-running worker.)

        Two caveats on relying on that for real accumulation, from reading
        dap's source (not confirmed against a live worker -- see
        list_dap_env_packages/check_dap_env_modules above for the same
        access limitation):
          - dap's workers are horizontally scaled: each one's
            dap.zmqsocks.ZMQSocketsWorker.backend_socket is a ZeroMQ PULL
            socket connected to a shared PUSH backend -- the standard
            fair-queued *competing consumers* pattern. If more than one
            worker process is handling this detector's stream, consecutive
            pulses are round-robined across independent processes, each
            with its own separate copy of any module-level state -- so a
            naive `count += 1` global would only count the fraction of
            pulses that particular process happened to receive, silently.
            Nothing found in dap/sf_daq_broker/slic reveals how many worker
            processes are actually configured per detector.
          - State is lost on a worker restart, same caching mechanism, same
            unknown restart cadence noted in disable_custom_script().
        """
        if callable(source):
            func = source
            name = name or getattr(func, "__name__", None)
            if not name:
                raise ValueError("name is required for this source")
            code = _dap_script_source_from_function(func)
        else:
            fpath = Path(source)
            if not fpath.is_file():
                raise TypeError(
                    "source must be a path to an existing .py file or a "
                    f"callable (meta, image, mask) -> result function, got {source!r}"
                )
            name = name or fpath.stem
            code = fpath.read_text()
            func = _load_dap_script_function_from_file(fpath)

        check_dap_script_imports(code, allowed_modules=allowed_modules)
        _test_dap_script_function(func, max_time=max_time)

        m = requests.post(
            f"{self.broker_address_aux}/upload_custom_dap_script",
            json={"name": name, "code": code},
        ).json()
        return m

    def get_active_custom_script(self, force=True):
        """
        Return the "beamline:name" of the custom DAP script currently
        applied to this detector's live stream, or None if none is active.

        This is the "custom_script" key of get_dap_settings()'s parameters
        dict -- i.e. the same pipeline_parameters.{jf_id}.json file the
        running dap worker itself re-reads every frame
        (dap.worker.work()'s `config = config_file.load()`, a BufferedJSON
        that is mtime-cached for at most 2s) and merges into the per-frame
        `results` dict that dap.algos.custom.calc_custom reads
        "custom_script" off of. So what this returns really is "what's
        running now", with at most ~2s of staleness -- not merely "what was
        last requested".

        Unlike get_dap_settings() itself, this defaults to force=True: the
        whole point of calling this is to know the live state, and the
        cached _dap_settings_storage value (force=False) can be arbitrarily
        stale, e.g. it never reflects a change made by someone else's
        session, or via the raw REST endpoint directly.
        """
        parameters = self.get_dap_settings(force=force) or {}
        return parameters.get("custom_script") or None

    def disable_custom_script(self):
        """
        Turn off whichever custom DAP script is currently active on this
        detector (calc_custom: `if not script: return`), reverting to
        eco/dap's normal built-in analysis chain. Equivalent to
        `self.set_dap_settings({"custom_script": None})`.

        Read this before relying on it as an "undo" for
        upload_custom_dap_script -- it is *not* a full undo:

        - It only clears which script is selected, not the script file
          itself. The file previously uploaded under that name stays on
          GPFS (it is written into a git-tracked "custom_dap_scripts" repo
          server-side and committed -- see upload_custom_dap_script's
          docstring), recoverable only via git access to that repo, which
          neither eco nor slic exposes over the REST API.
        - It does not restore whatever custom_script value (if any) was
          active *before* the one you are disabling. If you need to go
          back to a specific prior script rather than "none", capture it
          yourself first via get_active_custom_script() and pass it to
          set_dap_settings() explicitly.
        - Re-enabling a script by name later does not guarantee you get
          the file's current content: dap.algos.custom.load_custom (the
          server-side loader) is functools.cache'd per detector's dap
          worker process, keyed on the "beamline:name" string, with no
          cache invalidation on re-upload. A worker that has already
          successfully loaded a given name once keeps using that cached
          function indefinitely, even across this disable/re-enable, until
          that worker process itself restarts -- eco/dap/slic's source
          gave no visibility into when/how often that happens. If you
          uploaded a fixed version of a script under the *same* name while
          it may have already run, disabling and re-enabling it here is
          not sufficient to guarantee the fix is what executes next --
          uploading under a new name (and pointing custom_script at that)
          is the only way from eco to be sure.
        - Applying the change itself is near-immediate (~2s, see
          get_active_custom_script()'s docstring), same as any other
          set_dap_settings() call.
        """
        return self.set_dap_settings({"custom_script": None})

    def get_detector_settings(self, force=False):
        """
        Read this detector's live hardware settings (delay, detector_mode,
        exptime, gain_mode).

        REST: GET {broker_address_aux}/get_detector_settings -- same slow
        broker as get_dap_settings above, same caching/throttling pattern
        and the same reasoning: force=False (default) returns the cached
        value from _detector_settings_storage; force=True re-queries the
        broker, throttled to once per 5 s via _last_detector_settings_req_time.
        """
        if force:
            if 5 < (time.time() - self._last_detector_settings_req_time):
                self._last_detector_settings_message = requests.get(
                    f"{self.broker_address_aux}/get_detector_settings",
                    json={"detector_name": self.jf_id},
                ).json()
                self._last_detector_settings_req_time = time.time()

            if self._last_detector_settings_message["status"] == "ok":
                self._detector_settings_storage.set_target_value(
                    self._last_detector_settings_message["parameters"]
                ).wait()
                return self._last_detector_settings_message["parameters"]
        else:
            val = self._detector_settings_storage.get_current_value()
            if not val:
                val = self.get_detector_settings(force=True)
            return val

    def set_detector_settings(self, detector_setting_dict):
        """
        Change this detector's live hardware settings.

        REST: POST {broker_address_aux}/set_detector_settings, body
        {"detector_name": jf_id, "parameters": detector_setting_dict}. Only
        delay, detector_mode, exptime and gain_mode are recognised
        server-side; anything else in the dict is ignored. The broker stops
        the trigger, applies changes via setattr on its Detector object,
        then restarts the trigger -- i.e. this briefly interrupts triggering
        for this detector. Do not call it mid-acquisition.
        """
        m = requests.post(
            f"{self.broker_address_aux}/set_detector_settings",
            json={"detector_name": self.jf_id, "parameters": detector_setting_dict},
        ).json()
        if m["status"] == "ok":
            self._detector_settings_storage.set_target_value(
                detector_setting_dict
            ).wait()
            return m

    def get_status(self):
        """
        REST: GET {broker_address_aux}/get_detector_status -> the broker's
        Detector.get_status() dict. Single round trip, not cached (unlike
        get_detector_settings/get_dap_settings above).
        """
        return requests.get(
            f"{self.broker_address_aux}/get_detector_status",
            json={"detector_name": self.jf_id},
        ).json()["detector_status"]

    def get_pings(self):
        """
        Per-module network reachability for this detector.

        REST: GET {broker_address_aux}/get_detector_pings ->
        {"responding": [module_numbers], "unreachable": [module_numbers]}.
        Unlike the other diagnostics calls here, this one actually pings
        hardware over the network server-side and can take noticeably
        longer if a module is down -- and because the slow broker handles
        one request at a time (see get_dap_settings docstring), that stalls
        every other client of broker_address_aux for as long as this call
        takes. Don't call it from inside a scan loop.
        """
        return requests.get(
            f"{self.broker_address_aux}/get_detector_pings",
            json={"detector_name": self.jf_id},
        ).json()["pings"]

    def get_temperatures(self):
        """REST: GET {broker_address_aux}/get_detector_temperatures."""
        return requests.get(
            f"{self.broker_address_aux}/get_detector_temperatures",
            json={"detector_name": self.jf_id},
        ).json()["temperatures"]

    def get_stats(self):
        """
        Combined health check: is this detector actually alive and writing.

        REST: GET {broker_address_aux}/get_jfstats (note: the request key is
        "det", not "detector_name", unlike every other slow-broker
        endpoint here) -> {"parameters" (jfctrl monitor), "temperatures",
        "writing": bool}. "writing" reflects whether the detector's buffer
        file on the server was modified within the last 30 s -- of
        everything sf-daq exposes, this is the closest to a direct "is data
        actually flowing right now" signal, as opposed to get_isrunning()
        below, which only checks whether this jf_id is in the broker's
        *configured* running-detectors list, not whether it is actually
        producing data.
        """
        return requests.get(
            f"{self.broker_address_aux}/get_jfstats",
            json={"det": self.jf_id},
        ).json()

    def get_detector_frequency(self):
        return self._event_master.event_codes[
            self._detectors_event_code
        ].frequency.get_current_value()

    def get_availability(self):
        is_available = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_allowed_detectors").json()[
                "detectors"
            ]
        )
        return is_available

    def get_vis_url(self):
        tmp = requests.get(f"{self.broker_address}/get_allowed_detectors").json()
        ix = tmp["detectors"].index(self.jf_id)
        return tmp["visualisation_address"][ix]

    def get_isrunning(self):
        is_running = (
            self.jf_id
            in requests.get(f"{self.broker_address}/get_running_detectors").json()[
                "detectors"
            ]
        )
        return is_running

    def power_on(self):
        JF_channel = self.jf_id
        par = {"detector_name": JF_channel}
        return requests.post(
            f"{self.broker_address}/power_on_detector", json=par
        ).json()

    # def take_pedestal(self, JF_list=None, pgroup=None):
    #     if pgroup is None:
    #         pgroup = self.pgroup
    #     if not JF_list:
    #         JF_list = self.get_JFs_running()
    #     parameters = {
    #         "pgroup": pgroup,
    #         "rate_multiplicator": 1,adc_to_energy
    #         "detectors": {tJF: {} for tJF in JF_list},
    #     }
    #     return requests.post(
    #         f"{self.broker_address}/take_pedestal", json=parameters
    #     ).json()


class JungfrauDaqConfig(Assembly):
    def __init__(self, jf_id, jf_daq_cfg: Adjustable, name=None):
        super().__init__(name=name)
        self._jf_id = jf_id
        self._jf_daq_cfg = jf_daq_cfg
        cfg = self._jf_daq_cfg.get_current_value()
        if self._jf_id not in cfg.keys():
            cfg[self._jf_id] = {}
            self._jf_daq_cfg.set_target_value(cfg).wait()

        self._append(
            AdjustableGetSet,
            self._get_adc_to_energy,
            self._set_adc_to_energy,
            name="convert_adc_to_energy",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_geometry_corr,
            self._set_geometry_corr,
            name="apply_tile_geometry",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_compressed_bitshuffle,
            self._set_compressed_bitshuffle,
            name="compress_bitshuffle",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_rounding_factor,
            self._set_rounding_factor,
            name="rounding_factor_keV",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_large_pixel_processing,
            self._set_large_pixel_processing,
            name="large_pixel_processing",
            is_display=True,
            is_setting=True,
        )

        self._append(
            AdjustableGetSet,
            self._get_disabled_modules,
            self._set_disabled_modules,
            name="disabled_tiles",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_binning,
            self._set_binning,
            name="downsample",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_keep_raw_data,
            self._set_keep_raw_data,
            name="keep_raw_data",
            is_display=True,
            is_setting=True,
        )
        self._append(
            AdjustableGetSet,
            self._get_save_online_processing,
            self._set_save_online_processing,
            name="save_online_processing",
            is_display=True,
            is_setting=True,
        )

    def _get_adc_to_energy(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["adc_to_energy"]
        except KeyError:
            return False

    def _set_adc_to_energy(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["adc_to_energy"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["adc_to_energy"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_geometry_corr(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["geometry"]
        except KeyError:
            return "not sure what happens"

    def _set_geometry_corr(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["geometry"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["geometry"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_compressed_bitshuffle(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["compression"]
        except KeyError:
            return False

    def _set_compressed_bitshuffle(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["compression"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["compression"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_save_online_processing(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["save_dap_results"]
        except KeyError:
            # raise Exception("unclear what the default for keeping raw files is!")
            return None

    def _set_save_online_processing(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["save_dap_results"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["save_dap_results"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_keep_raw_data(self, *args):
        try:
            return not self._jf_daq_cfg.get_current_value()[self._jf_id][
                "remove_raw_files"
            ]
        except KeyError:
            # raise Exception("unclear what the default for keeping raw files is!")
            return None

    def _set_keep_raw_data(self, value):
        if value:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["remove_raw_files"] = False
            self._jf_daq_cfg.set_target_value(cfg).wait()
        else:
            cfg = self._jf_daq_cfg.get_current_value()
            cfg[self._jf_id]["remove_raw_files"] = True
            self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_large_pixel_processing(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id][
                "double_pixels_action"
            ]
        except KeyError:
            # raise Exception("unclear what the default for double pixels is!")
            return None

    def _set_large_pixel_processing(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["double_pixels_action"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_rounding_factor(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["factor"]
        except KeyError:
            # raise Exception("unclear what the default for double pixels is!")
            return None

    def _set_rounding_factor(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["factor"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_disabled_modules(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["disabled_modules"]
        except KeyError:
            return []

    def _set_disabled_modules(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        if value == []:
            cfg[self._jf_id].pop("disabled_modules")
        else:
            cfg[self._jf_id]["disabled_modules"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_binning(self, *args):
        try:
            return self._jf_daq_cfg.get_current_value()[self._jf_id]["downsample"]
        except KeyError:
            return [1, 1]

    def _set_binning(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        if value == [1, 1]:
            cfg[self._jf_id].pop("downsample")
        else:
            cfg[self._jf_id]["downsample"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()

    def _get_keepraw(self, *args):
        try:
            remove_raw = self._jf_daq_cfg.get_current_value()[self._jf_id][
                "remove_raw_files"
            ]
            # if type(remove_raw) is bool:
            return remove_raw

        except KeyError:
            return "not sure what happens"

    def _set_keepraw(self, value):
        cfg = self._jf_daq_cfg.get_current_value()
        cfg[self._jf_id]["remove_raw_files"] = value
        self._jf_daq_cfg.set_target_value(cfg).wait()


#          {
#     "adc_to_energy": true,
#     "compression": true,
#     "double_pixels_actions": "interpolate",
#     "downsample": [
#         1,
#         1
#     ],
#     "factor": 0.25,x
#     "geometry": true,
#     "remove_raw_files": false
#   "disabled_modules": [],
# },
