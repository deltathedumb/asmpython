"""JVM backend: a class file, packaged as a runnable jar.

    version.py    which class-file version to write, from --java-version and
                  --class-version
    classfile.py  the container format and the instruction encoder
    runtime.py    memory access and the host functions, as bytecode
    emit.py       the IR translation
"""
from .emit import JvmBackend  # noqa: F401
from .version import ClassVersion, JAVA_TO_CLASS_VERSION, VersionError, resolve

__all__ = ["ClassVersion", "JAVA_TO_CLASS_VERSION", "JvmBackend",
           "VersionError", "resolve"]
