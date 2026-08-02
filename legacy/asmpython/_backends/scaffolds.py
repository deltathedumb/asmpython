"""Catalog and registration for planned, intentionally unimplemented backends."""

from __future__ import annotations

from collections.abc import Callable

from ._scaffold import BackendScaffoldSpec, ScaffoldBackend


SCAFFOLD_BACKEND_SPECS: tuple[BackendScaffoldSpec, ...] = (
    BackendScaffoldSpec(
        "x86", "x86", "cpu", aliases=("X86",),
        planned_parameters=("--64bits", "--cpu", "--abi", "--features", "--code-model"),
    ),
    BackendScaffoldSpec(
        "arm", "ARM", "cpu", aliases=("ARM",),
        planned_parameters=("--64bits", "--cpu", "--abi", "--float-abi", "--features", "--endian"),
    ),
    BackendScaffoldSpec(
        "thumb", "Thumb", "cpu", aliases=("Thumb",),
        planned_parameters=("--two", "--cpu", "--abi", "--float-abi"),
    ),
    BackendScaffoldSpec(
        "riscv", "RISC-V", "cpu", aliases=("risc-v", "RISC-V"),
        planned_parameters=("--64bits", "--cpu", "--extensions", "--abi", "--code-model"),
    ),
    BackendScaffoldSpec(
        "mips", "MIPS", "cpu", aliases=("MIPS",),
        planned_parameters=("--64bits", "--cpu", "--abi", "--endian"),
    ),
    BackendScaffoldSpec(
        "powerpc", "PowerPC", "cpu", aliases=("PowerPC", "ppc"),
        planned_parameters=("--64bits", "--cpu", "--abi", "--endian"),
    ),
    BackendScaffoldSpec(
        "avr", "AVR", "embedded-cpu", aliases=("AVR",),
        planned_parameters=("--mcu", "--clock-hz", "--format"),
    ),
    BackendScaffoldSpec(
        "8051", "8051", "embedded-cpu", aliases=("mcs51",),
        planned_parameters=("--mcu", "--memory-model", "--format"),
    ),
    BackendScaffoldSpec(
        "pic", "PIC", "embedded-cpu", aliases=("PIC",),
        planned_parameters=("--family", "--mcu", "--format"),
    ),
    BackendScaffoldSpec(
        "xtensa", "Xtensa", "embedded-cpu", aliases=("Xtensa",),
        planned_parameters=("--cpu", "--abi", "--format"),
    ),
    BackendScaffoldSpec(
        "6502", "6502", "retro-cpu", aliases=("mos6502",),
        planned_parameters=("--variant", "--origin", "--format"),
    ),
    BackendScaffoldSpec(
        "z80", "Z80", "retro-cpu", aliases=("Z80",),
        planned_parameters=("--variant", "--origin", "--format"),
    ),
    BackendScaffoldSpec(
        "jvm", "JVM", "virtual-machine", aliases=("JVM", "jar"),
        planned_parameters=("--java-version", "--class-version", "--nopack", "--main-class", "--manifest", "--preview"),
    ),
    BackendScaffoldSpec(
        "android", "Android", "virtual-machine", aliases=("Android", "dex", "apk"),
        planned_parameters=("--min-sdk", "--target-sdk", "--package", "--main-class", "--manifest", "--nopack"),
    ),
    BackendScaffoldSpec(
        "python-bytecode", "Python Bytecode", "virtual-machine",
        aliases=("Python Bytecode", "python_bytecode", "pyc"),
        planned_parameters=("--version", "--optimize", "--invalidation", "--legacy-layout"),
    ),
    BackendScaffoldSpec(
        "webassembly", "WebAssembly", "virtual-machine", aliases=("WebAssembly", "wasm"),
        planned_parameters=("--64bits", "--target", "--component", "--text", "--features", "--import-memory", "--export-memory"),
    ),
    BackendScaffoldSpec(
        "beam", "BEAM", "virtual-machine", aliases=("BEAM",),
        planned_parameters=("--otp-version", "--module-name", "--application", "--nopack"),
    ),
    BackendScaffoldSpec(
        "spirv", "SPIR-V", "gpu-ir", aliases=("SPIR-V", "spir-v"),
        planned_parameters=("--version", "--environment", "--stage", "--entry", "--capability", "--addressing-model", "--assembly", "--validate"),
    ),
    BackendScaffoldSpec(
        "ebpf", "eBPF", "kernel-vm", aliases=("eBPF", "bpf"),
        planned_parameters=("--program-type", "--attach", "--core", "--btf", "--minimum-kernel", "--license", "--emit"),
    ),
    BackendScaffoldSpec(
        "cuda", "CUDA", "gpu", aliases=("CUDA",),
        planned_parameters=("--gpu-arch", "--virtual-arch", "--emit", "--gpu-code", "--max-registers", "--rdc"),
    ),
    BackendScaffoldSpec(
        "amdgpu", "AMDGPU", "gpu", aliases=("AMDGPU",),
        planned_parameters=("--gpu-arch", "--emit", "--code-object-version", "--wave-size"),
    ),
    BackendScaffoldSpec(
        "glsl", "GLSL", "shader-source", aliases=("GLSL",),
        planned_parameters=("--version", "--profile", "--stage", "--entry"),
    ),
    BackendScaffoldSpec(
        "hlsl", "HLSL", "shader-source", aliases=("HLSL",),
        planned_parameters=("--shader-model", "--stage", "--entry", "--emit", "--root-signature"),
    ),
    BackendScaffoldSpec(
        "wgsl", "WGSL", "shader-source", aliases=("WGSL",),
        planned_parameters=("--stage", "--entry"),
    ),
    BackendScaffoldSpec(
        "metal", "Metal", "gpu", aliases=("Metal",),
        planned_parameters=("--platform", "--language-version", "--stage", "--entry", "--emit"),
    ),
    BackendScaffoldSpec(
        "verilog", "Verilog", "hardware-description", aliases=("Verilog",),
        planned_parameters=("--top", "--clock", "--reset", "--reset-kind", "--emit", "--vendor"),
    ),
    BackendScaffoldSpec(
        "systemverilog", "SystemVerilog", "hardware-description",
        aliases=("SystemVerilog", "system-verilog"),
        planned_parameters=("--top", "--clock", "--reset", "--reset-kind", "--emit", "--vendor"),
    ),
    BackendScaffoldSpec(
        "vhdl", "VHDL", "hardware-description", aliases=("VHDL",),
        planned_parameters=("--top", "--clock", "--reset", "--reset-kind", "--emit", "--vendor"),
    ),
)

SCAFFOLD_BACKENDS: dict[str, ScaffoldBackend] = {
    spec.name: ScaffoldBackend(spec) for spec in SCAFFOLD_BACKEND_SPECS
}


def register_scaffold_backends(register: Callable[..., None]) -> None:
    """Register every canonical scaffold and its convenience aliases."""

    for spec in SCAFFOLD_BACKEND_SPECS:
        register(spec.name, SCAFFOLD_BACKENDS[spec.name], aliases=spec.aliases)


__all__ = [
    "SCAFFOLD_BACKENDS",
    "SCAFFOLD_BACKEND_SPECS",
    "register_scaffold_backends",
]
