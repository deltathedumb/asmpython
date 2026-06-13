"""asmlib.hardware — bare-metal hardware access.

These bindings target the *freestanding* backend.  Each c_name is a
symbol defined by FreestandingCodegen's emit_print_impls() (the `_hw_*`
family).  On hosted targets the symbols are provided by thin C wrappers
so the same source can be compiled for either target; on hosted builds
the actual port-I/O instructions are unavailable to user-mode code, so
those functions return 0 and are useful only as stubs.
"""
from __future__ import annotations

from ..stdlib import Func, Const

BINDINGS: dict = {
    # ---- Port I/O -----------------------------------------------------------
    # in_byte(port: int) -> int   read one byte from I/O port
    "in_byte":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_byte"),
    # out_byte(port: int, value: int)
    "out_byte":     Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_byte"),
    # in_word(port: int) -> int
    "in_word":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_word"),
    # out_word(port: int, value: int)
    "out_word":     Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_word"),
    # in_dword(port: int) -> int
    "in_dword":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_in_dword"),
    # out_dword(port: int, value: int)
    "out_dword":    Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_out_dword"),

    # ---- Memory-mapped I/O --------------------------------------------------
    # mmio_read8(addr: int) -> int   — read 1 byte from physical address
    "mmio_read8":   Func(arg_types=("int",),         ret_type="int",  c_name="_hw_mmio_read8"),
    # mmio_write8(addr: int, value: int)
    "mmio_write8":  Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_mmio_write8"),
    # mmio_read32(addr: int) -> int
    "mmio_read32":  Func(arg_types=("int",),         ret_type="int",  c_name="_hw_mmio_read32"),
    # mmio_write32(addr: int, value: int)
    "mmio_write32": Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_mmio_write32"),

    # ---- CPU utilities ------------------------------------------------------
    # rdtsc() -> int   read 64-bit timestamp counter
    "rdtsc":        Func(arg_types=(),               ret_type="int",  c_name="_hw_rdtsc"),
    # cpuid(leaf: int) -> int   returns EAX after CPUID(leaf)
    "cpuid":        Func(arg_types=("int",),         ret_type="int",  c_name="_hw_cpuid"),
    # halt() — execute HLT (useful in interrupt handlers)
    "halt":         Func(arg_types=(),               ret_type="int",  c_name="_hw_halt"),
    # disable_interrupts() / enable_interrupts()
    "disable_interrupts": Func(arg_types=(),         ret_type="int",  c_name="_hw_cli"),
    "enable_interrupts":  Func(arg_types=(),         ret_type="int",  c_name="_hw_sti"),
    # rdrand() -> int   hardware random 64-bit value (retries until valid).
    "rdrand":       Func(arg_types=(),               ret_type="int",  c_name="_hw_rdrand"),
    # io_wait() — tiny delay by writing the unused port 0x80 (PIC remap timing).
    "io_wait":      Func(arg_types=(),               ret_type="int",  c_name="_hw_io_wait"),

    # ---- Control / model-specific registers ---------------------------------
    # read_cr0/cr2/cr3/cr4() -> int. cr2 holds the faulting address after a
    # page fault; cr3 is the page-table base; cr0/cr4 hold mode bits.
    "read_cr0":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr0"),
    "read_cr2":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr2"),
    "read_cr3":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr3"),
    "read_cr4":     Func(arg_types=(),               ret_type="int",  c_name="_hw_read_cr4"),
    # write_cr3(value: int) — reload the page-table base (flushes the TLB).
    "write_cr3":    Func(arg_types=("int",),         ret_type="int",  c_name="_hw_write_cr3"),
    # read_msr(index: int) -> int / write_msr(index, value)
    "read_msr":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_read_msr"),
    "write_msr":    Func(arg_types=("int", "int"),   ret_type="int",  c_name="_hw_write_msr"),
    # invlpg(addr: int) — invalidate one page's TLB entry.
    "invlpg":       Func(arg_types=("int",),         ret_type="int",  c_name="_hw_invlpg"),
    # lidt(idt_ptr_addr: int) — load the Interrupt Descriptor Table register
    # from a 6-byte (limit, base) pointer at the given address.
    "lidt":         Func(arg_types=("int",),         ret_type="int",  c_name="_hw_lidt"),

    # ---- PIC (8259A) --------------------------------------------------------
    # pic_eoi(irq: int) — send end-of-interrupt signal
    "pic_eoi":      Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_eoi"),
    # pic_mask(irq: int) — mask (disable) one IRQ line
    "pic_mask":     Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_mask"),
    # pic_unmask(irq: int) — unmask (enable) one IRQ line
    "pic_unmask":   Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pic_unmask"),

    # ---- PIT (8253/8254 programmable interval timer) -----------------------
    # pit_set_freq(hz: int) — set channel-0 reload value for given frequency
    "pit_set_freq": Func(arg_types=("int",),         ret_type="int",  c_name="_hw_pit_set_freq"),

    # ---- Keyboard (PS/2) ----------------------------------------------------
    # keyboard_read() -> int   blocking read of the next raw scancode byte
    "keyboard_read": Func(arg_types=(),              ret_type="int",  c_name="_hw_keyboard_read"),
    # keyboard_poll() -> int   non-blocking; returns 0 if nothing waiting
    "keyboard_poll": Func(arg_types=(),              ret_type="int",  c_name="_hw_keyboard_poll"),

    # ---- VGA text-mode helpers ----------------------------------------------
    # vga_set_color(fg: int, bg: int)  set current attribute byte
    "vga_set_color": Func(arg_types=("int", "int"),  ret_type="int",  c_name="_hw_vga_set_color"),
    # vga_set_cursor(row: int, col: int)
    "vga_set_cursor": Func(arg_types=("int", "int"), ret_type="int",  c_name="_hw_vga_set_cursor"),
    # vga_get_row() -> int
    "vga_get_row":  Func(arg_types=(),               ret_type="int",  c_name="_hw_vga_get_row"),
    # vga_get_col() -> int
    "vga_get_col":  Func(arg_types=(),               ret_type="int",  c_name="_hw_vga_get_col"),
}
