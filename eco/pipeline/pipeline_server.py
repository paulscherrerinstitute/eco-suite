"""Interact with the SwissFEL pipeline server (``cam_server``).

The pipeline server can run **user-supplied Python code** on a stream close to
its source. That makes it possible to *offload* heavy per-shot calculations
(for example on beam-synchronous / BS channels) off an analysis client and onto
the server itself. This module wraps :class:`cam_server.PipelineClient` with a
small, shell-friendly API for that workflow:

* manage server-side user scripts (list / read / upload / delete);
* attach a script as the processing *function* of a running pipeline instance
  (the server then hot-reloads and runs it on every message);
* create a "custom" pipeline instance driven entirely by a user script;
* upload a self-contained Python function straight from the interactive shell.

Two processing-function contracts exist, depending on the pipeline type
(see :data:`PROCESS_IMAGE_TEMPLATE` and :data:`CUSTOM_PROCESS_TEMPLATE`):

``processing`` pipeline (camera image + optional BS data)::

    def process_image(image, pulse_id, timestamp, x_axis, y_axis, parameters, bsdata=None):
        return {"my_result": float(image.sum())}

``custom`` pipeline (arbitrary streaming source, e.g. BS channels)::

    def process(parameters, init=False):
        stream_data = {"my_result": 42.0}
        return stream_data, timestamp, pulse_id, data_size

.. note::
   This subpackage is experimental and kept separate from the main eco device
   tree. Uploaded scripts must be **self-contained** (do their own imports at
   module top level) — the server executes the file on its own.

Quick start::

    from eco.pipeline import PipelineServer
    ps = PipelineServer()

    # Write a self-contained processing script and attach it to a running
    # instance (uploads the file and hot-reloads the instance):
    ps.set_function("SARFE10-PSSS059_sp1", "/sf/bernina/.../my_proc.py")

    # Or offload a self-contained function defined in the shell/notebook:
    def process(parameters, init=False):
        import time
        return {"answer": 42.0}, time.time(), None, 1
    ps.offload_function("my_custom_instance", process)
"""

import inspect
import logging
import os
import textwrap

_logger = logging.getLogger(__name__)


PROCESS_IMAGE_TEMPLATE = '''\
"""Pipeline-server processing script for a "processing" (image) pipeline.

Runs on the server for every camera frame. Must be self-contained: put every
import you need at the top of this file. Return a dict of results; each entry
becomes a channel on the pipeline output stream.
"""
import numpy as np


def process_image(image, pulse_id, timestamp, x_axis, y_axis, parameters, bsdata=None):
    # `image` is the (background-subtracted) camera frame as a numpy array.
    # `parameters` is the live pipeline config dict.
    # `bsdata`, when the pipeline is configured with additional BS channels, is
    # a dict of {channel_name: value} synchronous with this frame.
    intensity = float(np.asarray(image).sum())
    return {
        "my_intensity": intensity,
    }
'''


CUSTOM_PROCESS_TEMPLATE = '''\
"""Pipeline-server processing script for a "custom" pipeline.

The server calls `process` repeatedly. Must be self-contained. Return a tuple
of (stream_data, timestamp, pulse_id, data_size), where `stream_data` is a dict
(or OrderedDict) of {channel_name: value}.
"""
import time


def process(parameters, init=False):
    # `init` is True on the first call, so you can set up state once.
    # `parameters` is the live pipeline config dict.
    value = 42.0
    stream_data = {"my_result": value}
    timestamp = time.time()
    pulse_id = None
    data_size = 1
    return stream_data, timestamp, pulse_id, data_size
'''


def get_pipeline_client():
    """Return a shared :class:`cam_server.PipelineClient` (created lazily).

    Imported lazily so that merely importing this module does not require
    ``cam_server`` or open a connection.
    """
    global _PIPELINE_CLIENT
    try:
        _PIPELINE_CLIENT
    except NameError:
        _PIPELINE_CLIENT = None
    if _PIPELINE_CLIENT is None:
        from cam_server import PipelineClient

        _PIPELINE_CLIENT = PipelineClient()
    return _PIPELINE_CLIENT


class PipelineServer:
    """Ergonomic wrapper around ``cam_server.PipelineClient`` for code offload.

    Parameters
    ----------
    client :
        An existing ``PipelineClient`` to use. If ``None`` (default), a shared
        client is created lazily via :func:`get_pipeline_client`.
    """

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        """The underlying ``cam_server.PipelineClient``."""
        if self._client is None:
            self._client = get_pipeline_client()
        return self._client

    # -- server-side user scripts -------------------------------------------

    def list_scripts(self):
        """Names of the user scripts currently stored on the server."""
        return self.client.get_user_scripts()

    def read_script(self, script_name):
        """Return the source of a server-side user script as a string."""
        return self.client.get_user_script(script_name)

    def upload_script(self, filename):
        """Upload a local ``.py`` file to the server as a user script.

        Returns the script name (the file's basename) under which it is stored.
        """
        self.client.upload_user_script(filename)
        return os.path.basename(filename)

    def upload_script_source(self, script_name, source):
        """Upload script *source* (a string) under *script_name* on the server."""
        if not script_name.endswith(".py"):
            script_name += ".py"
        self.client.set_user_script(script_name, source)
        return script_name

    def delete_script(self, script_name):
        """Delete a server-side user script."""
        return self.client.delete_script(script_name)

    # -- attaching scripts to instances -------------------------------------

    def set_function(self, instance_id, filename):
        """Upload *filename* and set it as the processing function of an instance.

        Wraps ``PipelineClient.set_function_script``: uploads the file, then sets
        ``function=<name>`` and ``reload=True`` on the running instance so the
        server hot-reloads and starts running the new code. Returns the script
        name used.
        """
        return self.client.set_function_script(instance_id, filename)

    def offload_function(self, instance_id, func, script_name=None):
        """Upload a *self-contained* Python function and run it on an instance.

        Extracts the source of ``func`` (which must be defined at module/shell
        top level and carry any imports it needs *inside its own body* or in the
        uploaded file), uploads it, and attaches it as the instance's processing
        function. ``func`` must be named ``process`` or ``process_image`` to
        match a pipeline-server contract.

        This is a convenience for quick, self-contained functions. For anything
        needing module-level imports or helpers, write a ``.py`` file and use
        :meth:`set_function` instead.
        """
        source = textwrap.dedent(inspect.getsource(func))
        name = func.__name__
        if name not in ("process", "process_image"):
            raise ValueError(
                "offloaded function must be named 'process' (custom pipeline) or "
                "'process_image' (processing pipeline), got %r" % name
            )
        script_name = script_name or (name + ".py")
        script_name = self.upload_script_source(script_name, source)
        # Point the running instance at the uploaded script and hot-reload it.
        self.client.set_instance_config(
            instance_id, {"function": script_name, "reload": True}
        )
        return script_name

    def reload_instance(self, instance_id):
        """Ask an instance to hot-reload its current function script."""
        return self.client.set_instance_config(instance_id, {"reload": True})

    # -- custom pipelines ---------------------------------------------------

    def create_custom_pipeline(
        self, script, instance_id=None, additional_config=None
    ):
        """Create a ``custom`` pipeline instance driven by a user *script*.

        Parameters
        ----------
        script :
            Either a path to a local ``.py`` file (it is uploaded) or the name of
            a script already on the server.
        instance_id :
            Optional explicit instance id. If ``None``, the server assigns one.
        additional_config :
            Extra pipeline-config keys merged into the created instance (e.g.
            input stream / BS channel configuration).

        Returns
        -------
        (instance_id, stream_address)
            As returned by ``PipelineClient.create_instance_from_config``.
        """
        if os.path.exists(script):
            # A local file: upload it and use its basename as the script name.
            script_name = self.upload_script(script)
        else:
            # Otherwise treat it as the name of a script already on the server.
            script_name = script
        config = {"pipeline_type": "custom", "function": script_name}
        if additional_config:
            config.update(additional_config)
        return self.client.create_instance_from_config(
            config, instance_id=instance_id
        )

    # -- instance introspection ---------------------------------------------

    def instance_info(self, instance_id):
        """Return the server's info dict for a running instance."""
        return self.client.get_instance_info(instance_id)

    def instance_config(self, instance_id):
        """Return the live configuration of a running instance."""
        return self.client.get_instance_config(instance_id)

    def stream_address(self, instance_id):
        """Return the output stream address of a running instance."""
        return self.client.get_instance_stream(instance_id)

    def is_running(self, instance_id):
        """Whether the given instance is currently running on the server."""
        return self.client.is_instance_running(instance_id)

    def stop(self, instance_id):
        """Stop a running instance."""
        return self.client.stop_instance(instance_id)
