package asmpython.jvm;

/**
 * Lists, dicts, instances and boxed scalars, laid out exactly as the native
 * runtime lays them out.
 *
 * <p>"Exactly" is not a stylistic choice. Generated code reads these headers
 * <em>directly</em> — a {@code for x in xs} loads the list's length from +8 and
 * its buffer from +16 and indexes that itself, never calling the ABI — so a
 * different layout here would not fail, it would silently read the wrong words.
 * The layouts below are transcribed from {@code abi_shims.asm} and
 * {@code codegen.py}'s {@code DICT_*}/{@code LIST_*} constants; those are the
 * source of truth, and this file follows them.
 *
 * <pre>
 *   list      [cap @0][len @8][buf @16]                 buf = cap * 8 bytes
 *   dict      [cap @0][len @8][tomb @16][buf @24][order @32]
 *                                                       buf   = cap * 16
 *                                                       order = cap * 8
 *   dict slot [key @0][value @8]      key 0 = empty, 1 = tombstone
 *   box       [BOX_MAGIC @0][tag @8][payload @16]
 * </pre>
 *
 * <p>An instance is a dict — attribute access lowers to {@code _abi_dict_set} /
 * {@code _abi_dict_get_default} against the object's own header, which is why
 * classes need no separate machinery here.
 */
public class Containers extends Memory {

    // ---- list ------------------------------------------------------------

    protected static final int LIST_CAP_OFF = 0;
    protected static final int LIST_LEN_OFF = 8;
    protected static final int LIST_BUF_OFF = 16;
    protected static final int LIST_HEADER = 24;

    // ---- dict ------------------------------------------------------------

    protected static final int DICT_CAP_OFF = 0;
    protected static final int DICT_LEN_OFF = 8;
    protected static final int DICT_TOMB_OFF = 16;
    protected static final int DICT_BUF_OFF = 24;
    protected static final int DICT_ORDER_OFF = 32;
    protected static final int DICT_HEADER = 40;
    protected static final int DICT_SLOT_SIZE = 16;
    protected static final int DICT_INITIAL_CAP = 8;

    /** Slot key sentinels. Neither can collide with a real heap address. */
    private static final long SLOT_EMPTY = 0;
    private static final long SLOT_TOMBSTONE = 1;

    // ---- box -------------------------------------------------------------

    /**
     * Stamped at offset 0 of every boxed scalar. A large odd sentinel no real
     * pointer, length or tag can equal, so one fault-safe load identifies a box
     * — which is what makes reading a tag safe on <em>any</em> value in an
     * "any" slot, including a raw string or list pointer.
     *
     * <p>Keep in sync with {@code BOX_MAGIC} in ir_lower.py.
     */
    protected static final long BOX_MAGIC = 0xB0BE11EDB0BE11EDL;

    protected static final int BOX_TAG_OFF = 8;
    protected static final int BOX_PAYLOAD_OFF = 16;
    protected static final int BOX_SIZE = 24;

    protected Containers() {
    }

    // ======================================================================
    // list
    // ======================================================================

    /** {@code _abi_new_list(capacity)} — an empty list with room for `cap`. */
    public static long _abi_new_list(long capacity) {
        long cap = Math.max(capacity, 1);
        long header = allocate(LIST_HEADER);
        storeLong(header + LIST_CAP_OFF, cap);
        storeLong(header + LIST_LEN_OFF, 0);
        storeLong(header + LIST_BUF_OFF, allocate(cap * 8));
        return header;
    }

    /** {@code _abi_list_append(list, value)} — grows by doubling. */
    public static void _abi_list_append(long list, long value) {
        long len = loadLong(list + LIST_LEN_OFF);
        long cap = loadLong(list + LIST_CAP_OFF);
        if (len >= cap) {
            long grown = Math.max(cap * 2, 1);
            long buffer = allocate(grown * 8);
            long old = loadLong(list + LIST_BUF_OFF);
            for (long i = 0; i < len; i++) {
                storeLong(buffer + i * 8, loadLong(old + i * 8));
            }
            storeLong(list + LIST_BUF_OFF, buffer);
            storeLong(list + LIST_CAP_OFF, grown);
        }
        storeLong(loadLong(list + LIST_BUF_OFF) + len * 8, value);
        storeLong(list + LIST_LEN_OFF, len + 1);
    }

    public static long listLength(long list) {
        return loadLong(list + LIST_LEN_OFF);
    }

    public static long listGet(long list, long index) {
        return loadLong(loadLong(list + LIST_BUF_OFF) + index * 8);
    }

    /** {@code _abi_list_slice(src, start, stop)} — a new list, Python-clamped. */
    public static long _abi_list_slice(long source, long start, long stop) {
        long length = listLength(source);
        long from = clamp(start, length);
        long to = clamp(stop, length);
        long result = _abi_new_list(Math.max(to - from, 1));
        for (long i = from; i < to; i++) {
            _abi_list_append(result, listGet(source, i));
        }
        return result;
    }

    /** {@code _abi_list_slice_step(src, start, stop, step)}. */
    public static long _abi_list_slice_step(long source, long start, long stop, long step) {
        long length = listLength(source);
        long result = _abi_new_list(1);
        if (step == 0) {
            _abi_raise(allocateString("ValueError: slice step cannot be zero"), 0);
        }
        if (step > 0) {
            for (long i = clamp(start, length); i < clamp(stop, length); i += step) {
                _abi_list_append(result, listGet(source, i));
            }
        } else {
            long from = start < 0 ? start + length : Math.min(start, length - 1);
            long to = stop < 0 ? stop + length : stop;
            for (long i = from; i > to && i >= 0; i += step) {
                _abi_list_append(result, listGet(source, i));
            }
        }
        return result;
    }

    /**
     * Python's slice clamping: a negative index counts from the end, and an
     * out-of-range one is pinned rather than raising — {@code xs[1:99]} is not
     * an error.
     */
    private static long clamp(long index, long length) {
        long i = index < 0 ? index + length : index;
        if (i < 0) {
            return 0;
        }
        return Math.min(i, length);
    }

    public static void _abi_list_extend(long list, long other) {
        long length = listLength(other);
        for (long i = 0; i < length; i++) {
            _abi_list_append(list, listGet(other, i));
        }
    }

    public static long _abi_list_pop(long list, long index) {
        long length = listLength(list);
        long at = index < 0 ? index + length : index;
        if (at < 0 || at >= length) {
            _abi_raise(allocateString("IndexError: pop index out of range"), 0);
        }
        long buffer = loadLong(list + LIST_BUF_OFF);
        long value = loadLong(buffer + at * 8);
        for (long i = at; i < length - 1; i++) {
            storeLong(buffer + i * 8, loadLong(buffer + (i + 1) * 8));
        }
        storeLong(list + LIST_LEN_OFF, length - 1);
        return value;
    }

    public static void _abi_list_reverse(long list) {
        long buffer = loadLong(list + LIST_BUF_OFF);
        long low = 0;
        long high = listLength(list) - 1;
        while (low < high) {
            long swap = loadLong(buffer + low * 8);
            storeLong(buffer + low * 8, loadLong(buffer + high * 8));
            storeLong(buffer + high * 8, swap);
            low++;
            high--;
        }
    }

    public static void _abi_list_insert(long list, long index, long value) {
        long length = listLength(list);
        long at = index < 0 ? Math.max(index + length, 0) : Math.min(index, length);
        _abi_list_append(list, 0);                     // make room, then shift
        long buffer = loadLong(list + LIST_BUF_OFF);
        for (long i = length; i > at; i--) {
            storeLong(buffer + i * 8, loadLong(buffer + (i - 1) * 8));
        }
        storeLong(buffer + at * 8, value);
    }

    public static long _abi_list_repeat(long list, long times) {
        long result = _abi_new_list(1);
        long length = listLength(list);
        for (long n = 0; n < times; n++) {
            for (long i = 0; i < length; i++) {
                _abi_list_append(result, listGet(list, i));
            }
        }
        return result;
    }

    /** {@code _abi_range_list(start, stop, step)} — range() materialised. */
    public static long _abi_range_list(long start, long stop, long step) {
        if (step == 0) {
            _abi_raise(allocateString("ValueError: range() arg 3 must not be zero"), 0);
        }
        long span = step > 0 ? stop - start : start - stop;
        long magnitude = Math.abs(step);
        long count = span <= 0 ? 0 : (span + magnitude - 1) / magnitude;
        long result = _abi_new_list(Math.max(count, 1));
        for (long i = 0, value = start; i < count; i++, value += step) {
            _abi_list_append(result, value);
        }
        return result;
    }

    // ---- sorting ---------------------------------------------------------
    //
    // In place, over the element buffer, so a sorted list is still the same
    // list object to any pointer already holding it.

    public static void _abi_sort_int(long list) {
        long length = listLength(list);
        long buffer = loadLong(list + LIST_BUF_OFF);
        long[] values = new long[(int) length];
        for (int i = 0; i < length; i++) {
            values[i] = loadLong(buffer + (long) i * 8);
        }
        java.util.Arrays.sort(values);
        for (int i = 0; i < length; i++) {
            storeLong(buffer + (long) i * 8, values[i]);
        }
    }

    public static void _abi_sort_str(long list) {
        long length = listLength(list);
        long buffer = loadLong(list + LIST_BUF_OFF);
        Long[] pointers = new Long[(int) length];
        for (int i = 0; i < length; i++) {
            pointers[i] = loadLong(buffer + (long) i * 8);
        }
        // By content, not by address: two equal strings are usually two
        // separate allocations here.
        java.util.Arrays.sort(pointers,
                (a, b) -> readString(a).compareTo(readString(b)));
        for (int i = 0; i < length; i++) {
            storeLong(buffer + (long) i * 8, pointers[i]);
        }
    }

    // ======================================================================
    // dict (and instances, which are dicts)
    // ======================================================================

    /** {@code _abi_new_instance()} — an empty dict; also every object. */
    public static long _abi_new_instance() {
        long header = allocate(DICT_HEADER);
        storeLong(header + DICT_CAP_OFF, DICT_INITIAL_CAP);
        storeLong(header + DICT_LEN_OFF, 0);
        storeLong(header + DICT_TOMB_OFF, 0);
        storeLong(header + DICT_BUF_OFF, allocate((long) DICT_INITIAL_CAP * DICT_SLOT_SIZE));
        storeLong(header + DICT_ORDER_OFF, allocate((long) DICT_INITIAL_CAP * 8));
        return header;
    }

    public static long _abi_new_dict() {
        return _abi_new_instance();
    }

    /**
     * FNV-1a over the key's bytes, matching {@code _runtime_hash_string}.
     *
     * <p>Hashing the CONTENT and not the pointer is the whole point: dict keys
     * are strdup'd on insert, so the caller's pointer and the stored one are
     * never the same address.
     */
    private static long hashKey(long key) {
        long hash = 0xcbf29ce484222325L;
        long cursor = key;
        for (long b = loadByte(cursor); b != 0; b = loadByte(++cursor)) {
            hash ^= b;
            hash *= 0x100000001b3L;
        }
        return hash;
    }

    private static boolean keysEqual(long a, long b) {
        if (a == b) {
            return true;
        }
        long i = a;
        long j = b;
        while (true) {
            long x = loadByte(i++);
            long y = loadByte(j++);
            if (x != y) {
                return false;
            }
            if (x == 0) {
                return true;
            }
        }
    }

    /**
     * The probe: returns the matching slot, or {@code -(insertion slot) - 1}
     * when the key is absent.
     *
     * <p>Linear probing with {@code idx = (idx + 1) & (cap - 1)}, the same walk
     * as {@code _runtime_dict_lookup_slot}, and the first tombstone seen is
     * preferred as the insertion point so deletions do not permanently cost a
     * slot.
     */
    private static long lookupSlot(long dict, long key) {
        long cap = loadLong(dict + DICT_CAP_OFF);
        long buffer = loadLong(dict + DICT_BUF_OFF);
        long mask = cap - 1;
        long index = hashKey(key) & mask;
        long firstTombstone = -1;

        for (long probes = 0; probes <= cap; probes++) {
            long slot = buffer + index * DICT_SLOT_SIZE;
            long slotKey = loadLong(slot);
            if (slotKey == SLOT_EMPTY) {
                return -(firstTombstone >= 0 ? firstTombstone : slot) - 1;
            }
            if (slotKey == SLOT_TOMBSTONE) {
                if (firstTombstone < 0) {
                    firstTombstone = slot;
                }
            } else if (keysEqual(slotKey, key)) {
                return slot;
            }
            index = (index + 1) & mask;
        }
        // Unreachable while the load factor is enforced, but a full table must
        // not loop forever if it ever is not.
        return -(firstTombstone >= 0 ? firstTombstone : buffer) - 1;
    }

    /** {@code _abi_dict_set(dict, key, value)} — insert or update. */
    public static void _abi_dict_set(long dict, long key, long value) {
        long length = loadLong(dict + DICT_LEN_OFF);
        long tombs = loadLong(dict + DICT_TOMB_OFF);
        long cap = loadLong(dict + DICT_CAP_OFF);
        if (length + tombs >= cap - cap / 4) {          // load factor 3/4
            grow(dict);
        }

        long found = lookupSlot(dict, key);
        if (found >= 0) {
            storeLong(found + 8, value);
            return;
        }

        long slot = -(found + 1);
        if (loadLong(slot) == SLOT_TOMBSTONE) {
            storeLong(dict + DICT_TOMB_OFF, loadLong(dict + DICT_TOMB_OFF) - 1);
        }
        long owned = strdup(key);                        // the dict owns its keys
        storeLong(slot, owned);
        storeLong(slot + 8, value);

        long len = loadLong(dict + DICT_LEN_OFF);
        storeLong(loadLong(dict + DICT_ORDER_OFF) + len * 8, owned);
        storeLong(dict + DICT_LEN_OFF, len + 1);
    }

    /**
     * Rehash into a table twice the size.
     *
     * <p>Insertion order is what drives it: iterating the order buffer rather
     * than the slot table is what keeps a rehashed dict in the order Python
     * promises, and it drops tombstones for free.
     */
    private static void grow(long dict) {
        long cap = loadLong(dict + DICT_CAP_OFF);
        long length = loadLong(dict + DICT_LEN_OFF);
        long oldOrder = loadLong(dict + DICT_ORDER_OFF);

        long[] keys = new long[(int) length];
        long[] values = new long[(int) length];
        for (int i = 0; i < length; i++) {
            long key = loadLong(oldOrder + (long) i * 8);
            keys[i] = key;
            long slot = lookupSlot(dict, key);
            values[i] = slot >= 0 ? loadLong(slot + 8) : 0;
        }

        long grown = cap * 2;
        storeLong(dict + DICT_CAP_OFF, grown);
        storeLong(dict + DICT_BUF_OFF, allocate(grown * DICT_SLOT_SIZE));
        storeLong(dict + DICT_ORDER_OFF, allocate(grown * 8));
        storeLong(dict + DICT_LEN_OFF, 0);
        storeLong(dict + DICT_TOMB_OFF, 0);

        long order = loadLong(dict + DICT_ORDER_OFF);
        for (int i = 0; i < length; i++) {
            long slot = -(lookupSlot(dict, keys[i]) + 1);
            storeLong(slot, keys[i]);                    // already owned
            storeLong(slot + 8, values[i]);
            storeLong(order + (long) i * 8, keys[i]);
        }
        storeLong(dict + DICT_LEN_OFF, length);
    }

    /** {@code _abi_dict_get_default(dict, key, fallback)}. */
    public static long _abi_dict_get_default(long dict, long key, long fallback) {
        long slot = lookupSlot(dict, key);
        return slot >= 0 ? loadLong(slot + 8) : fallback;
    }

    /** {@code _abi_dict_contains(dict, key)} -> 1 or 0. */
    public static long _abi_dict_contains(long dict, long key) {
        return lookupSlot(dict, key) >= 0 ? 1 : 0;
    }

    /** Raises KeyError when absent, unlike get_default. */
    public static long _abi_dict_get(long dict, long key) {
        long slot = lookupSlot(dict, key);
        if (slot < 0) {
            _abi_raise(allocateString("KeyError: " + readString(key)), 0);
        }
        return loadLong(slot + 8);
    }

    /** {@code _abi_dict_keys(dict)} — a list of keys, in insertion order. */
    public static long _abi_dict_keys(long dict) {
        long length = loadLong(dict + DICT_LEN_OFF);
        long order = loadLong(dict + DICT_ORDER_OFF);
        long result = _abi_new_list(Math.max(length, 1));
        for (long i = 0; i < length; i++) {
            _abi_list_append(result, loadLong(order + i * 8));
        }
        return result;
    }

    /** {@code _abi_dict_update(destination, source)}. */
    public static void _abi_dict_update(long destination, long source) {
        long length = loadLong(source + DICT_LEN_OFF);
        long order = loadLong(source + DICT_ORDER_OFF);
        for (long i = 0; i < length; i++) {
            long key = loadLong(order + i * 8);
            _abi_dict_set(destination, key, _abi_dict_get_default(source, key, 0));
        }
    }

    /**
     * {@code _abi_dict_pop(dict, key, fallback)} — tombstones the slot.
     *
     * <p>The order buffer is compacted rather than tombstoned, because it is
     * what iteration and rehashing walk; leaving a hole there would surface the
     * deleted key as a live one.
     */
    public static long _abi_dict_pop(long dict, long key, long fallback) {
        long slot = lookupSlot(dict, key);
        if (slot < 0) {
            return fallback;
        }
        long value = loadLong(slot + 8);
        long stored = loadLong(slot);
        storeLong(slot, SLOT_TOMBSTONE);
        storeLong(slot + 8, 0);
        storeLong(dict + DICT_TOMB_OFF, loadLong(dict + DICT_TOMB_OFF) + 1);

        long length = loadLong(dict + DICT_LEN_OFF);
        long order = loadLong(dict + DICT_ORDER_OFF);
        boolean shifting = false;
        for (long i = 0; i < length; i++) {
            if (!shifting && loadLong(order + i * 8) == stored) {
                shifting = true;
            }
            if (shifting && i + 1 < length) {
                storeLong(order + i * 8, loadLong(order + (i + 1) * 8));
            }
        }
        storeLong(dict + DICT_LEN_OFF, length - 1);
        return value;
    }

    public static void _abi_dict_clear(long dict) {
        long cap = loadLong(dict + DICT_CAP_OFF);
        long buffer = loadLong(dict + DICT_BUF_OFF);
        for (long i = 0; i < cap; i++) {
            storeLong(buffer + i * DICT_SLOT_SIZE, SLOT_EMPTY);
            storeLong(buffer + i * DICT_SLOT_SIZE + 8, 0);
        }
        storeLong(dict + DICT_LEN_OFF, 0);
        storeLong(dict + DICT_TOMB_OFF, 0);
    }

    // ======================================================================
    // boxed scalars
    // ======================================================================

    /**
     * {@code _abi_new_box(tag, payload)} — a tagged cell for an "any" slot.
     *
     * <p>A float's payload arrives already bit-cast to a long, so this stores
     * words and never interprets them; only the tag says what they mean.
     */
    public static long _abi_new_box(long tag, long payload) {
        long cell = allocate(BOX_SIZE);
        storeLong(cell, BOX_MAGIC);
        storeLong(cell + BOX_TAG_OFF, tag);
        storeLong(cell + BOX_PAYLOAD_OFF, payload);
        return cell;
    }

    public static long isBox(long value) {
        if (value <= 0 || value + BOX_SIZE > HEAP_BYTES) {
            return 0;
        }
        return loadLong(value) == BOX_MAGIC ? 1 : 0;
    }

    // ======================================================================
    // raising
    // ======================================================================
    //
    // asmpython lowers try/except to setjmp/longjmp. The JVM has no setjmp,
    // but throw already unwinds the stack, which is the hard half of longjmp
    // done for free. What a raise still has to do is publish the exception
    // where the generated handler will look for it: two heap cells the module
    // allocates and registers here at class-initialisation time.

    private static long excMessageCell;
    private static long excTypeCell;

    /**
     * Tell the runtime where this module keeps its exception state.
     *
     * <p>The cells belong to the generated class, not to the runtime — the
     * lowering reads them as ordinary globals — so their addresses have to
     * come the other way. Registering once beats threading them through every
     * raise.
     */
    public static void installExceptionSlots(long messageCell, long typeCell) {
        excMessageCell = messageCell;
        excTypeCell = typeCell;
    }

    /** {@code _abi_raise(message, typeId)} — publish, then unwind. */
    public static void _abi_raise(long message, long code) {
        if (excMessageCell != 0) {
            storeLong(excMessageCell, message);
            storeLong(excTypeCell, code);
        }
        throw new AsmPythonError(readString(message), code);
    }

    /**
     * Keep unwinding after a landing pad decides the live handler is not its
     * own.
     *
     * <p>Rebuilt from the published state rather than rethrowing the caught
     * object: holding the original would need a reference local in the
     * generated method, and every local there is a long or double — which is
     * exactly what lets the StackMapTable be one repeated frame.
     */
    public static void _abi_rethrow() {
        long message = excMessageCell == 0 ? 0 : loadLong(excMessageCell);
        long code = excTypeCell == 0 ? 0 : loadLong(excTypeCell);
        throw new AsmPythonError(message == 0 ? "" : readString(message), code);
    }

    /** A raised Python exception, carrying the type id the lowering assigned. */
    public static class AsmPythonError extends RuntimeException {
        private static final long serialVersionUID = 1L;

        public final long code;

        public AsmPythonError(String message, long code) {
            super(message);
            this.code = code;
        }
    }
}
