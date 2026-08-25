"""asmpython's object runtime, written in the machine subset.

NOT AN IMPORTABLE PACKAGE in any useful sense. The modules beside this one are
read as SOURCE and compiled by asmpython's own Python frontend into every
program that needs the object runtime -- see `objects/ir.py`. Importing
one under CPython fails, because `i64` and `ptr` are not names Python has, and
that failure is the honest signal: this is not host code.

The package exists so that the source travels with an installed asmpython.
"""
