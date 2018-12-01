[![Build Status](https://travis-ci.org/paulscherrerinstitute/eco.svg?branch=master)](https://travis-ci.org/paulscherrerinstitute/eco)
                                                             
                          ___ _______
                         / -_) __/ _ \ 
    Experiment Control   \__/\__/\___/

# Experiment Control
eco is a python based control environment for experiments, developed and used at SwissFEL, PSI.
It is supposed to be used as 
- library of experimental devices for higher level python applications or GUIs
- interactive command line interface from e.g. ipython/jupyter shell or notebook

Eco follows an object oriented approach to represent devices which can be passed around as a compatibility layer in python, This should facilitate to combine devices in general control and acquisition routines as well as to develop experimental routines which take advantage of the constantly growing landscape of scientific python libraries.
Examples for such object representation will follow in the documantation, for object-oriented programing in python also checkout online documentation like this [short introduction]{https://realpython.com/python3-object-oriented-programming/}.

## eco Elements
Eco consists in general terms of
1. conventions and examples for the behavior of general objects that allow to use them for different purposes.
2. library modules for broadly used devices using protocols like epics.
3. library modules for more specific, facility-dependent devices or logical assemblies of devices.
4. scopes of specific configurations of devices and scope-specific code, usable e.g. in interactive mode.

## Package Structure
eco consists of a hierachy of mutiple python modules.

At top level should be found:
- utilities (basic and convention helpers)
- basic devices
- examples
-- convention checkers
-- utilities

- specific types of devices
-- general definition of potentially recurring devices
- configurations of multiple devices into instruments


[Device representation.pdf](https://github.com/paulscherrerinstitute/eco/files/2453401/Device.representation.pdf)

# Installation

## Anaconda

The eco package is available on [anaconda.org](https://anaconda.org/paulscherrerinstitute/eco) and can be installed as follows:

```bash
conda install -c paulscherrerinstitute bsread
```
