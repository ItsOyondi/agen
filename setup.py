"""Builds the optional C++ backend (agen._native). Metadata lives in pyproject.toml.

Without a C++ compiler the build is skipped and Agen runs on the pure Python
interpreter, so installation never fails because of the extension.
"""

import sys

from setuptools import Extension, setup

if sys.platform == "win32":
    compile_args = ["/O2", "/std:c++17", "/EHsc", "/W3"]
else:
    compile_args = ["-O3", "-std=c++17", "-fvisibility=hidden"]

setup(
    ext_modules=[
        Extension(
            "agen._native",
            sources=["native/vm.cpp"],
            language="c++",
            extra_compile_args=compile_args,
            optional=True,
        )
    ]
)
