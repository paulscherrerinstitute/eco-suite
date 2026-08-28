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

   .. grid-item-card:: :octicon:`light-bulb` Concepts
      :link: concepts
      :link-type: doc

      Why eco exists, its basic elements (Adjustable, Detector, Assembly)
      and the Bernina namespace.

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


.. toctree::
   :maxdepth: 2
   :caption: Getting started
   :hidden:

   installation
   concepts

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

.. toctree::
   :maxdepth: 2
   :caption: UI
   :hidden:

   widget_views
   widget_containers

.. toctree::
   :maxdepth: 2
   :caption: Reference
   :hidden:

   api
