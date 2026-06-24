"""Freestanding 16-bit boot-sector codegen — bootable flat disk image.

Where `--target freestanding` produces a Multiboot1 kernel (GRUB/QEMU hands
control over already in 32-bit protected mode), this target produces a raw,
BIOS-bootable disk image: the first 512 bytes are a real-mode (16-bit) boot
sector ending in the 0xAA55 signature, exactly how a PC starts.

    asmpython hello.py --target freestanding16 -o boot.img
    qemu-system-x86_64 -drive format=raw,file=boot.img

Layout (single flat address space at 0x7C00, where BIOS loads sector 0):
  - sector 0 (512 B): the 16-bit boot sector. It loads the rest of the image
    (sectors 1..N) to the bytes right after itself via INT 13h, enables A20,
    sets up a GDT + identity page tables, switches 16 -> 32 -> 64-bit long
    mode, and jumps to the 64-bit kernel that sits immediately after it.
  - the 64-bit kernel body, runtime helpers, and data — the same code the
    Multiboot1 target emits, just running from low memory (~0x7E00) instead of
    1 MB, which the identity-mapped page tables cover.

Everything from start64 onward is inherited unchanged from FreestandingCodegen;
only the boot path and ORG differ. Verified booting under QEMU (real-mode boot
sector -> 32-bit protected -> 64-bit long mode -> kernel, output over COM1).
"""

from __future__ import annotations

from .target_freestanding import FreestandingCodegen
from . import ast_nodes as A

_BOOT_ORG = 0x7C00
_SECTOR = 512
# 4 KB-aligned address for the page tables, just past the 512-byte boot sector.
_PAGE_TABLES = 0x9000


class Freestanding16Codegen(FreestandingCodegen):
    target_name = "Freestanding16Codegen"
    # Sectors the boot code loads after sector 0 via a single INT 13h call.
    # Fixed (NASM -f bin can't do the assemble-time division for an exact count
    # across sections), so the driver pads the image to exactly
    # (BOOT_READ_SECTORS + 1) sectors after assembly — INT 13h fails the whole
    # read if any requested sector is past EOF, so the image must be at least
    # this large. 127 sectors (~63 KB after the boot sector) stays within the
    # conservative single-call limit some BIOSes impose and covers these
    # kernels' code+data (the heap/stack are uninitialized RAM, not on disk).
    BOOT_READ_SECTORS = 127

    def generate(self) -> str:
        self.emit("; asmpython freestanding 16-bit boot image (BIOS, x86-64)")
        self.emit("; Boot: qemu-system-x86_64 -drive format=raw,file=<output.img>")
        self.emit("")
        # One flat ORG at the BIOS load address. The boot sector and the kernel
        # share this address space — the kernel runs in place right after the
        # boot sector (no copy to 1 MB needed; page tables identity-map it).
        self.emit(f"ORG 0x{_BOOT_ORG:X}")
        self.emit("default rel")
        self.emit("BITS 16")
        self.emit("")

        self._emit_boot16()

        # 64-bit kernel body — same as the Multiboot1 target, but in place.
        self.emit("")
        self.emit("BITS 64")
        self.label("start64")
        self._emit_kernel_init()

        self.emit_entry()
        for f in self.mod.funcs:
            self.emit_function(f)
        for cls in self.mod.classes:
            for m in cls.methods:
                mangled = A.FuncDef(
                    name=self._method_symbol(cls.name, m.name),
                    params=list(m.params),
                    body=list(m.body),
                    pos=m.pos,
                    defaults=list(m.defaults),
                    param_types=list(m.param_types),
                    asm_body=m.asm_body,
                    asm_symbol=(
                        self._method_symbol(cls.name, m.name)
                        if m.asm_body is not None
                        else None
                    ),
                )
                self.emit_function(mangled)
        self.emit_print_impls()
        self.emit_data_sections()

        return "\n".join(self.lines) + "\n"

    # ----------------------------------------------------------- boot (16) --

    def _emit_boot16(self) -> None:
        """A 512-byte real-mode boot sector that loads the rest of the image and
        switches the CPU all the way to 64-bit long mode."""
        self.emit("global _boot")
        self.label("_boot")
        self.emitf(
            "cli",
            "xor ax, ax",
            "mov ds, ax",
            "mov es, ax",
            "mov ss, ax",
            "mov sp, 0x7C00",
            "mov [_boot_drive], dl",  # BIOS leaves the boot drive in DL
        )

        # ---- Load the rest of the image (sectors 1..N) via INT 13h AH=42h ----
        # Destination is the byte right after the boot sector: 0x7E00 = 0x7C0:0
        # segment 0x07E0, offset 0. Reading in place keeps everything in one
        # contiguous low-memory image, addressable in real mode.
        self.emitf(
            "mov si, _dap",
            "mov ah, 0x42",  # extended read (LBA)
            "mov dl, [_boot_drive]",
            "int 0x13",
            "jc _boot_err",
        )

        # ---- Enable A20 (fast A20 via port 0x92) ----------------------------
        self.emitf("in al, 0x92", "or al, 2", "out 0x92, al")

        # ---- Enter 32-bit protected mode -----------------------------------
        self.emitf("lgdt [_gdt16_ptr]")
        self.emitf("mov eax, cr0", "or eax, 1", "mov cr0, eax")
        self.emitf("jmp dword 0x08:_pm32")  # 0x08 = 32-bit code selector

        # ---- 32-bit protected mode: enable long mode -----------------------
        self.emit("")
        self.emit("BITS 32")
        self.label("_pm32")
        self.emitf(
            "mov ax, 0x10",
            "mov ds, ax",
            "mov es, ax",
            "mov ss, ax",
            "mov esp, 0x7C00",
        )
        self.emitf("mov eax, cr4", "or eax, 0x20", "mov cr4, eax")  # PAE
        self.emitf("mov eax, _pml4", "mov cr3, eax")  # CR3
        self.emitf("mov ecx, 0xC0000080", "rdmsr", "or eax, 0x100", "wrmsr")  # LME
        self.emitf("mov eax, cr0", "or eax, 0x80000001", "mov cr0, eax")  # PG|PE
        self.emitf("jmp 0x18:start64")  # 0x18 = 64-bit code selector

        # ---- 16-bit error handler ------------------------------------------
        self.emit("")
        self.emit("BITS 16")
        self.label("_boot_err")
        self.emitf("mov ah, 0x0E", "mov al, 'E'", "int 0x10")
        self.label("._be_halt")
        self.emitf("hlt", "jmp ._be_halt")

        # ---- GDT (flat: 32-bit code/data + 64-bit code) ---------------------
        self.emit("align 8")
        self.label("_gdt16")
        self.emitf("dq 0x0000000000000000")  # null
        self.emitf("dq 0x00CF9A000000FFFF")  # 0x08 32-bit code
        self.emitf("dq 0x00CF92000000FFFF")  # 0x10 data
        self.emitf("dq 0x00AF9A000000FFFF")  # 0x18 64-bit code
        self.label("_gdt16_end")
        self.label("_gdt16_ptr")
        self.emitf("dw _gdt16_end - _gdt16 - 1")
        self.emitf("dd _gdt16")

        # ---- Disk Address Packet: read sectors 1..N to 0x7E00 ---------------
        self.emit("align 4")
        self.label("_dap")
        # Read a fixed, generous span (covers the kernel code/data; the heap and
        # stack are uninitialized RAM, not part of the loadable image). Computing
        # the exact count needs an assemble-time division of a cross-section
        # label difference, which NASM -f bin rejects, so we over-read instead —
        # reading a few unused trailing sectors is harmless.
        self.emitf("db 0x10")  # packet size
        self.emitf("db 0")  # reserved
        self.emitf(f"dw {self.BOOT_READ_SECTORS}")  # sector count (fixed)
        self.emitf("dw 0x7E00")  # dest offset
        self.emitf("dw 0x0000")  # dest segment (0x0000:0x7E00)
        self.emitf("dq 1")  # starting LBA

        self.label("_boot_drive")
        self.emitf("db 0")

        # ---- Boot signature at byte 510 of sector 0 -------------------------
        # `.boot` is the first section, so $$ is byte 0 of the file; pad to 510
        # within it, then the signature.
        self.emit("times (510 - ($ - $$)) db 0")
        self.emitf("dw 0xAA55")

        # ---- Page tables (after the boot sector, page-aligned) --------------
        # Identity-map the first 16 MB via 2 MB pages. They live in the loaded
        # image (sectors 1+), so the boot sector's INT 13h read brings them in.
        # NOTE: a NASM `align 4096` here corrupts the -f bin section base (it
        # shifts the whole image by up to 4 KB, throwing the 0xAA55 signature off
        # offset 510). Place the tables at the fixed 4 KB-aligned address 0x9000
        # with an explicit `times` pad instead.
        self.emit("")
        self.emit(f"times (0x{_PAGE_TABLES:X} - 0x{_BOOT_ORG:X}) - ($ - $$) db 0")
        self.label("_pml4")
        self.emitf("dq _pdpt + 3")
        self.emitf("times 511 dq 0")
        self.label("_pdpt")
        self.emitf("dq _pd + 3")
        self.emitf("times 511 dq 0")
        self.label("_pd")
        for i in range(8):
            self.emitf(f"dq 0x{i * 0x200000:07X} | 0x83")
        self.emitf("times 504 dq 0")

    # ---- helpers: image sizing -------------------------------------------

    # Note: the boot code reads a fixed BOOT_READ_SECTORS sectors. Under QEMU's
    # raw disk, reads past end-of-file return zeros, so over-reading is safe and
    # no explicit padding is needed. (For real hardware, pad the image fiwe cantle to
    # >= (BOOT_READ_SECTORS+1)*512 bytes after the build.)
