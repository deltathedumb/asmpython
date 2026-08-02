"""Diagnostic audits: passes that inspect and report rather than transform.

Grouped out of the flat `_compiler/` namespace because they share a shape --
each walks the program looking for one pattern and records what it finds --
and none of them is part of the compile path proper.
"""
