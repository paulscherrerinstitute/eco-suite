[![Build Status](https://travis-ci.org/paulscherrerinstitute/eco.svg?branch=master)](https://travis-ci.org/paulscherrerinstitute/eco)
                                                             
                          ___ _______
                         / -_) __/ _ \ 
    Experiment Control   \__/\__/\___/

# Experiment Control
Python based control environment for experiments, developed and used at SwissFEL, PSI.
eco is supposed to be used as 
- library of experimental devices for higher level python applications or GUIs
- interactive command line interface from e.g. ipython/jupyter shell or notebook

Eco follows an object oriented approach to represent devices which can be passed around as a compatibility layer in python, This should facilitate to combine devices in general control and acquisition routines as well as to develop experimental routines which take advantage of the constantly growing landscape of scientific python libraries.

## eco Elements
Eco consists in general terms of
1. conventions and examples for the behavior of general objects that allow to use them for different purposes.
2. library modules for broadly used devices using protocols like epics.
3. library modules for more specific, facility-dependent devices or logical assemblies of devices.
4. scopes of specific configurations of devices and scope-specific code, usable e.g. in interactive mode.

## Package Structure
eco consists of mutiple python modules structured in main classes

- basic devices
-- examples
-- convention checkers
-- utilities

- specific types of devices
-- general definition of potentially recurring devices
- configurations of multiple devices into instruments


[Device representation.pdf](https://github.com/paulscherrerinstitute/eco/files/2453401/Device.representation.pdf)

