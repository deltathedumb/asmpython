"""uasm -- a retargetable compiler.

    uasm.diagnostics   spans, structured diagnostics, rendering
    uasm.ir            the language-independent IR
    uasm.passes        IR -> IR transforms
    uasm.frontend(s)   source -> IR
    uasm.backend(s)    IR -> artifacts
    uasm.driver        orchestration and the command line

The IR is the contract between the two halves. It knows nothing about any
source language and nothing about any machine; frontends lower INTO it and
backends emit FROM it, and neither reaches past it.
"""
#: Kept equal to `VERSION` at the repository root, which is what the build
#: reads and therefore what an installed uasm reports. A literal rather
#: than a read of that file, because the file is not inside the package and an
#: installed copy has no repository to look in -- and a literal that can drift
#: is exactly what `test_the_version_is_one_number` exists to catch. It had
#: drifted: this said `0.1.0` while the project called itself 3.14.0-preview.
__version__ = "3.14.0-preview"
