# Planned backend scaffolds

ASMPython exposes placeholders for planned code-generation targets before their
implementations land. These entries are **scaffolds only**: selecting one is
expected to raise `NotImplementedError`, and none of them count toward platform,
compatibility, or production-backend claims.

## Canonical backend names

```text
x86
arm
thumb
riscv
mips
powerpc
avr
8051
pic
xtensa
6502
z80
jvm
python-bytecode
webassembly
beam
spirv
ebpf
cuda
amdgpu
glsl
hlsl
wgsl
metal
verilog
systemverilog
vhdl
```

Convenience aliases include `jar` for `jvm`, `pyc` for `python-bytecode`, `wasm`
for `webassembly`, `SPIR-V` for `spirv`, `bpf` for `ebpf`, and common display-name
capitalizations.

## Behavior

A simple build such as:

```text
asmpython build app.py --backend jvm --no-pyinbin-fallback
```

may complete normal source parsing, semantic analysis, and IR lowering before it
reaches the backend. Once the scaffold is invoked, compilation and linking fail
with a message identifying the selected backend and unimplemented operation.
The `--no-pyinbin-fallback` flag is shown deliberately: normal ASMPython builds
may otherwise execute through PyinBin after any native backend reports
`NotImplementedError`.

Scaffold objects additionally expose hard-fail placeholders for future IR
validation, object emission, source emission, and artifact packaging APIs.

## Replacing a scaffold

The registry intentionally permits a real backend to register under an existing
scaffold's canonical name:

```python
import asmpython

asmpython.backend.Backend(name="jvm", impl=real_jvm_backend)
```

The real implementation replaces the placeholder while preserving the public
`--backend jvm` name. Aliases continue to resolve through the canonical entry.

## Planned parameters

Each scaffold stores its planned option names as metadata, such as `--64bits`,
`--two`, `--java-version`, `--class-version`, `--nopack`, shader stages, GPU
architectures, and HDL top/clock/reset settings. These options are not registered
with the CLI yet; they become active only when the corresponding implementation
and validation rules exist.

The current production and experimental backends remain separate:

- `x86-64`: production/reference SSA backend
- `legacy`: original NASM-text pipeline
- `ternary`: existing experimental backend
- ARM64 work under `asmpython/_backends/arm64/`: gated implementation work, not
  replaced or advertised by the broad `arm` scaffold
