API reference
=============

The pages below are generated directly from the docstrings in the source, so
improving a docstring immediately improves this reference. Only a curated set of
modules is wired up so far — the ones the concepts and examples build on. Extend
the ``automodule`` list as more of eco grows proper docstrings.

.. TODO(eco docs): the sections below for eco.elements.assembly, eco.epics_utils.monitor,
   eco.dbase.archiver, eco.pipeline.pipeline_server, eco.motion.deltatau_config
   (+ its submodules), and eco.devices_general.newport_xps/schneider_mcode
   currently render empty. The build (see docs/conf.py's autodoc_mock_imports)
   fails to import them with "TypeError: unsupported operand type(s) for |:
   'Timestamp' and 'type'". This is not an eager-annotation issue (confirmed:
   adding `from __future__ import annotations` does not change it), so it likely
   originates inside the real (unmocked) `escape` package reacting to a mocked
   type during the autodoc import. Root-causing it needs an interactive debug
   session importing eco directly, which risks opening live EPICS connections
   outside a controlled environment - deliberately left for a maintainer to
   investigate on a machine where that is safe, rather than guessed at here.

Core object protocols
----------------------

.. automodule:: eco.elements.protocols
   :members:

Assembly
--------

.. automodule:: eco.elements.assembly
   :members:
   :exclude-members: NumpyEncoder

Detector elements
-----------------

.. autoclass:: eco.elements.detector.DetectorGet
   :members:

Adjustable elements
-------------------

.. autoclass:: eco.elements.adjustable.AdjustableGetSet
   :members:

Listening monitor
-----------------

.. automodule:: eco.epics_utils.monitor
   :members:

Archiver / DataHub
------------------

.. automodule:: eco.dbase.archiver
   :members:

Pipeline server (offloading code)
---------------------------------

.. automodule:: eco.pipeline.pipeline_server
   :members:

Delta Tau / PowerBrick configs
------------------------------

.. automodule:: eco.motion.deltatau_config
   :members:

.. automodule:: eco.motion.deltatau_config.cfgfile
   :members:

.. automodule:: eco.motion.deltatau_config.bundle
   :members:

.. automodule:: eco.motion.deltatau_config.deploy
   :members:

.. automodule:: eco.motion.deltatau_config.verify
   :members:

.. automodule:: eco.motion.deltatau_config.module_ioc
   :members:

PowerBrick parameters
---------------------

.. automodule:: eco.devices_general.powerbrick
   :members: PowerBrickChannelPars, unlock, lock, gateway_open, axis_from_out, is_powerbrick_motor, ioc_host

Direct motor drivers (EPICS-independent)
----------------------------------------

.. automodule:: eco.devices_general.newport_xps
   :members:

.. automodule:: eco.devices_general.schneider_mcode
   :members:

Simulated scan targets
----------------------

.. automodule:: eco.devices_general.simulated_signals
   :members: SimulatedSignals, SimAxis, SimDetector, SimBeam

Ask eco
-------

.. autofunction:: eco.ei.ask

.. autofunction:: eco.ei.assistant

.. autofunction:: eco.ei.time_window

.. autofunction:: eco.ei.load_config

.. automodule:: eco.logs_elog
   :members: Reader, update, ca_bundle, parse_export

.. automodule:: eco.ei_check
   :members: check, check_answer, build, Problem, static_namespace

.. automodule:: eco.ei_logcheck
   :members: code_lines, Checker, check_index, report, Finding

.. automodule:: eco.ei_tools
   :members: Toolbox, describe

.. automodule:: eco.utilities.storage_locations
   :members: where, contents, check, load, Storage, Item, Location

.. automodule:: eco.ei_page
   :members: render, write, open_page, clean_html

.. automodule:: eco.ei_wait
   :members: pick, Waiting, in_terminal
