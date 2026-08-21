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

Direct motor drivers (EPICS-independent)
----------------------------------------

.. automodule:: eco.devices_general.newport_xps
   :members:

.. automodule:: eco.devices_general.schneider_mcode
   :members:
