"""hardware — bare-metal hardware access.

These bindings target the *freestanding* backend, where each c_name is a
symbol defined by FreestandingCodegen's emit_print_impls() (the `_hw_*`
family) using real privileged instructions (in/out, MOV CRn, RDMSR/WRMSR,
CLI/STI/HLT, LIDT, ...).

On hosted targets (Windows/Linux), the same symbols are provided by small
inline asm helpers emitted by emit_asmlib_runtime() so the same source
compiles for either target. Most of those operations require ring 0 and
are unavailable to user-mode code, so on hosted targets they are stubs that
return 0 (port I/O, MMIO, MSRs, CR0-4, invlpg, lidt, cli/sti/hlt, PIC/PIT,
keyboard, VGA text mode).

`rdtsc()`, `cpuid()`, and `rdrand()` are *not* privileged -- ring-3 code can
execute them directly -- so they have real implementations on hosted targets
too, returning genuine CPU values rather than 0.

In addition to the low-level register/port/MMIO primitives above, this module
provides a small *high-level console* layer (`console_*`): write text,
position the cursor, and set colors. On `--target freestanding` these draw
directly into the VGA text-mode framebuffer at 0xB8000 (the same buffer
`print()` uses there); on hosted Windows/Linux they drive the real terminal
via ANSI/VT100 escape sequences written to stdout. `console_get_row()` /
`console_get_col()` return a 0-indexed cursor position tracked internally on
every target, so simple text UIs (status lines, menus, progress bars) can be
written once and run on both bare metal and a normal terminal.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # ---- Port I/O -----------------------------------------------------------
    "in_byte":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_byte"),
    "out_byte":     Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_byte"),
    "in_word":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_word"),
    "out_word":     Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_word"),
    "in_dword":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_dword"),
    "out_dword":    Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_dword"),

    # ---- Memory-mapped I/O --------------------------------------------------
    "mmio_read8":   Func(arg_types=("int",),         ret_type="int",  c_name="_hw_mmio_read8"),
    "mmio_write8":  Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_mmio_write8"),
    "mmio_read32":  Func(arg_types=("int",),         ret_type="int",  c_name="_hw_mmio_read32"),
    "mmio_write32": Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_mmio_write32"),

    # ---- CPU utilities ------------------------------------------------------
    "rdtsc":        Func(arg_types=(),               ret_type="int",  c_name="_hw_rdtsc"),
    "cpuid":        Func(arg_types=("int",),         ret_type="int",  c_name="_hw_cpuid"),
    "halt":         Func(arg_types=(),               ret_type="int",  c_name="_hw_halt"),
    "disable_interrupts": Func(arg_types=(),         ret_type="int",  c_name="_hw_cli"),
    "enable_interrupts":  Func(arg_types=(),         ret_type="int",  c_name="_hw_sti"),
    "rdrand":       Func(arg_types=(),               ret_type="int",  c_name="_hw_rdrand"),
    "io_wait":      Func(arg_types=(),               ret_type="int",  c_name="_hw_io_wait"),

    # ---- Control / model-specific registers ---------------------------------
    "read_cr0":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr0"),
    "read_cr2":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr2"),
    "read_cr3":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr3"),
    "read_cr4":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr4"),
    "write_cr3":    Func(arg_types=("int",),         ret_type="int",  c_name="_hw_write_cr3"),
    "read_msr":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_read_msr"),
    "write_msr":    Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_write_msr"),
    "invlpg":       Func(arg_types=("int",),         ret_type="int",  c_name="_hw_invlpg"),
    "lidt":         Func(arg_types=("int",),         ret_type="int",  c_name="_hw_lidt"),

    # ---- PIC (8259A) --------------------------------------------------------
    "pic_eoi":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_eoi"),
    "pic_mask":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_mask"),
    "pic_unmask":   Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_unmask"),

    # ---- PIT (8253/8254 programmable interval timer) -----------------------
    "pit_set_freq": Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pit_set_freq"),

    # ---- Keyboard (PS/2) ----------------------------------------------------
    "keyboard_read": Func(arg_types=(),              ret_type="int",  c_name="_hw_keyboard_read"),
    "keyboard_poll": Func(arg_types=(),              ret_type="int",  c_name="_hw_keyboard_poll"),

    # ---- VGA text-mode helpers ----------------------------------------------
    "vga_set_color": Func(arg_types=("int", "int"),  ret_type="int",  c_name="_hw_vga_set_color"),
    "vga_set_cursor": Func(arg_types=("int", "int"), ret_type="int",  c_name="_hw_vga_set_cursor"),
    "vga_get_row":  Func(arg_types=(),               ret_type="int",  c_name="_hw_vga_get_row"),
    "vga_get_col":  Func(arg_types=(),               ret_type="int",  c_name="_hw_vga_get_col"),

    # ---- High-level console -------------------------------------------------
    "console_clear": Func(arg_types=(),              ret_type="int",  c_name="_hw_console_clear"),
    "console_putc": Func(arg_types=("int",),         ret_type="int",  c_name="_hw_console_putc"),
    "console_write": Func(arg_types=("str",),        ret_type="int",  c_name="_hw_console_write"),
    "console_set_color": Func(arg_types=("int", "int"), ret_type="int", c_name="_hw_console_set_color"),
    "console_set_cursor": Func(arg_types=("int", "int"), ret_type="int", c_name="_hw_console_set_cursor"),
    "console_get_row": Func(arg_types=(),            ret_type="int",  c_name="_hw_console_get_row"),
    "console_get_col": Func(arg_types=(),            ret_type="int",  c_name="_hw_console_get_col"),
}
