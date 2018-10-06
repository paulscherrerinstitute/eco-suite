#!/usr/bin/env python


from setuptools import setup, find_packages

VERSION = (0, 0, 1)
VERSION_STR = ".".join([str(x) for x in VERSION])

setup(
    name="eco",
    version=VERSION_STR,
    description="Eco ...",
    long_description="Eco ../",
    author="Paul Scherrer Institute",
    author_email="@psi.ch",
    url="https://github.com/paulscherrerinstitute/eco",
    packages=find_packages(),
    requires=["numpy", "xrayutilities", "xraylib"],
)
