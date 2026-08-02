; 64-bit division/remainder helpers for asmpython's x86 (32-bit) backend.
;
; asmpython's shared IR models every int as a true 64-bit value (see
; _compiler/ir.py's I64), but a 32-bit GP register only holds 32 bits --
; the x86 (32-bit) backend represents a 64-bit int as a pair of 32-bit
; registers/stack words (low dword, high dword), the same technique every
; real 32-bit x86 toolchain uses for C's `long long` (GCC's libgcc calls
; these __udivdi3/__divdi3/__umoddi3/__moddi3; MSVC's are __aulldiv/
; __aulldvrm/etc under different names). There is no single x86-32
; instruction for a 64-by-64 division (IDIV only does 64-by-32, dividend
; already in EDX:EAX, and gives a 32-bit quotient) -- this file supplies
; that operation as a real callable helper instead of inlining a ~20-
; instruction shift-and-subtract loop at every division site in
; codegen.py.
;
; Calling convention: plain cdecl. All four functions take two 64-bit
; arguments (dividend, divisor), each as two stacked 32-bit dwords, low
; dword at the lower address (i.e. the layout `push hi` / `push lo`
; naturally produces, low dword pushed last):
;
;   [ebp+8]  dividend_lo
;   [ebp+12] dividend_hi
;   [ebp+16] divisor_lo
;   [ebp+20] divisor_hi
;
; Result returned in EDX:EAX (EAX = low dword, EDX = high dword) -- same
; convention x86-32 IDIV/DIV themselves use for a 64-bit value split
; across two registers, so codegen.py's call-result handling for these
; helpers is identical to any other GP-returning call.

BITS 32

; An explicit `section .text` is required here, not just implied by
; being the first code -- NASM's `elf32` output format defaults an
; unlabeled leading section to a real, defined .text (so this file
; worked fine there without one), but its `win32` output format does
; NOT: every symbol below showed up as UNDEFINED (COFF section index 0)
; in a real `nasm -f win32`-assembled object, confirmed directly via
; objdump, with a minimal single-label repro isolating the section
; directive as the exact variable that flips it from undefined to
; correctly-defined. This was invisible until the PE32/COFF32 linker
; work actually tried to assemble this file with -f win32 for the
; first time -- the existing -f elf32 usage never exercised this path.
section .text

global __udivdi64
global __divdi64
global __umoddi64
global __moddi64


; ---- unsigned 64/64 -> 64 quotient -----------------------------------------
; Classic "restoring binary long division", 64 iterations. Each iteration:
;   1. Shift the dividend pair (edi:esi) left by 1, exposing its next bit.
;   2. Shift the remainder pair left by 1, inserting the bit exposed in
;      step 1 as the remainder's new low bit.
;   3. If remainder >= divisor, subtract the divisor from the remainder
;      and set the corresponding quotient bit.
;
; SHLD is exactly the right primitive for step 1/2's "shift this pair left
; by 1, filling the low word's new bit 0 from the high word's incoming
; source" pattern: `shld dst, src, 1` sets dst = (dst<<1) | (src>>31) and
; leaves src untouched. Getting the dividend's extracted bit into the
; remainder pair takes care with instruction order, since two DIFFERENT
; carry-producing steps are involved and neither may clobber the other's
; result before it's consumed:
;   1. `shld edi, esi, 1` / `shl esi, 1` shifts the dividend pair left,
;      and CF right after the shld is the dividend's true outgoing MSB
;      (`msb`) -- but this MUST be saved somewhere other than CF/a
;      register the next step needs, since step 2 below immediately
;      produces its OWN, unrelated CF.
;   2. `shld edx, eax, 1` shifts the remainder pair's HIGH word using the
;      remainder LOW word's own (pre-shift) top bit -- this must run
;      BEFORE eax itself is modified, since shld reads eax non-
;      destructively as its bit-31 source.
;   3. `rcl eax, msb` (via `shr`+`or`, not literally RCL, since RCL's
;      operand is a fixed CF, not an arbitrary saved bit) shifts the
;      remainder LOW word and inserts the SAVED msb from step 1 as its
;      new bit 0.
; An earlier version of this file conflated these two independent carry
; chains (used the CF still sitting around from step 2's shld as if it
; were step 1's dividend bit), silently inserting the wrong bit into the
; remainder whenever the dividend's top two bits differed -- confirmed
; via a real unicorn-engine execution trace of udivdi64/umoddi64(100, 7),
; which returned 0 (the remainder pair never actually accumulated
; anything, since step 2's shld was clobbering CF before anything used
; the value step 1 had produced).
;
; Register plan for the loop (all 6 non-ebp/esp GP registers are live
; simultaneously -- there is no room left for a register-held loop
; counter OR the saved msb bit, hence both living on the stack below):
;   ebx:ecx  divisor (hi:lo), fixed for the whole loop
;   edi:esi  dividend (hi:lo), shifted left each iteration
;   edx:eax  remainder (hi:lo), shifted left (with insert) each iteration
;
; divisor == 0 is not checked here: callers needing a real ZeroDivisionError
; check the divisor before calling this, exactly like idiv/irem's existing
; codegen.py checks already do for the 32-bit-native case.
__udivdi64:
    push ebp
    mov ebp, esp
    sub esp, 16           ; [ebp-4]=quot_lo [ebp-8]=quot_hi [ebp-12]=counter [ebp-16]=msb
    push ebx
    push esi
    push edi

    mov esi, [ebp+8]     ; dividend_lo
    mov edi, [ebp+12]    ; dividend_hi
    mov ecx, [ebp+16]    ; divisor_lo
    mov ebx, [ebp+20]    ; divisor_hi

    call .run
    mov eax, [ebp-4]     ; quotient_lo
    mov edx, [ebp-8]     ; quotient_hi

    pop edi
    pop esi
    pop ebx
    mov esp, ebp
    pop ebp
    ret
.run:
    xor edx, edx
    xor eax, eax
    mov dword [ebp-4], 0
    mov dword [ebp-8], 0
    mov dword [ebp-12], 64
.loop:
    shld edi, esi, 1
    setc byte [ebp-16]    ; save the dividend's true outgoing MSB
    shl esi, 1

    shld edx, eax, 1      ; must run BEFORE eax is touched below (reads
                          ; eax's own pre-shift top bit non-destructively)
    shl eax, 1
    or al, byte [ebp-16]  ; insert the saved dividend MSB as eax's new bit 0

    ; Shift the quotient pair left by 1 EVERY iteration (win or lose the
    ; comparison below) -- an earlier version only ever did `bts
    ; dword [ebp-4], 0` on a successful subtract, which sets bit 0
    ; unconditionally instead of shifting the quotient's prior bits up
    ; first, so every quotient this produced collapsed to just its own
    ; last bit (confirmed via a real trace: udivdi64(100, 7) returned 1
    ; instead of the correct 14 = 0b1110).
    shl dword [ebp-4], 1
    rcl dword [ebp-8], 1

    cmp edx, ebx
    jb .skip_sub
    ja .do_sub
    cmp eax, ecx
    jb .skip_sub
.do_sub:
    sub eax, ecx
    sbb edx, ebx
    or dword [ebp-4], 1
.skip_sub:
    dec dword [ebp-12]
    jnz .loop
    ret


; ---- unsigned 64/64 -> 64 remainder -----------------------------------------
; Kept as its own entry point (rather than having callers derive it via
; dividend - quotient*divisor, a further 64x64->128 multiply) since
; codegen.py needs remainder alone for `%` far more often than paired
; with a discarded quotient.
__umoddi64:
    push ebp
    mov ebp, esp
    sub esp, 4            ; [ebp-4] = saved dividend MSB per iteration (see __udivdi64's comment)
    push ebx
    push esi
    push edi
    push ecx             ; loop counter lives here (no quotient tracking needed)

    mov esi, [ebp+8]     ; dividend_lo
    mov edi, [ebp+12]    ; dividend_hi
    mov ecx, [ebp+16]    ; divisor_lo (kept differently below -- see note)
    mov ebx, [ebp+20]    ; divisor_hi

    ; ecx is needed both as "divisor_lo" (compared/subtracted every
    ; iteration) and as the loop counter -- unlike __udivdi64, there's no
    ; free stack quotient slot to offload the counter to here since this
    ; routine avoids the extra sub esp/mov esp,ebp bookkeeping when it
    ; doesn't need one. Swap roles instead: keep divisor_lo in a fixed
    ; stack local, and use ecx purely as the down-counter.
    push ecx              ; save divisor_lo underneath everything else
    mov ecx, 64

    xor edx, edx
    xor eax, eax          ; remainder starts at 0

.loop:
    shld edi, esi, 1
    setc byte [ebp-4]     ; save the dividend's true outgoing MSB
    shl esi, 1

    shld edx, eax, 1      ; must run BEFORE eax is touched below
    shl eax, 1
    or al, byte [ebp-4]   ; insert the saved dividend MSB as eax's new bit 0

    cmp edx, ebx
    jb .skip_sub
    ja .do_sub
    cmp eax, [esp]        ; divisor_lo
    jb .skip_sub
.do_sub:
    sub eax, [esp]
    sbb edx, ebx
.skip_sub:
    dec ecx
    jnz .loop

    ; eax:edx already hold the final remainder (low:high) -- nothing
    ; further to move.
    add esp, 4            ; drop the saved divisor_lo
    pop ecx
    pop edi
    pop esi
    pop ebx
    mov esp, ebp
    pop ebp
    ret


; ---- signed 64/64 -> 64 quotient -------------------------------------------
; Standard sign-normalize / unsigned-divide / sign-fixup approach: take the
; absolute value of both operands, divide unsigned, then negate the
; quotient if exactly one operand was negative (matching Python/C's
; truncating-toward-zero division semantics -- asmpython's `idiv` on
; already-32-bit-native values uses the same truncating IDIV instruction,
; so this stays consistent with that existing behavior rather than
; introducing floor-division semantics division on 64-bit values wouldn't
; otherwise have).
__divdi64:
    push ebp
    mov ebp, esp
    push ebx
    push esi
    push edi

    mov eax, [ebp+8]     ; dividend_lo
    mov edx, [ebp+12]    ; dividend_hi
    mov ebx, [ebp+16]    ; divisor_lo
    mov ecx, [ebp+20]    ; divisor_hi

    xor esi, esi          ; esi = result sign (0 = positive, 1 = negative)
    test edx, edx
    jns .dividend_ok
    neg eax
    adc edx, 0
    neg edx
    xor esi, 1
.dividend_ok:
    test ecx, ecx
    jns .divisor_ok
    neg ebx
    adc ecx, 0
    neg ecx
    xor esi, 1
.divisor_ok:

    push ecx              ; divisor_hi (abs)
    push ebx              ; divisor_lo (abs)
    push edx              ; dividend_hi (abs)
    push eax              ; dividend_lo (abs)
    call __udivdi64
    add esp, 16

    test esi, esi
    jz .positive
    neg eax
    adc edx, 0
    neg edx
.positive:

    pop edi
    pop esi
    pop ebx
    pop ebp
    ret


; ---- signed 64/64 -> 64 remainder -------------------------------------------
; C/Python's `%` truncating-division remainder takes the DIVIDEND's sign
; (not the divisor's, unlike Python's own floor-mod operator on plain
; machine ints) -- matching how asmpython's existing 32-bit-native `irem`
; already behaves via IDIV, so this 64-bit path stays consistent with it.
__moddi64:
    push ebp
    mov ebp, esp
    push ebx
    push esi
    push edi

    mov eax, [ebp+8]
    mov edx, [ebp+12]
    mov ebx, [ebp+16]
    mov ecx, [ebp+20]

    xor esi, esi           ; esi = dividend's original sign
    test edx, edx
    jns .dividend_ok
    neg eax
    adc edx, 0
    neg edx
    mov esi, 1
.dividend_ok:
    test ecx, ecx
    jns .divisor_ok
    neg ebx
    adc ecx, 0
    neg ecx
.divisor_ok:

    push ecx
    push ebx
    push edx
    push eax
    call __umoddi64
    add esp, 16

    test esi, esi
    jz .positive
    neg eax
    adc edx, 0
    neg edx
.positive:

    pop edi
    pop esi
    pop ebx
    pop ebp
    ret
