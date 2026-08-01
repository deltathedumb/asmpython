"""apc -- a retargetable compiler.

    apc.diagnostics   spans, structured diagnostics, rendering
    apc.ir            the language-independent IR
    apc.passes        IR -> IR transforms
    apc.frontend(s)   source -> IR
    apc.backend(s)    IR -> artifacts
    apc.driver        orchestration and the command line

The IR is the contract between the two halves. It knows nothing about any
source language and nothing about any machine; frontends lower INTO it and
backends emit FROM it, and neither reaches past it.
"""
__version__ = "0.1.0"
