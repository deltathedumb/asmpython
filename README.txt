asmpython Compiler - Standalone Build

RELEASE MILESTONE: 1.0
DEADLINE: by/in March 2027 (see roadmap.md for the compatibility-vs-date risk)
  1.0 requires TWO things:
  (1) the first-party standard library asmpython.libs:
        - asmpython.libs.os       OS features (user-mode)
        - asmpython.libs.net      networking / sockets
        - asmpython.libs.hardware ring-0 / --freestanding driver-grade
                                  hardware (needs a freestanding backend)
  (2) Python language compatibility -- the 99.9% target: idiomatic Python
      compiles or fails with a clear "not implemented" message (never a silent
      miscompile). The excluded 0.1% is interpreter-only (eval, C-API, async,
      generators, metaclasses). NOTE: 99.9% is a multi-year arc and is the main
      tension with the March 2027 date -- a product decision is pending.

PARALLEL TRACK: Self-compilation (asmpython compiling asmpython)
  Not a gate for 1.0; tracked separately. The honest proxy for compatibility.
  See roadmap.md. Front-end gauntlet currently 18/19 (codegen unmeasured).

This repository contains a completely portable build of the asmpython compiler.
See more in about.md

==== FOR WINDOWS ====
Simply use asmpython.bat via the terminal.
Dependencies are downloaded at runtime so no need for extra downloads!

==== FOR LINUX ====
We presume you are smart enough to install Python, GCC, and NASM yourself.
Once dependencies are installed, use asmpython.sh via bash.

==== FOR MAC ====
There is currently no built-in mac support. ¯\_(ツ)_/¯