"""asmpython -- a retargetable compiler.

    asmpython.diagnostics   spans, structured diagnostics, rendering
    asmpython.ir            the language-independent IR
    asmpython.passes        IR -> IR transforms
    asmpython.frontend(s)   source -> IR
    asmpython.backend(s)    IR -> artifacts
    asmpython.driver        orchestration and the command line

The IR is the contract between the two halves. It knows nothing about any
source language and nothing about any machine; frontends lower INTO it and
backends emit FROM it, and neither reaches past it.
"""
__version__ = "0.1.0"
