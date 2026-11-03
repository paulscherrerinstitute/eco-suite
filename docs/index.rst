eco — Experiment Control
========================

.. rst-class:: lead

   **eco** is a Python-based control environment for experiments, developed and
   used at SwissFEL, PSI. It provides an object-oriented representation of
   beamline and experiment devices that can be driven interactively from an
   IPython/Jupyter shell or reused as a library by higher-level applications
   and GUIs.

eco follows an object-oriented approach: every device — from a single motor to
a whole instrument — is a Python object with a small, predictable interface.
Because devices share conventions, they can be freely combined in generic
acquisition and scanning routines and analysed with the growing landscape of
scientific Python libraries.

.. grid:: 1 1 2 2
   :gutter: 3

   .. grid-item-card:: :octicon:`rocket` Getting started
      :link: installation
      :link-type: doc

      Install eco and open your first interactive session.

   .. grid-item-card:: :octicon:`megaphone` Did you know?
      :link: did_you_know/index
      :link-type: doc

      Short tips on recent features: one call, what it gains you, and
      where to read more.

   .. grid-item-card:: :octicon:`sparkle-fill` What's new
      :link: whats_new
      :link-type: doc

      Recent changes and new features, and what to watch out for when
      you update.

   .. grid-item-card:: :octicon:`light-bulb` Concepts
      :link: concepts
      :link-type: doc

      Why eco exists, its basic elements (Adjustable, Detector, Assembly)
      and the Bernina namespace.

   .. grid-item-card:: :octicon:`list-ordered` Queues
      :link: queues
      :link-type: doc

      Queue scans and commands with ``%queue``: each one test-run when
      queued, run one after another while the prompt stays free.

   .. grid-item-card:: :octicon:`comment-discussion` Ask eco
      :link: ask_eco
      :link-type: doc

      ``%ei how do I ...?`` answers from the docs, the command history,
      SciLog and the machine elog, with your session's devices.

   .. grid-item-card:: :octicon:`goal` Adaptive scans
      :link: adaptive_scans
      :link-type: doc

      Scans that put the next points where the live data say they help
      most. Try them on the simulated targets in ``sim``.

   .. grid-item-card:: :octicon:`device-mobile` eco on a phone
      :link: web_app
      :link-type: doc

      ``eco web``: the running session in a browser, with live values,
      moves, queues and the cameras.

   .. grid-item-card:: :octicon:`pulse` Listening monitor
      :link: examples/listening_monitor
      :link-type: doc

      Subscribe to a channel and collect its value stream in the background.

   .. grid-item-card:: :octicon:`graph` Archiver → strip chart
      :link: examples/archiver_stripchart
      :link-type: doc

      Pull historical data from the archiver and open a live strip chart.

   .. grid-item-card:: :octicon:`server` Pipeline offload
      :link: examples/pipeline_offload
      :link-type: doc

      Upload your own code to the pipeline server to offload BS-stream
      calculations.

   .. grid-item-card:: :octicon:`gear` Delta Tau configs
      :link: examples/deltatau_config
      :link-type: doc

      Read, edit, apply and verify PowerBrick motor-controller
      configurations.

   .. grid-item-card:: :octicon:`cpu` PowerBrick servo mode
      :link: deltatau_servo
      :link-type: doc

      Servo parameters, their auto-assigned ratios, and how to tune
      them.

   .. grid-item-card:: :octicon:`key` PowerBrick parameters
      :link: powerbrick_parameters
      :link-type: doc

      The controller's own view of a motor (``cnf_pb``), and loading the
      Kappa or SampleStage onto the diffractometer.

   .. grid-item-card:: :octicon:`plug` Direct motor drivers
      :link: examples/direct_motor_drivers
      :link-type: doc

      Talk to Newport XPS and Schneider MCode controllers directly,
      with no EPICS in the loop.

   .. grid-item-card:: :octicon:`device-desktop` Widget views
      :link: widget_views
      :link-type: doc

      The default Adjustable/Detector row in Qt vs ipywidgets, and the
      matched, stock-widget-only LED/gauge/dial "indicator" gadgets on
      both.

   .. grid-item-card:: :octicon:`stack` Widget containers
      :link: widget_containers
      :link-type: doc

      Compose a custom panel by stacking assembly/adjustable/detector/
      viewer widgets, aligned left/center/right, on either front end.


.. admonition:: Did you know?
   :class: tip

   ``%ei what happened to the machine since yesterday?`` reads the SwissFEL
   commissioning elog for you and answers with the entries in order. More
   tips like this: :doc:`did_you_know/index`, or ``features()`` in a session.

.. toctree::
   :maxdepth: 2
   :caption: Getting started
   :hidden:

   installation
   launching
   whats_new
   concepts
   did_you_know/index

.. toctree::
   :maxdepth: 2
   :caption: Using eco
   :hidden:

   scanning
   adaptive_scans
   simulated_targets
   queues
   tweak
   command_history
   ask_eco
   tab_completion
   recspace
   recspace_crystals
   status_server
   run_history
   shared_files

.. toctree::
   :maxdepth: 2
   :caption: Examples
   :hidden:

   examples/listening_monitor
   examples/archiver_stripchart
   examples/pipeline_offload
   examples/deltatau_config
   examples/direct_motor_drivers

.. toctree::
   :maxdepth: 2
   :caption: Hardware background
   :hidden:

   deltatau_servo
   powerbrick_parameters
   channel_access

.. toctree::
   :maxdepth: 2
   :caption: UI
   :hidden:

   widget_views
   widget_containers
   schematic_panels
   live_gui
   web_app
   terminal_plots

.. toctree::
   :maxdepth: 2
   :caption: Development
   :hidden:

   developer_notes
   multidim_scan_data

.. toctree::
   :maxdepth: 2
   :caption: Reference
   :hidden:

   api
