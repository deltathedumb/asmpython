"""asmpython's pluggable linkers.

A linker is a plain module exposing:
  requested_args: list[dict]        (optional -- CLI args it wants, default [])
  def link(ctx: dict) -> bytes       (required -- performs the link, returns
                                       the final output file's bytes)

`ctx` carries everything a linker might need: at minimum `objects` (a list
of object-file bytes to link) and `target_os`; individual linkers read
whatever else they require out of it (e.g. `entry_symbol`, `gcc_path`,
`extra_args`, `out_path`).

Backends pick which linker to use (see asmpython/_backends/x86_64's
default_linker/run_backend_link) -- this package just holds the
implementations, named by how driver.py's --linker flag refers to them.
"""
