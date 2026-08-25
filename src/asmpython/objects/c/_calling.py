"""The object runtime, in C: calling, type objects and operator dispatch.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * calling
  * type objects
  * operator dispatch to user methods
"""

C = r"""/* --- calling ------------------------------------------------------------ */

/* The arity switch. Nine is the ceiling because `env` occupies one of the
   platform's argument registers and eight declared parameters is already far
   past anything the suite writes; a tenth would be another line here and no
   new idea. Every cast is to a function of `apy_value` arguments returning
   one, which is what every dynamic function compiles to. */
typedef apy_value (*apy_fn0)(apy_value);
typedef apy_value (*apy_fn1)(apy_value, apy_value);
typedef apy_value (*apy_fn2)(apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn3)(apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn4)(apy_value, apy_value, apy_value, apy_value,
                             apy_value);
typedef apy_value (*apy_fn5)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value);
typedef apy_value (*apy_fn6)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn7)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn8)(apy_value, apy_value, apy_value, apy_value,
                             apy_value, apy_value, apy_value, apy_value,
                             apy_value);
/* PAST EIGHT. `datetime.replace` declares ten parameters and is ordinary
   Python, so the old ceiling was a limit of this dispatch rather than of the
   language. Written out because a call through a function pointer needs the
   exact arity at the call site -- there is no way to spell "n arguments" in C
   without varargs, and varargs would change the ABI every backend shares. */
typedef apy_value (*apy_fn9)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn10)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn11)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn12)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn13)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn14)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn15)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);
typedef apy_value (*apy_fn16)(apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value, apy_value);

/* One value per selector, so `super().__init__ is super().__init__` holds
   the way it does for any other attribute reached twice -- and so the repr
   does not depend on how many times it was asked for. Binding copies it, as
   binding any function does. */
/* Declared here because the exported half calls it. */
static apy_value apy_native(int sel, int64_t arity, const char *name);
APY_API apy_value apy_native_of(int64_t sel, int64_t arity,
                                apy_value name) {
    return apy_native((int)sel, arity, (const char *)name);
}
static apy_value apy_native(int sel, int64_t arity, const char *name) {
    static apy_value made[APY_NAT_GEN_CLOSE + 1];
    apy_obj *o;
    /* INTERNED PER SELECTOR, so `super().__init__` reached twice is one
       object as any other attribute would be -- EXCEPT for the one selector
       whose name and arity vary, where interning handed back whichever was
       built first and every builtin protocol method then behaved as that one.
       A fresh object there is also what CPython answers: `[].append is
       [].append` is False. */
    if (sel != APY_NAT_KIND && made[sel]) return made[sel];
    o = apy_alloc(APY_FUNC_K);
    o->v.fn.code = 0;
    o->v.fn.native = sel;
    o->v.fn.arity = arity;
    o->v.fn.name = apy_lit(name);
    /* THE ARGUMENT IS OPTIONAL for the two that stand in for a builtin base's
       constructor, because `dict.__init__` takes nought or one and
       `super().__init__()` with nothing is the ordinary spelling. The arity
       check further down fills a missing trailing slot from `defaults` and
       then insists the count matches exactly, so an omitted argument was an
       arity error naming a method the program never declared. */
    if (sel == APY_NAT_BUILTIN_INIT || sel == APY_NAT_BUILTIN_NEW) {
        static apy_value absent[1];
        absent[0] = apy_none();
        o->v.fn.ndefaults = 1;
        o->v.fn.defaults = absent;
    }
    if (sel == APY_NAT_KIND) return V(o);
    made[sel] = V(o);
    return made[sel];
}

/* `type(name, bases, ns)` as an OBJECT: what `super().__new__` inside a
   metaclass's `__new__` answers. The class it builds records `mcls` as its
   metaclass, which is what makes `type(C)` say `Meta`. */
/* THE C3 LINEARISATION: the order attribute lookup walks.

   With one base it is the base chain and nothing is gained by computing it.
   With several it is the only order that keeps two promises at once -- a class
   comes before its bases, and the bases keep the order they were written in --
   and no simple walk can keep both. `class D(B, C)` over a diamond has to find
   B's method before C's and C's before A's, which depth-first does not.

   Answers 0 and reports a TypeError when no order satisfies both, which is
   what CPython does for `class Z(X, Y)` where X and Y disagree. */
static apy_value apy_c3(apy_value cls, apy_value bases) {
    apy_value out = apy_tuple_new(8);
    apy_value queues[9];
    int64_t heads[9], count = 0, i, j, k;
    int64_t nbases = (bases && apy_is_seq(bases)) ? O(bases)->v.q.n : 0;
    apy_seq_push(out, cls);
    if (nbases > 8) return apy_fail("TypeError", "too many bases");
    /* THE LISTS TO MERGE: each base's own linearisation, then the list of
       bases itself -- which is what makes the written order binding. */
    for (i = 0; i < nbases; i++) {
        apy_value b = O(bases)->v.q.items[i];
        if (O(b)->kind != APY_TYPE_K)
            return apy_fail("TypeError", "a base must be a class");
        queues[count] = O(b)->v.t.mro ? O(b)->v.t.mro : 0;
        if (!queues[count]) {
            /* A base with no recorded MRO is its own chain. */
            apy_value chain = apy_tuple_new(4);
            apy_value walk = b;
            while (walk && O(walk)->kind == APY_TYPE_K) {
                apy_seq_push(chain, walk);
                walk = O(walk)->v.t.base;
            }
            queues[count] = chain;
        }
        heads[count] = 0;
        count++;
    }
    queues[count] = bases ? bases : apy_tuple_new(1);
    heads[count] = 0;
    count++;
    for (;;) {
        apy_value chosen = 0;
        int64_t done = 1;
        for (i = 0; i < count; i++)
            if (heads[i] < O(queues[i])->v.q.n) { done = 0; break; }
        if (done) break;
        /* THE FIRST HEAD THAT IS IN NO TAIL. A candidate appearing in some
           other list's tail must wait for that list, or the result would put
           it before something that has to come first. */
        for (i = 0; i < count && !chosen; i++) {
            apy_value head;
            int blocked = 0;
            if (heads[i] >= O(queues[i])->v.q.n) continue;
            head = O(queues[i])->v.q.items[heads[i]];
            for (j = 0; j < count && !blocked; j++)
                for (k = heads[j] + 1; k < O(queues[j])->v.q.n; k++)
                    if (O(queues[j])->v.q.items[k] == head) { blocked = 1; break; }
            if (!blocked) chosen = head;
        }
        if (!chosen)
            return apy_fail("TypeError",
                            "Cannot create a consistent method resolution "
                            "order (MRO) for bases");
        {
            int64_t already = 0;
            for (i = 0; i < O(out)->v.q.n; i++)
                if (O(out)->v.q.items[i] == chosen) { already = 1; break; }
            if (!already) apy_seq_push(out, chosen);
        }
        for (i = 0; i < count; i++)
            if (heads[i] < O(queues[i])->v.q.n
                && O(queues[i])->v.q.items[heads[i]] == chosen)
                heads[i]++;
    }
    return out;
}

static apy_value apy_type_from_ns(apy_value mcls, apy_value name,
                                  apy_value bases, apy_value ns) {
    apy_value base = 0, cls;
    int64_t i;
    if (bases && apy_is_seq(bases) && O(bases)->v.q.n > 0)
        base = O(bases)->v.q.items[0];
    cls = apy_type_new(name, base ? base : apy_none());
    if (!cls) return 0;
    if (mcls && O(mcls)->kind == APY_TYPE_K) O(cls)->v.t.meta = mcls;
    if (bases && apy_is_seq(bases) && O(bases)->v.q.n > 0) {
        apy_value order;
        O(cls)->v.t.bases = bases;
        order = apy_c3(cls, bases);
        if (!order) return 0;
        O(cls)->v.t.mro = order;
    }
    /* The namespace is COPIED IN, not adopted: `__prepare__` may hand back a
       mapping the program goes on using, and a class that shared it would see
       later writes to it. */
    if (ns && O(ns)->kind == APY_DICT_K)
        for (i = 0; i < O(ns)->v.d.n; i++)
            apy_dict_set(O(cls)->v.t.dict, O(ns)->v.d.keys[i],
                         O(ns)->v.d.vals[i]);
    return cls;
}

/* `type` AS A CLASS OBJECT, so `class Meta(type)` has a real base rather
   than a special case. Its dict holds the two natives a metaclass reaches
   through `super()`, which is what makes `super().__new__(mcls, name, bases,
   ns)` build a class instead of an instance. Interned: `Meta.__base__ is
   type` has to hold, and two of them would make it False. */
/* PEP 3115: the mapping a class body is executed into. A metaclass may
   supply one through `__prepare__`, which is how a body's bindings can be
   seen in order or pre-seeded; a metaclass without one gets a plain dict.

   Asked for even when there is no `__prepare__`, so the body always writes
   into a mapping rather than into the type -- one lowering for both. */
APY_API apy_value apy_prepare(apy_value meta, apy_value name, apy_value bases) {
    apy_value hook, args[2], got;
    if (!meta || O(meta)->kind != APY_TYPE_K) return apy_dict_new(8);
    hook = apy_class_find(meta, apy_name("__prepare__"));
    if (!hook) return apy_dict_new(8);
    /* AN IMPLICIT CLASSMETHOD, like `__init_subclass__`: it receives the
       metaclass, and a program writes `@classmethod` above it because
       CPython wants that spelling -- the descriptor is unwrapped here. */
    if (O(hook)->kind == APY_PROP_K && O(hook)->v.p.get) {
        /* THE METACLASS IS ITS FIRST ARGUMENT, which is what `@classmethod`
           on it means -- unwrapping the descriptor without supplying that
           called a three-parameter function with two. */
        apy_value three[3];
        three[0] = meta;
        three[1] = name;
        three[2] = bases;
        got = apy_call_n(O(hook)->v.p.get, three, 3);
    } else {
        args[0] = name;
        args[1] = bases;
        got = apy_call_n(hook, args, 2);
    }
    if (!got) return 0;
    if (O(got)->kind != APY_DICT_K)
        return apy_fail("TypeError",
                        "__prepare__() must return a mapping");
    return got;
}

/* `type(name, bases, ns)` -- the three-argument form, which is the `class`
   statement written out. The same builder a metaclass's `super().__new__`
   reaches, with no metaclass recorded: one made this way IS a plain `type`. */
APY_API apy_value apy_type_make(apy_value name, apy_value bases,
                                apy_value ns) {
    return apy_type_from_ns(0, name, bases, ns);
}

/* THE METACLASS A `class` STATEMENT SHOULD USE: the one written, or the one
   its base already has. A subclass of a class with a metaclass has the same
   metaclass -- that is what makes `class Shape(ABC)` collect its own abstract
   methods without repeating `metaclass=ABCMeta`. */
/* PEP 560: WHAT A NON-CLASS BASE CONTRIBUTES.

   `class C(Fake())` asks the object for `__mro_entries__(bases)` and inherits
   whatever it answers -- which is how a generic alias resolves to its origin
   and how a library builds a base at run time. A class contributes itself,
   which is the ordinary case and costs one test. */
APY_API apy_value apy_mro_entries(apy_value written, apy_value bases) {
    apy_value hook, got;
    if (O(written)->kind == APY_TYPE_K) return written;
    if (O(written)->kind != APY_INST_K)
        return apy_fail2("TypeError", "bases must be types, not '%s'%s",
                         apy_kind_name(written), "");
    hook = apy_class_find(O(written)->v.o.cls, apy_name("__mro_entries__"));
    if (!hook)
        return apy_fail2("TypeError", "bases must be types, not '%s'%s",
                         apy_kind_name(written), "");
    got = apy_call_n(apy_bind(hook, written), &bases, 1);
    if (!got) return 0;
    /* THE FIRST ENTRY. `__mro_entries__` answers a tuple because one object
       may contribute several bases; this runtime linearises from a flat list,
       so the rest would need splicing into the caller's tuple -- which is
       what the caller does, one entry at a time. */
    if (apy_is_seq(got) && O(got)->v.q.n > 0) return O(got)->v.q.items[0];
    return apy_object_class();
}

APY_API apy_value apy_meta_for(apy_value given, apy_value bases) {
    int64_t i;
    if (given && O(given)->kind == APY_TYPE_K) return given;
    if (bases && apy_is_seq(bases))
        for (i = 0; i < O(bases)->v.q.n; i++) {
            apy_value base = O(bases)->v.q.items[i];
            if (O(base)->kind == APY_TYPE_K && O(base)->v.t.meta)
                return O(base)->v.t.meta;
        }
    return apy_none();
}

/* Build the class a `class` statement describes.

   THROUGH THE METACLASS when there is one, which is what makes `ABCMeta`
   able to refuse an instantiation and `EnumMeta` able to rewrite the body.
   Without one this is the plain construction, and the two paths meet here so
   the lowering does not have to know which it is -- it cannot, because
   whether a base carries a metaclass is a run-time question. */
APY_API apy_value apy_class_build_kw(apy_value meta, apy_value name,
                                     apy_value bases, apy_value ns,
                                     apy_value kw) {
    apy_value use = apy_meta_for(meta, bases);
    if (use && O(use)->kind == APY_TYPE_K) {
        apy_value argv[3];
        argv[0] = name;
        argv[1] = bases;
        argv[2] = ns;
        /* THE CLASS KEYWORDS GO TO THE METACLASS -- `class C(metaclass=M,
           kind="x")` is `M(name, bases, ns, kind="x")`. Only when there IS
           one: without a metaclass they are for `__init_subclass__`, which
           the caller announces separately, and handing them to the plain
           construction would make them an arity error. */
        if (kw && O(kw)->kind == APY_DICT_K && O(kw)->v.d.n)
            return apy_call_kw(use, (apy_value)(uintptr_t)argv, 3, kw);
        return apy_call_n(use, argv, 3);
    }
    return apy_type_from_ns(0, name, bases, ns);
}

APY_API apy_value apy_class_build(apy_value meta, apy_value name,
                                  apy_value bases, apy_value ns) {
    return apy_class_build_kw(meta, name, bases, ns, 0);
}

/* `object` AS A CLASS OBJECT -- what `C.__base__` answers for a class with
   no written base, and what `C.__bases__` holds. Its dict carries the same
   defaults `super()` falls back to.

   NOT INSTALLED AS AN ACTUAL BASE POINTER on every class. It is the honest
   ANSWER to a question about the hierarchy; making it a real link would put
   `__eq__` and friends into every `apy_class_find` walk, which changes what
   `hasattr` says about classes that define none of them. */
APY_API apy_value apy_object_class(void) {
    static apy_value cls = 0;
    static const char *names[] = {"__init__", "__new__", "__repr__", "__str__",
                                  "__eq__", "__ne__", "__hash__",
                                  "__getattribute__", "__setattr__",
                                  "__delattr__"};
    size_t i;
    if (cls) return cls;
    cls = apy_type_new(apy_lit("object"), 0);
    for (i = 0; i < sizeof names / sizeof names[0]; i++)
        apy_dict_set(O(cls)->v.t.dict, apy_name(names[i]),
                     apy_object_default(
                         (apy_value)(uintptr_t)names[i]));
    return cls;
}

APY_API apy_value apy_type_class(void) {
    static apy_value cls = 0;
    if (cls) return cls;
    cls = apy_type_new(apy_lit("type"), 0);
    apy_dict_set(O(cls)->v.t.dict, apy_name("__new__"),
                 apy_native(APY_NAT_TYPE_NEW, 4, "__new__"));
    apy_dict_set(O(cls)->v.t.dict, apy_name("__init__"),
                 apy_native(APY_NAT_TYPE_INIT, 4, "__init__"));
    /* What `C(...)` MEANS, reachable by name so a metaclass's own `__call__`
       can delegate to it. */
    apy_dict_set(O(cls)->v.t.dict, apy_name("__call__"),
                 apy_native(APY_NAT_TYPE_CALL, 1, "__call__"));
    return cls;
}

/* `__class__` for a kind with no attributes of its own -- a generic alias,
   a slice, a view. INTERNED per kind name, because the object a program
   compares or reads `__name__` off has to be the same one each time. */
APY_API apy_value apy_kind_class(apy_value obj) {
    static apy_value classes = 0;
    apy_value key = apy_lit(apy_kind_name(obj)), found;
    if (!classes) classes = apy_dict_new(8);
    found = apy_dict_get_or(classes, key, 0);
    if (found) return found;
    found = apy_type_new(key, 0);
    apy_dict_set(classes, key, found);
    return found;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

static apy_value apy_instantiate(apy_value f, apy_value *argv, int64_t argc,
                                 apy_value kwrest, int bound);
static apy_value apy_call_nk(apy_value f, apy_value *argv, int64_t argc,
                             apy_value kwrest, int bound);

/* A BUILTIN'S PROTOCOL METHODS, AS VALUES.

   `[].append` and `{}.keys` are lowered at the call site by the frontend,
   which means they exist as CALLS and never as attributes -- so
   `hasattr([1], "__iter__")` answered False for the most iterable object in
   the language, and every structural type test written against
   `collections.abc` said no.

   This does not make the whole method table reachable by name; it makes the
   PROTOCOL reachable, which is the part a program asks about rather than
   calls. Answers 0 for a name the kind does not have -- that is what keeps
   `hasattr` honest -- and `None` where CPython HAS the attribute and sets it
   to None, which is how a mutable container says it cannot be hashed. */
/* One protocol method as a value, bound to `obj` when there IS one. A TYPE
   asked the same question has no receiver -- `dict.keys` is unbound in
   CPython too, and `dict.keys(d)` is how it is called. */
APY_API apy_value apy_kind_method_of(apy_value obj, int64_t arity,
                                    apy_value namev, int64_t bind) {
    const char *name = (const char *)namev;
    apy_value fn = apy_native(APY_NAT_KIND, arity, name);
    return bind ? apy_bind(fn, obj) : fn;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_kind_method(apy_value obj, int64_t arity,
                                 const char *name, int bind) {
    return apy_kind_method_of(obj, arity,
                              (apy_value)(uintptr_t)name,
                              (int64_t)bind);
}

/* An EMPTY VALUE of the kind a builtin type names, so the table above can
   answer for the type without a second copy of it. Nothing is done with the
   prototype but ask its kind, and the natives it yields are unbound. */
APY_API apy_value apy_kind_prototype(apy_value type_namev) {
    const char *type_name = (const char *)type_namev;
    if (strcmp(type_name, "list") == 0)  return apy_list_new(1);
    if (strcmp(type_name, "tuple") == 0) return apy_tuple_new(1);
    if (strcmp(type_name, "dict") == 0)  return apy_dict_new(1);
    if (strcmp(type_name, "set") == 0)   return apy_set_new(1);
    if (strcmp(type_name, "frozenset") == 0) return apy_frozenset_new(1);
    if (strcmp(type_name, "str") == 0)   return apy_lit("");
    if (strcmp(type_name, "bytes") == 0) return apy_bytes_copy("", 0);
    if (strcmp(type_name, "int") == 0 || strcmp(type_name, "bool") == 0)
        return apy_from_int(0);
    if (strcmp(type_name, "float") == 0) return apy_from_float(0.0);
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

APY_API apy_value apy_kind_attr_of(apy_value obj, apy_value wantv,
                                  int64_t bind) {
    const char *want = (const char *)wantv;
    int k = O(obj)->kind;
    int seq = apy_is_seq(obj), set = apy_is_set(obj);
    int text = k == APY_STR_K || k == APY_BYTES_K;
    int dict = k == APY_DICT_K;
    int walks = seq || set || text || dict || k == APY_MVIEW_K
                || k == APY_VIEW_K;
    int mutable_ = k == APY_LIST_K || k == APY_DICT_K || k == APY_SET_K
                   || (k == APY_BYTES_K && O(obj)->v.s.mut);

    if (strcmp(want, "__hash__") == 0) {
        /* THE ATTRIBUTE EXISTS EITHER WAY. `[].__hash__ is None` is how a
           program asks whether a list can be a dict key, and answering "no
           such attribute" is a different claim from the one CPython makes. */
        if (mutable_) return apy_none();
        return apy_kind_method(obj, 1, "__hash__", bind);
    }
    if (strcmp(want, "__len__") == 0 && walks)
        return apy_kind_method(obj, 1, "__len__", bind);
    if (strcmp(want, "__iter__") == 0
            && (walks || k == APY_GEN_K || k == APY_ITER_K))
        return apy_kind_method(obj, 1, "__iter__", bind);
    if (strcmp(want, "__next__") == 0
            && (k == APY_GEN_K || k == APY_ITER_K))
        return apy_kind_method(obj, 1, "__next__", bind);
    if (strcmp(want, "__contains__") == 0 && walks)
        return apy_kind_method(obj, 2, "__contains__", bind);
    if (strcmp(want, "__getitem__") == 0
            && (seq || text || dict || k == APY_MVIEW_K))
        return apy_kind_method(obj, 2, "__getitem__", bind);
    if (strcmp(want, "__setitem__") == 0
            && (k == APY_LIST_K || dict
                || (k == APY_BYTES_K && O(obj)->v.s.mut)))
        return apy_kind_method(obj, 3, "__setitem__", bind);
    if (dict && (strcmp(want, "keys") == 0 || strcmp(want, "values") == 0
                 || strcmp(want, "items") == 0))
        return apy_kind_method(obj, 1, want, bind);
    if ((seq || text) && (strcmp(want, "index") == 0
                          || strcmp(want, "count") == 0))
        return apy_kind_method(obj, 2, want, bind);
    if (k == APY_LIST_K && strcmp(want, "append") == 0)
        return apy_kind_method(obj, 2, "append", bind);
    if (k == APY_LIST_K && strcmp(want, "insert") == 0)
        return apy_kind_method(obj, 3, "insert", bind);
    if (k == APY_SET_K && (strcmp(want, "add") == 0
                           || strcmp(want, "discard") == 0))
        return apy_kind_method(obj, 2, want, bind);
    if (set && strcmp(want, "isdisjoint") == 0)
        return apy_kind_method(obj, 2, "isdisjoint", bind);
    /* PEP 688: whatever can be handed to `memoryview` HAS `__buffer__`. It is
       a protocol a program asks about far more often than it calls, and
       answering False for `bytes` said this runtime has no buffers at all. */
    if (strcmp(want, "__buffer__") == 0
            && (k == APY_BYTES_K || k == APY_MVIEW_K))
        return apy_kind_method(obj, 2, "__buffer__", bind);
    if (k == APY_RANGE_K) {
        /* THE THREE NUMBERS A RANGE IS, read back. */
        if (strcmp(want, "start") == 0)
            return apy_from_int(O(obj)->v.rg.start);
        if (strcmp(want, "stop") == 0)
            return apy_from_int(O(obj)->v.rg.stop);
        if (strcmp(want, "step") == 0)
            return apy_from_int(O(obj)->v.rg.step);
        if (strcmp(want, "index") == 0 || strcmp(want, "count") == 0)
            return apy_kind_method(obj, 2, want, bind);
        if (strcmp(want, "__len__") == 0)
            return apy_kind_method(obj, 1, "__len__", bind);
        if (strcmp(want, "__iter__") == 0)
            return apy_kind_method(obj, 1, "__iter__", bind);
        if (strcmp(want, "__contains__") == 0)
            return apy_kind_method(obj, 2, "__contains__", bind);
        if (strcmp(want, "__getitem__") == 0)
            return apy_kind_method(obj, 2, "__getitem__", bind);
    }
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

APY_API apy_value apy_kind_attr(apy_value obj, apy_value wantv) {
    const char *want = (const char *)wantv;
    return apy_kind_attr_of(obj, wantv, 1);
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */

static apy_value apy_native_call(apy_value f, apy_value *a, int64_t n) {
    switch (O(f)->v.fn.native) {
    case APY_NAT_POSITIONS:
        /* `code.co_positions()` -- what `apy_code_of` recorded, handed back
           as it stands. A method rather than an attribute because that is how
           CPython spells it, and a program calls it. */
        if (n < 1) return apy_fail("TypeError", "unbound builtin method");
        return apy_getattr(a[0], apy_lit("_positions"));
    case APY_NAT_TASK_CANCEL:
        return n < 1 ? 0 : apy_task_cancel(a[0]);
    case APY_NAT_TASK_RESULT:
        return n < 1 ? 0 : apy_task_result(a[0]);
    case APY_NAT_TASK_DONE:
        return n < 1 ? 0 : apy_task_done(a[0]);
    case APY_NAT_TASK_CANCELLED:
        return n < 1 ? 0 : apy_task_cancelled(a[0]);
    case APY_NAT_TG_ENTER:
        /* THE GROUP ITSELF is what `async with ... as tg` binds. */
        return n < 1 ? 0 : apy_coro_value(a[0]);
    case APY_NAT_TG_CREATE: {
        apy_value t;
        if (n < 2) return apy_fail("TypeError",
                                   "create_task() takes a coroutine");
        t = apy_asyncio_create_task(a[1]);
        if (!t) return 0;
        apy_seq_push(apy_getattr(a[0], apy_lit("_tasks")), t);
        return t;
    }
    case APY_NAT_TG_EXIT: {
        apy_value g;
        if (n < 1) return 0;
        g = apy_gen_new(0, 1);
        O(g)->v.g.coro = 1;
        O(g)->v.g.builtin = APY_CORO_TGWAIT;
        O(g)->v.g.slots[0] = apy_getattr(a[0], apy_lit("_tasks"));
        if (!O(g)->v.g.slots[0]) return 0;
        return g;
    }
    case APY_NAT_INIT:     return apy_none();
    case APY_NAT_BUILTIN_NEW: {
        /* `super().__new__(cls, x)` PAST A BUILTIN BASE, which is the only
           way to build an immutable one: a tuple's contents cannot be set
           after it exists, so `class P(tuple)` fills it here or never.
           `apy_object_default("__new__")` would answer a bare instance with
           an EMPTY tuple inside -- a wrong value rather than an error, and
           the shape every `namedtuple` would have had.

           THE CLASS IS THE FIRST ARGUMENT and is not a bound receiver:
           `__new__` is an implicit staticmethod, so the caller writes the
           class out. */
        apy_value cls, made;
        int kind;
        if (n < 1) return apy_fail("TypeError", "unbound builtin method");
        cls = a[0];
        if (O(cls)->kind != APY_TYPE_K)
            return apy_fail("TypeError",
                            "__new__() argument 1 must be a type");
        made = apy_instance_new(cls);
        if (!made) return 0;
        if (n > 1 && O(a[1])->kind != APY_NONE_K
                && O(made)->kind == APY_INST_K && O(made)->v.o.held) {
            apy_value filled = apy_call_kind(O(O(made)->v.o.held)->kind, a[1]);
            if (!filled) return 0;
            O(made)->v.o.held = filled;
        }
        return made;
    }
    case APY_NAT_BUILTIN_INIT: {
        /* `super().__init__(...)` where the base chain ends at a BUILTIN.
           `class M(dict)` writing it means `dict.__init__`, which FILLS the
           instance -- and there is no Python above `M` to find that on, so
           the walk ran out, `object.__init__` answered, and the call quietly
           did nothing. An empty dict where the program asked for a full one
           is the failure this arrangement is worst at showing: no error, just
           a container that is not there.

           IN PLACE, not a new object: the lines after `super().__init__()` in
           the same body go on using the instance the caller already has. */
        apy_value self, made;
        if (n < 1) return apy_fail("TypeError", "unbound builtin method");
        self = a[0];
        if (O(self)->kind != APY_INST_K || !O(self)->v.o.held)
            return apy_none();
        /* NONE MEANS OMITTED, since the default above always fills the
           slot. `super().__init__(None)` written out is therefore the same as
           `super().__init__()` -- no builtin constructor takes None as
           content, so nothing is lost. */
        if (n < 2 || O(a[1])->kind == APY_NONE_K) return apy_none();
        made = apy_call_kind(O(O(self)->v.o.held)->kind, a[1]);
        if (!made) return 0;
        O(self)->v.o.held = made;
        return apy_none();
    }
    case APY_NAT_EXC_INIT: {
        /* `BaseException.__init__(*args)`: it SETS THE MESSAGE AND `args`,
           which is the whole of what it does and the reason a class writing
           `super().__init__(f"{code}: {message}")` prints that text. */
        apy_value exc, tuple;
        int64_t i;
        if (n < 1) return apy_fail("TypeError", "unbound builtin method");
        exc = a[0];
        if (O(exc)->kind != APY_EXC_K) return apy_none();
        tuple = apy_tuple_new(n > 1 ? n - 1 : 1);
        for (i = 1; i < n; i++) apy_seq_push(tuple, a[i]);
        O(exc)->v.e.arg = n > 1 ? a[1] : apy_none();
        O(exc)->v.e.has_arg = n > 1;
        O(exc)->v.e.argv = tuple;
        /* The text is the ARGUMENT again, not something already rendered --
           see `rendered` on the cell for what that flag stops twice-over. */
        O(exc)->v.e.rendered = 0;
        return apy_none();
    }
    case APY_NAT_NEW:
        /* `object.__new__(cls)`. The CLASS is the argument, not an instance:
           it is an implicit staticmethod, which is why a bound one still
           receives the class in `a[0]`. */
        if (n < 1 || O(a[0])->kind != APY_TYPE_K)
            return apy_fail("TypeError", "object.__new__(): not a type");
        return apy_instance_new(a[0]);
    case APY_NAT_REPR:
    case APY_NAT_STR:      return n < 1 ? 0 : apy_default_repr(a[0]);
    case APY_NAT_EQ:       return n < 2 ? 0 : apy_default_eq(a[0], a[1]);
    case APY_NAT_NE: {
        apy_value r = n < 2 ? 0 : apy_default_eq(a[0], a[1]);
        return r ? apy_from_bool(!apy_truth(r)) : r;
    }
    case APY_NAT_HASH:     return n < 1 ? 0 : apy_default_hash(a[0]);
    case APY_NAT_GETATTR:  return n < 2 ? 0 : apy_default_getattr(a[0], a[1]);
    case APY_NAT_SETATTR:
        return n < 3 ? 0 : apy_default_setattr(a[0], a[1], a[2]);
    case APY_NAT_DELATTR:  return n < 2 ? 0 : apy_default_delattr(a[0], a[1]);
    case APY_NAT_TYPE_NEW:
        if (n < 4)
            return apy_fail("TypeError",
                            "type.__new__() takes 4 arguments");
        return apy_type_from_ns(a[0], a[1], a[2], a[3]);
    case APY_NAT_TYPE_INIT:
    case APY_NAT_INIT_SUBCLASS: return apy_none();
    /* THE DESCRIPTOR PROTOCOL AS VALUES. A property answers `hasattr(p,
       "__get__")` with True in CPython because the methods exist; here they
       existed as runtime behaviour with nothing naming them, so a program
       that ASKS -- and `enum` asks, to tell a member from a method -- got
       False for a descriptor. */
    case APY_NAT_KIND: {
        /* ONE SELECTOR FOR THE LOT, dispatched on the name it carries: the
           bodies are all one existing runtime entry point, and a selector per
           name would be twenty enum members that differ only in which. */
        const char *w = APY_CSTR(O(f)->v.fn.name);
        if (n < 1) return apy_fail("TypeError", "unbound builtin method");
        if (strcmp(w, "__hash__") == 0) return apy_hash(a[0]);
        if (strcmp(w, "__len__") == 0) return apy_len(a[0]);
        if (strcmp(w, "__iter__") == 0) return apy_iter(a[0]);
        if (strcmp(w, "__next__") == 0) return apy_next(a[0], 0, 0);
        if (strcmp(w, "keys") == 0)
            return apy_dict_parts(a[0], APY_PART_KEYS);
        if (strcmp(w, "values") == 0)
            return apy_dict_parts(a[0], APY_PART_VALUES);
        if (strcmp(w, "items") == 0)
            return apy_dict_parts(a[0], APY_PART_ITEMS);
        if (n < 2) return apy_fail("TypeError",
                                   "builtin method takes an argument");
        /* `x in obj` -- the NEEDLE FIRST, which is the order `apy_contains`
           takes and the reverse of the method's. */
        if (strcmp(w, "__contains__") == 0) return apy_contains(a[1], a[0]);
        if (strcmp(w, "__getitem__") == 0) return apy_getitem(a[0], a[1]);
        if (O(a[0])->kind == APY_RANGE_K
                && (strcmp(w, "index") == 0 || strcmp(w, "count") == 0)) {
            int64_t want, at;
            if (!apy_is_int_like(a[1]))
                return strcmp(w, "count") == 0 ? apy_from_int(0)
                    : apy_fail("ValueError", "value is not in range");
            if (!apy_index_arg(a[1], &want, APY_IDX_SIZE)) return 0;
            at = apy_range_find(a[0], want);
            if (strcmp(w, "count") == 0) return apy_from_int(at >= 0 ? 1 : 0);
            if (at < 0) return apy_fail("ValueError",
                                        "value is not in range");
            return apy_from_int(at);
        }
        if (strcmp(w, "index") == 0) return apy_index_of(a[0], a[1]);
        if (strcmp(w, "count") == 0) return apy_count_of(a[0], a[1]);
        if (strcmp(w, "append") == 0) return apy_seq_push(a[0], a[1]);
        if (strcmp(w, "add") == 0) return apy_set_add(a[0], a[1]);
        if (strcmp(w, "discard") == 0) return apy_set_discard(a[0], a[1]);
        if (strcmp(w, "isdisjoint") == 0)
            return apy_set_isdisjoint(a[0], a[1]);
        /* `b.__buffer__(flags)` answers a memoryview over it, which is what
           the protocol is for and what `memoryview(b)` already does. */
        if (strcmp(w, "__buffer__") == 0) return apy_memoryview(a[0]);
        if (n < 3) return apy_fail("TypeError",
                                   "builtin method takes two arguments");
        if (strcmp(w, "__setitem__") == 0)
            return apy_setitem(a[0], a[1], a[2]);
        if (strcmp(w, "insert") == 0)
            return apy_list_insert(a[0], a[1], a[2]);
        return apy_fail("TypeError", "not callable");
    }
    case APY_NAT_DESCR_GET:
        if (n < 2) return apy_fail("TypeError",
                                   "__get__() takes at least 2 arguments");
        return apy_descr_get(a[0], a[1], n > 2 ? a[2] : 0);
    case APY_NAT_DESCR_SET:
        if (n < 3) return apy_fail("TypeError",
                                   "__set__() takes 3 arguments");
        return apy_descr_set(a[0], a[1], a[2]) ? apy_none() : 0;
    case APY_NAT_DESCR_DEL:
        if (n < 2) return apy_fail("TypeError",
                                   "__delete__() takes 2 arguments");
        return apy_descr_set(a[0], a[1], 0) ? apy_none() : 0;
    case APY_NAT_HAS_DEFAULT: {
        /* `T.has_default()` -- whether a default was written. */
        apy_value held;
        if (n < 1) return apy_from_bool(0);
        held = apy_dict_get_or(O(a[0])->v.o.dict, apy_lit("__default__"), 0);
        return apy_from_bool(held && O(held)->kind != APY_NONE_K);
    }
    case APY_NAT_TYPE_CALL:
        /* Only reached for a native called with no keywords; the path that
           carries them intercepts this selector where the dict is in hand. */
        if (n < 1) return apy_fail("TypeError", "type.__call__() needs a type");
        return apy_instantiate(a[0], a + 1, n - 1, 0, 0);
    case APY_NAT_GEN_SEND:
        return n < 2 ? 0 : apy_gen_send(a[0], a[1]);
    case APY_NAT_GEN_THROW:
        return n < 2 ? 0 : apy_gen_throw(a[0], a[1]);
    case APY_NAT_GEN_CLOSE:
        return n < 1 ? 0 : apy_gen_close(a[0]);
    default:
        return apy_fail("TypeError", "not callable");
    }
}

/* The object defaults, by name. `super()` on a class whose base chain has run
   out looks here, which is what makes `super().__init__()` in a class with no
   base do what CPython's `object.__init__` does rather than fail. */
APY_API apy_value apy_object_default(apy_value wantv) {
    const char *want = (const char *)wantv;
    if (strcmp(want, "__init__") == 0)
        return apy_native(APY_NAT_INIT, 1, "__init__");
    if (strcmp(want, "__new__") == 0)
        return apy_native(APY_NAT_NEW, 1, "__new__");
    if (strcmp(want, "__repr__") == 0)
        return apy_native(APY_NAT_REPR, 1, "__repr__");
    if (strcmp(want, "__str__") == 0)
        return apy_native(APY_NAT_STR, 1, "__str__");
    if (strcmp(want, "__eq__") == 0)
        return apy_native(APY_NAT_EQ, 2, "__eq__");
    if (strcmp(want, "__ne__") == 0)
        return apy_native(APY_NAT_NE, 2, "__ne__");
    if (strcmp(want, "__hash__") == 0)
        return apy_native(APY_NAT_HASH, 1, "__hash__");
    if (strcmp(want, "__getattribute__") == 0)
        return apy_native(APY_NAT_GETATTR, 2, "__getattribute__");
    if (strcmp(want, "__setattr__") == 0)
        return apy_native(APY_NAT_SETATTR, 3, "__setattr__");
    if (strcmp(want, "__delattr__") == 0)
        return apy_native(APY_NAT_DELATTR, 2, "__delattr__");
    /* Every class has one, and a user hook ends by calling it: `object`'s is
       the no-op that terminates the chain. */
    if (strcmp(want, "__init_subclass__") == 0)
        return apy_native(APY_NAT_INIT_SUBCLASS, 1, "__init_subclass__");
    return 0;
}

static apy_value apy_invoke(apy_value f, apy_value *a, int64_t n) {
    uintptr_t c = O(f)->v.fn.code;
    /* A NATIVE has no code pointer to call; the selector is the whole of it.
       Tested first, because calling through a null `code` is the crash this
       replaced. */
    if (O(f)->v.fn.native) return apy_native_call(f, a, n);
    switch (n) {
    case 0: return ((apy_fn0)c)(f);
    case 1: return ((apy_fn1)c)(f, a[0]);
    case 2: return ((apy_fn2)c)(f, a[0], a[1]);
    case 3: return ((apy_fn3)c)(f, a[0], a[1], a[2]);
    case 4: return ((apy_fn4)c)(f, a[0], a[1], a[2], a[3]);
    case 5: return ((apy_fn5)c)(f, a[0], a[1], a[2], a[3], a[4]);
    case 6: return ((apy_fn6)c)(f, a[0], a[1], a[2], a[3], a[4], a[5]);
    case 7: return ((apy_fn7)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6]);
    case 8: return ((apy_fn8)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6],
                                a[7]);
    case 9: return ((apy_fn9)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8]);
    case 10: return ((apy_fn10)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9]);
    case 11: return ((apy_fn11)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10]);
    case 12: return ((apy_fn12)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11]);
    case 13: return ((apy_fn13)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12]);
    case 14: return ((apy_fn14)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13]);
    case 15: return ((apy_fn15)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14]);
    case 16: return ((apy_fn16)c)(f, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15]);
    default:
        return apy_fail("TypeError", "a function of more than 16 parameters "
                                     "is not supported");
    }
}

static apy_value apy_arity_error(apy_value f, int64_t got) {
    char buf[192];
    /* POSITIONS, not declared slots. A keyword-only parameter is declared and
       cannot be filled by position, so counting it here told the caller to
       pass more positional arguments than the function accepts. */
    int64_t want = O(f)->v.fn.arity - (O(f)->v.fn.bound ? 1 : 0)
                   - (O(f)->v.fn.vararg ? 1 : 0) - (O(f)->v.fn.kwarg ? 1 : 0)
                   - O(f)->v.fn.kwonly;
    if (want < 0) want = 0;
    snprintf(buf, sizeof buf,
             "%s() takes %lld positional argument%s but %lld %s given",
             APY_CSTR(O(f)->v.fn.name), (long long)want,
             want == 1 ? "" : "s", (long long)got,
             got == 1 ? "was" : "were");
    return apy_fail("TypeError", buf);
}

/* One call, with the `**kw` dict the caller resolved (or 0 for none).

   Threaded as a parameter rather than appended to `argv` by the caller,
   because a callee with BOTH `*rest` and `**kw` packs the surplus positionals
   into `rest` FIRST -- a kw dict sitting in `argv` would be swallowed by that
   packing and arrive as one more element of `rest`. Only this function knows
   where the boundary is, so only it can put the dict past it. */
/* `C(...)` -- allocate, then run `__init__` if there is one. The instance is
   what the call yields whatever `__init__` returns, which is why its result is
   discarded rather than propagated.

   SEPARATE FROM `apy_call_nk` because `type.__call__` is exactly this and
   nothing else: a metaclass that overrides `__call__` and ends by delegating
   upward has to reach the default without re-entering the hook. */
/* `dict(x)`, `list(x)`, `tuple(x)`, `set(x)` or `str(x)`, chosen by KIND
   rather than by name -- the caller has an instance carrying one of these and
   wants another built from its argument, and the kind is what it knows.
   Assembled from the entry points the frontend already lowers those calls to,
   so there is no second opinion about what `dict(pairs)` means. */
static apy_value apy_call_kind(int kind, apy_value src) {
    apy_value out;
    /* AN INSTANCE ARGUMENT MEANS ITS CONTENT. `OrderedDict(other)` reaches
       here with an instance, and every branch below reads `src` as a real
       container -- see `_content_of` in the host for the same unwrap. */
    if (O(src)->kind == APY_INST_K && O(src)->v.o.held)
        src = O(src)->v.o.held;
    if (kind == APY_DICT_K) {
        out = apy_dict_new(4);
        return (out && apy_update(out, src)) ? out : 0;
    }
    /* A SET IS FILLED ONE ELEMENT AT A TIME and not by `apy_extend`, which
       pushes through `apy_seq_push` -- a list operation that reports
       `'set' object has no attribute 'append'` when handed one. Adding is
       also the only way to get the DEDUPLICATION a set is for. */
    if (kind == APY_SET_K) {
        /* THROUGH A LIST, which is not a detour. `apy_extend` is the one
           thing here that already knows how to drain every kind of source --
           a generator, a dict (its KEYS), a str -- and it pushes through
           `apy_seq_push`, a LIST operation that refuses a set outright.
           Collect with it, then add, which is also where the deduplication a
           set is for happens.

           NOT `apy_key_at`: that answers what ITERATING yields for a dict, a
           set or a cursor, and a plain list falls past those branches into
           the cursor one, where the union's `it.i` overlaps the element
           count. It read off the end and the program died with its buffered
           output lost, which is why this printed nothing at all. */
        apy_value tmp = apy_list_new(4);
        int64_t i;
        if (!tmp || !apy_extend(tmp, src)) return 0;
        out = apy_set_new(4);
        if (!out) return 0;
        for (i = 0; i < O(tmp)->v.q.n; i++)
            if (!apy_set_add(out, O(tmp)->v.q.items[i])) return 0;
        return out;
    }
    if (kind == APY_LIST_K || kind == APY_TUPLE_K) {
        out = kind == APY_LIST_K ? apy_list_new(4) : apy_tuple_new(4);
        return (out && apy_extend(out, src)) ? out : 0;
    }
    if (kind == APY_STR_K) return apy_str(src);
    return 0;
}

static apy_value apy_instantiate(apy_value f, apy_value *argv, int64_t argc,
                                 apy_value kwrest, int bound) {
    apy_value self;
    apy_value init;
    apy_value maker = apy_class_find(f, apy_name("__new__"));
    if (maker) {
        /* `__new__` IS AN IMPLICIT STATICMETHOD: it receives the CLASS as
           its first argument, not an instance, so it is called unbound
           with the class pushed in front. It was ignored entirely before
           -- the instance was allocated and `__new__` never ran, which is
           a wrong answer rather than a missing feature. */
        apy_value pushed[17];
        int64_t j;
        pushed[0] = f;
        for (j = 0; j < argc && j + 1 < 17; j++) pushed[j + 1] = argv[j];
        self = apy_call_nk(maker, pushed,
                           argc + 1 < 17 ? argc + 1 : 17, kwrest, 0);
        if (!self) return 0;
        /* `__init__` RUNS ONLY IF `__new__` RETURNED ONE OF THESE.
           Returning something else is how a `__new__` deliberately
           bypasses initialisation, and CPython honours that. */
        /* `__init__` RUNS WHEN `__new__` ANSWERED AN INSTANCE OF THIS
           CLASS -- and for a METACLASS the thing it answered is a class
           whose metaclass is this one, which is the same test through
           `apy_type_of`. Comparing only against `v.o.cls` skipped a
           metaclass's `__init__` entirely. */
        if (apy_type_of(self) != f)
            return self;
    } else {
        self = apy_instance_new(f);
        if (!self) return 0;
    }
    init = apy_class_find(f, apy_name("__init__"));
    if (init) {
        apy_value bound_init = apy_bind(init, self);
        /* A NATIVE `__init__` TAKES NO KEYWORDS. `type.__init__` is the one
           that matters: `class C(metaclass=M, kind="x")` hands the keywords
           to `M.__new__`, and CPython's `type.__init__` ignores them rather
           than reporting one it does not declare. */
        if (O(init)->kind == APY_FUNC_K && O(init)->v.fn.native) kwrest = 0;
        if (!apy_call_nk(bound_init, argv, argc, kwrest, bound)) return 0;
    } else if ((argc != 0 || kwrest) && !maker) {
        /* A CLASS EXTENDING A BUILTIN INHERITS ITS CONSTRUCTOR. `class
           L(list): pass` then `L([1, 2, 3])` is a list of three, because
           `list.__init__` is what the empty body left in place -- and
           `L() takes no arguments` names the wrong thing entirely: the class
           HAS a constructor, inherited, and the arguments are what it wants.

           ONE ARGUMENT, which is every builtin constructor that fills from
           something: `dict(pairs)`, `list(it)`, `tuple(it)`, `set(it)`. The
           keyword forms (`dict(a=1)`) reach `__init__` on a class that wrote
           one; a class that did not gets the refusal it had before.

           NONE OF THIS WHEN THE CLASS WROTE `__new__` -- see the `!maker` on
           the branch above. That constructor has already decided what the
           instance holds (a `namedtuple` packs its arguments into one tuple),
           and CPython draws the same line: `object.__init__` complains about
           surplus arguments only when `__new__` is not overridden. */
        if (O(self)->kind == APY_INST_K && O(self)->v.o.held && argc == 1) {
            apy_value made = apy_call_kind(
                O(O(self)->v.o.held)->kind, argv[0]);
            if (!made) return 0;
            O(self)->v.o.held = made;
            if (kwrest && O(made)->kind == APY_DICT_K
                    && !apy_update(made, kwrest)) return 0;
            return self;
        }
        /* KEYWORDS ALONE, which `dict` is the whole reason for: `class
           C(dict): pass` then `C(a=1)` has NO positional argument, so the
           branch above never ran and the guard on this one used to send it
           straight past -- the instance came back with an EMPTY dict and no
           error at all, which is worse than the refusal the comment above
           promises. */
        if (O(self)->kind == APY_INST_K && O(self)->v.o.held && argc == 0
                && kwrest && O(O(self)->v.o.held)->kind == APY_DICT_K) {
            if (!apy_update(O(self)->v.o.held, kwrest)) return 0;
            return self;
        }
        char buf[128];
        snprintf(buf, sizeof buf,
                 "%s() takes no arguments", APY_CSTR(O(f)->v.t.name));
        return apy_fail("TypeError", buf);
    }
    return self;
}

static apy_value apy_call_nk(apy_value f, apy_value *argv, int64_t argc,
                             apy_value kwrest, int bound) {
    apy_value slots[17];
    int64_t i, n = 0;

    if (O(f)->kind == APY_TYPE_K && O(f)->v.t.meta) {
        /* THE METACLASS DECIDES WHAT CALLING THE CLASS DOES, if it says so:
           `type(C).__call__(C, ...)` is what `C(...)` means, and it is how
           `ABCMeta` refuses to instantiate a class with abstract methods.
           Only when a `__call__` is actually written -- the default is the
           allocate-and-init below, and routing every class through a lookup
           that almost never finds anything would cost every instantiation. */
        apy_value hook = apy_class_find(O(f)->v.t.meta, apy_name("__call__"));
        if (hook) {
            apy_value pushed[17];
            int64_t j;
            pushed[0] = f;
            for (j = 0; j < argc && j + 1 < 17; j++) pushed[j + 1] = argv[j];
            return apy_call_nk(hook, pushed, argc + 1 < 17 ? argc + 1 : 17,
                               kwrest, 0);
        }
    }
    /* AN EXCEPTION TYPE IS CALLABLE, and reaching it through a variable is
       the only way to notice that it was not. `ValueError("v")` is resolved
       at the CALL SITE by the frontend and never arrives here; `c =
       ValueError; c("v")` does, and answered `ValueError() takes no
       arguments` -- about a class every program constructs.

       `warnings.warn` is why this surfaced: `raise category(message)` holds
       the class in a parameter, so the entire module was unwritable. A type
       that can only be called by the spelling the compiler recognises is not
       a value, and every library that takes an exception class as an
       argument depends on it being one.

       A type carries no argument; an INSTANCE does. That is the whole
       distinction here, and it is what stops `e = ValueError("v"); e()` from
       being read as a second construction. */
    if (O(f)->kind == APY_EXC_K && !O(f)->v.e.has_arg && !O(f)->v.e.argv)
        return apy_make_excn(apy_lit(O(f)->v.e.name), (apy_value)argv, argc);
    /* AND THE TYPE OBJECT THE NAME ACTUALLY ANSWERS, which is not an
       `APY_EXC_K` at all. `apy_exc_type` builds one and immediately hands it
       to `apy_type_of`, so what a program holds when it writes `c =
       ValueError` is a plain `APY_TYPE_K` with an empty dict -- and the test
       above, which reads exactly right, never fired once.

       That is why this is a SECOND check and not a widening of the first: the
       interpreter's `objects_host` keeps the exception cell and matched on
       it, so the same source ran correctly there and failed here. Two paths
       agreeing on the language and disagreeing on which object a name holds
       is the failure this whole runtime is arranged to make visible, and it
       stayed invisible because nothing constructed an exception through a
       variable on the compiled path until `warnings.warn` did.

       GUARDED ON THE CLASS BEING EMPTY. A type with `__init__` or `__new__`
       is a class the program wrote, and `apy_exc_class_named` finds the ones
       it wrote by subclassing an exception; either way it means its own and
       `apy_instantiate` is right for it. */
    if (O(f)->kind == APY_TYPE_K && apy_type_is_exc(f))
        return apy_make_excn(O(f)->v.t.name, (apy_value)argv, argc);
    if (O(f)->kind == APY_TYPE_K)
        return apy_instantiate(f, argv, argc, kwrest, bound);
    if (O(f)->kind == APY_FUNC_K
            && O(f)->v.fn.native == APY_NAT_TYPE_CALL) {
        /* `type.__call__(cls, ...)` -- THE ORDINARY INSTANTIATION, with the
           metaclass hook deliberately skipped. This is what a metaclass's
           `__call__` ends with, and consulting the hook again from here
           would be that same `__call__` calling itself forever. */
        apy_value cls = O(f)->v.fn.bound;
        if (cls) return apy_instantiate(cls, argv, argc, kwrest, 0);
        if (argc < 1)
            return apy_fail("TypeError",
                            "type.__call__() takes at least 1 argument");
        return apy_instantiate(argv[0], argv + 1, argc - 1, kwrest, 0);
    }
    if (O(f)->kind == APY_INST_K) {
        /* A callable instance: `x(...)` is `type(x).__call__(x, ...)`. */
        apy_value m = apy_class_find(O(f)->v.o.cls, apy_name("__call__"));
        if (!m)
            return apy_fail2("TypeError", "'%s' object is not callable%s",
                             apy_kind_name(f), "");
        return apy_call_nk(apy_bind(m, f), argv, argc, kwrest, bound);
    }
    if (O(f)->kind != APY_FUNC_K)
        return apy_fail2("TypeError", "'%s' object is not callable%s",
                         apy_kind_name(f), "");

    if (O(f)->v.fn.bound) slots[n++] = O(f)->v.fn.bound;
    {   /* `*rest` collects everything past the declared parameters, and it
           occupies one argument slot of its own after them. Packed here
           rather than by the caller, which does not know the callee has
           one. */
        int64_t declared = O(f)->v.fn.arity - (O(f)->v.fn.vararg ? 1 : 0)
                                             - (O(f)->v.fn.kwarg ? 1 : 0);
        /* WHERE POSITIONS STOP. A keyword-only parameter is declared but not
           reachable by position, so a surplus argument belongs to `*rest` --
           or is an error -- rather than landing in it. */
        /* `bound` means the caller ALREADY matched names to slots, so every
           argument here belongs where it is. Re-applying the keyword-only
           limit would truncate a list `apy_call_kw` had just completed and
           then refill the tail from defaults, silently discarding the values
           the keywords supplied. */
        int64_t byslot = bound ? declared : declared - O(f)->v.fn.kwonly;
        int64_t take = argc;
        if (n + argc > byslot) take = byslot - n;
        if (take < 0) take = 0;
        for (i = 0; i < take && n < 17; i++) slots[n++] = argv[i];
        /* A missing trailing argument comes from the default the `def`
           evaluated, which lives in the function object -- see the comment on
           `fn` in `struct apy_obj`. */
        while (n < declared) {
            int64_t d = n - (declared - O(f)->v.fn.ndefaults);
            if (d < 0 || d >= O(f)->v.fn.ndefaults) break;
            slots[n++] = O(f)->v.fn.defaults[d];
        }
        if (O(f)->v.fn.vararg) {
            apy_value rest = apy_tuple_new(argc - take + 1);
            for (i = take; i < argc; i++) apy_seq_push(rest, argv[i]);
            if (n < 17) slots[n++] = rest;
        }
        /* `**kw` is the LAST parameter and is passed even when empty: `def
           f(**kw)` called as `f()` binds `{}`, not nothing. */
        if (O(f)->v.fn.kwarg && n < 17)
            slots[n++] = kwrest ? kwrest : apy_dict_new(1);
    }
    if (n != O(f)->v.fn.arity) return apy_arity_error(f, argc);
    return apy_invoke(f, slots, n);
}


static apy_value apy_call_n(apy_value f, apy_value *argv,
                            int64_t argc) {
    return apy_call_nk(f, argv, argc, 0, 0);
}

/* The frontend's entry point: `argv` is the ADDRESS of an array of values in
   a stack slot, the same shape `apy_print` takes and for the same reason --
   the IR has no varargs. */
APY_API apy_value apy_call(apy_value f, apy_value argv, int64_t argc) {
    return apy_call_n(f, (apy_value *)argv, argc);
}

/* The FUNC_K object a call will actually ENTER, and how many of its declared
   parameters the caller does not supply -- 1 for the `self` of a method or of
   a class's `__init__`, 0 otherwise. Zero when there is nothing to enter.

   Keyword resolution needs this and a plain call does not: `C(n=1)` names a
   parameter of `C.__init__`, so the names have to be read off that function
   and matched against positions shifted past `self`. */
static apy_value apy_call_target(apy_value f, int64_t *skip) {
    *skip = 0;
    if (O(f)->kind == APY_TYPE_K) {
        /* `__new__` DECLARES THE KEYWORDS when a class writes one and leaves
           `__init__` to the default -- which is exactly a metaclass taking
           class keywords: `M.__new__(mcls, name, bases, ns, kind=None)` with
           `type.__init__` behind it. Reading the names off `__init__` there
           matched them against a native that declares none. */
        apy_value init = apy_class_find(f, apy_name("__init__"));
        apy_value maker = apy_class_find(f, apy_name("__new__"));
        if ((!init || O(init)->v.fn.native) && maker
                && O(maker)->kind == APY_FUNC_K && !O(maker)->v.fn.native) {
            *skip = 1;
            return maker;
        }
        if (!init || O(init)->kind != APY_FUNC_K || O(init)->v.fn.native)
            return 0;
        *skip = 1;
        return init;
    }
    if (O(f)->kind == APY_INST_K) {
        apy_value m = apy_class_find(O(f)->v.o.cls, apy_name("__call__"));
        if (!m || O(m)->kind != APY_FUNC_K) return 0;
        *skip = 1;
        return m;
    }
    if (O(f)->kind != APY_FUNC_K) return 0;
    if (O(f)->v.fn.bound) *skip = 1;
    return f;
}

/* `f(a, b, k=v, **d)`. The positional arguments are in `buf`; the keyword
   ones are a DICT, built at the call site, because `**d` merges a dict whose
   keys are not known until it exists -- a compile-time list of names could
   not express that and a second entry point for it would duplicate all of the
   resolution below.

   The keywords are placed into their PARAMETER POSITIONS here and a complete
   argument list is handed to `apy_call_n`, so everything downstream -- the
   arity check, `*rest`, a bound receiver -- stays the one implementation it
   already was. Defaults are filled here too, because a keyword can leave a
   HOLE in the middle (`f(1, c=3)` against `def f(a, b=2, c=3)`) and
   `apy_call_n` only knows how to fill a missing TAIL. */
APY_API apy_value apy_call_kw(apy_value f, apy_value buf, int64_t argc,
                              apy_value kwd) {
    apy_value *raw = (apy_value *)buf;
    apy_value slots[17], rest = 0;
    char filled[17];
    int64_t skip = 0, declared, want, bypos, i, k, kwn;
    apy_value target = apy_call_target(f, &skip);

    if (!target)
        /* No signature to match against: a class with no `__init__`, or a
           value that is not callable at all. `apy_call_nk` words both of
           those, so let it.
           THROUGH `apy_call_nk` AND NOT `apy_call_n`, so the keywords SURVIVE.
           `class C(dict): pass` then `C(a=1)` has no `__init__` to match
           against and used to arrive here, drop `kwd` on the floor, and hand
           `apy_instantiate` nothing at all -- the instance came back with an
           empty dict and no error, which is a wrong answer where a refusal
           was intended. */
        return apy_call_nk(f, raw, argc, kwd, 0);
    kwn = apy_raw_len(kwd);
    declared = O(target)->v.fn.arity - (O(target)->v.fn.vararg ? 1 : 0)
                                     - (O(target)->v.fn.kwarg ? 1 : 0);
    want = declared - skip;
    if (want < 0) want = 0;
    if (want > 17) want = 17;
    /* Positions reach only as far as the keyword-only tail; names reach all
       of it, which is why `want` stays whole and only `bypos` shrinks. */
    bypos = want - O(target)->v.fn.kwonly;
    if (bypos < 0) bypos = 0;
    /* Extra positionals with a `*rest` never leave a hole, so they can go
       straight through -- and a keyword alongside them would name a parameter
       already filled, which the loop below reports. */
    for (i = 0; i < want; i++) filled[i] = 0;
    for (i = 0; i < argc && i < bypos; i++) { slots[i] = raw[i]; filled[i] = 1; }
    if (O(target)->v.fn.kwarg) rest = apy_dict_new(kwn + 1);

    for (k = 0; k < kwn; k++) {
        apy_value nm = O(kwd)->v.d.keys[k], val = O(kwd)->v.d.vals[k];
        int64_t at = -1;
        int posonly_hit = 0;
        if (O(target)->v.fn.pnames)
            for (i = 0; i < want; i++) {
                apy_value p = O(target)->v.fn.pnames[i + skip];
                if (!p || strcmp(APY_CSTR(p), APY_CSTR(nm)) != 0) continue;
                /* POSITIONAL-ONLY: the name is recorded so this message can
                   be specific, but it does not match. */
                if (i + skip < O(target)->v.fn.posonly) { posonly_hit = 1; break; }
                at = i;
                break;
            }
        /* NOT CONDITIONAL ON THERE BEING NO SURPLUS. Surplus positionals
           belong to `*rest`; they do not stop a keyword from naming a
           parameter, and least of all a keyword-only one, which no position
           could have filled. Requiring `argc <= bypos` here sent `c=3` in
           `d(1, 2, c=3, z=4)` into `**kw` and left `c` on its default. */
        if (at >= 0 && !filled[at]) {
            slots[at] = val;
            filled[at] = 1;
            continue;
        }
        if (at >= 0) {
            char b[160];
            snprintf(b, sizeof b, "%s() got multiple values for argument '%s'",
                     APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            return apy_fail("TypeError", b);
        }
        /* Not a declared parameter. `**kw` collects it; without one it is the
           error CPython reports, naming the keyword rather than a count. */
        if (rest) { apy_dict_set(rest, nm, val); continue; }
        {
            char b[192];
            /* NAMED SPECIFICALLY when the parameter exists but is
               positional-only. "unexpected keyword" would send the reader
               looking for a typo in a name that is right there in the
               signature; the mistake is the spelling of the CALL, not of the
               name. */
            if (posonly_hit)
                snprintf(b, sizeof b,
                         "%s() got some positional-only arguments passed as "
                         "keyword arguments: '%s'",
                         APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            else
                snprintf(b, sizeof b,
                         "%s() got an unexpected keyword argument '%s'",
                         APY_CSTR(O(target)->v.fn.name), APY_CSTR(nm));
            return apy_fail("TypeError", b);
        }
    }

    for (i = 0; i < want; i++) {
        int64_t d;
        if (filled[i]) continue;
        d = (i + skip) - (declared - O(target)->v.fn.ndefaults);
        if (d < 0 || d >= O(target)->v.fn.ndefaults) {
            char b[160];
            const char *pn = (O(target)->v.fn.pnames
                              && O(target)->v.fn.pnames[i + skip])
                ? APY_CSTR(O(target)->v.fn.pnames[i + skip]) : "?";
            snprintf(b, sizeof b,
                     "%s() missing 1 required positional argument: '%s'",
                     APY_CSTR(O(target)->v.fn.name), pn);
            return apy_fail("TypeError", b);
        }
        slots[i] = O(target)->v.fn.defaults[d];
    }
    if (argc > bypos) {
        if (O(target)->v.fn.vararg) {
            /* AFTER the declared parameters, not over the keyword-only ones
               at the end of them -- those were just filled by name, and
               writing the surplus into their slots discarded what the
               keywords supplied. `apy_call_nk` with `bound` set takes the
               first `declared` as parameters and everything past them as
               `*rest`, which is exactly this layout. */
            int64_t j;
            for (j = 0; bypos + j < argc && want + j < 17; j++)
                slots[want + j] = raw[bypos + j];
            want += j;
        } else {
            /* No `*rest` to swallow them, so this is an arity error -- left
               to `apy_call_nk`, which words it. */
            for (i = bypos; i < argc && i < 9; i++) slots[i] = raw[i];
            want = argc < 9 ? argc : 9;
        }
    }
    return apy_call_nk(f, slots, want, rest, 1);
}

/* `f(*xs)`, where the argument COUNT is a value rather than a constant.

   An ordinary call knows its arity at compile time and passes a stack array.
   A starred one cannot: `xs` decides how many arguments there are, and the
   frontend has no number to emit. So the arguments arrive as a list and are
   copied into an argv here, which is the one place that count exists.

   `apy_call_n` then binds them against the callee's own signature -- defaults,
   `*rest`, arity mismatch and all -- so a spread call reports a wrong count
   exactly as a direct one does, at run time rather than at compile time. */
/* `f(*xs, **kw)`. The keyword half travels SEPARATELY, for the reason it does
   everywhere else here: only the binder knows where the positional arguments
   stop, so a dict appended to the list would arrive as one more positional.
   Dropping it made `f(*xs, **kw)` ignore every keyword in silence. */
APY_API apy_value apy_call_spread_kw(apy_value f, apy_value args,
                                     apy_value kwd) {
    int64_t n = O(args)->v.q.n;
    apy_value *argv = (apy_value *)malloc(sizeof(apy_value)
                                          * (size_t)(n ? n : 1));
    apy_value r;
    if (!argv) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    memcpy(argv, O(args)->v.q.items, sizeof(apy_value) * (size_t)n);
    r = (kwd && O(kwd)->kind == APY_DICT_K && O(kwd)->v.d.n)
        ? apy_call_kw(f, (apy_value)argv, n, kwd)
        : apy_call_n(f, argv, n);
    free(argv);
    return r;
}

APY_API apy_value apy_call_spread(apy_value f, apy_value args) {
    int64_t n = O(args)->v.q.n;
    apy_value *argv = (apy_value *)malloc(sizeof(apy_value) * (size_t)(n ? n : 1));
    apy_value r;
    if (!argv) { fputs("asmpython: out of memory\n", stderr); exit(1); }
    memcpy(argv, O(args)->v.q.items, sizeof(apy_value) * (size_t)n);
    r = apy_call_n(f, argv, n);
    free(argv);
    return r;
}

/* `xs.extend(other)` in all but name: every element of a sequence, appended.
   Used to flatten a starred argument into the list a spread call builds. */
APY_API apy_value apy_extend(apy_value seq, apy_value other) {
    int64_t i;
    /* Drain anything that is iterable but not indexable -- a generator, a
       user object with `__iter__` -- so `[*gen]` and `f(*gen)` work. Both
       walks below are by index and neither can step a cursor. */
    other = apy_iterable(other);
    if (!other) return 0;
    if (O(other)->kind == APY_STR_K || O(other)->kind == APY_BYTES_K
        || O(other)->kind == APY_DICT_K || O(other)->kind == APY_RANGE_K) {
        int64_t n = apy_raw_len(other);
        for (i = 0; i < n; i++) {
            apy_value item = O(other)->kind == APY_DICT_K
                ? O(other)->v.d.keys[i]
                : apy_getitem(other, apy_from_int(i));
            if (!item) return 0;
            apy_seq_push(seq, item);
        }
        return apy_none();
    }
    if (!apy_is_seq(other) && !apy_is_set(other))
        return apy_fail2("TypeError", "'%s' object is not iterable%s",
                         apy_kind_name(other), "");
    for (i = 0; i < O(other)->v.q.n; i++)
        apy_seq_push(seq, O(other)->v.q.items[i]);
    return apy_none();
}

/* Is this a user object? The frontend branches on it before a built-in method
   whose NAME some class in the same program also defines -- `x.add(1)` is a
   set's `add` or the program's, and only the receiver knows which.

   A predicate rather than a check inside each method entry point, because the
   frontend already knows which names collide: the branch is emitted only
   where one actually might, so `xs.append(v)` in a program with no `append`
   method stays a single call and pays nothing. */
APY_API int64_t apy_is_instance(apy_value v) {
    return O(v)->kind == APY_INST_K;
}

/* WHICH SIDE OF A METHOD CALL A RECEIVER BELONGS ON, and what it should be
   when it lands there. `_dyn_method_either` emits both a user lookup and a
   direct builtin call and picks between them at run time; these two answer
   the picking.

   THE QUESTION IS ABOUT THE CLASS, NOT THE KIND. It used to be `is this an
   instance`, which is right for the collision it was written for -- a class
   defining `add` next to `set.add` -- and wrong for `class D(dict)`, which
   INHERITS `keys` without writing it. Such an instance is not a set and not a
   dict either, so both branches refused it: the user lookup found no `keys`
   on the class, and the builtin call was never reached.

   SO THE INSTANCE IS UNWRAPPED INSTEAD. A method the class did not define is
   the builtin's, and the builtin wants the value the instance CARRIES. That
   is one test and one substitution rather than teaching each of the hundred
   or so builtin methods what an instance is. */
/* An instance ACTING AS the builtin it carries, for one operation. The
   class body wins: a `Counter` writing `__eq__` means its own, so the dunder
   is asked for by name and a class that defines it is left alone. Identity
   for everything that is not a builtin-extending instance, which is what lets
   this be dropped in front of an existing test rather than beside it. */
static apy_value apy_as_builtin(apy_value v, const char *dunder) {
    if (O(v)->kind == APY_INST_K && O(v)->v.o.held
            && !apy_class_find(O(v)->v.o.cls, apy_name(dunder)))
        return O(v)->v.o.held;
    return v;
}

APY_API int64_t apy_method_is_builtin(apy_value obj, apy_value name) {
    if (O(obj)->kind != APY_INST_K)
        return 1;
    /* THE CLASS BODY WINS. A `Counter` defining `update` means its own, even
       though `dict` has one -- which is the whole reason this asks the class
       before it asks the kind. */
    if (apy_class_find(O(obj)->v.o.cls, name))
        return 0;
    /* AN ORDINARY INSTANCE STAYS ON THE USER SIDE even with nothing found,
       so a `__getattr__` still gets its chance -- the lookup there reports
       the AttributeError, and reporting it from a builtin that was handed the
       wrong kind would name the kind instead of the attribute. */
    return O(obj)->v.o.held != 0;
}

APY_API apy_value apy_method_self(apy_value obj, apy_value name) {
    if (O(obj)->kind == APY_INST_K && O(obj)->v.o.held
            && !apy_class_find(O(obj)->v.o.cls, name))
        return O(obj)->v.o.held;
    return obj;
}

/* --- type objects ------------------------------------------------------- */
/* `type(x)` has to be a VALUE now, not the string `apy_type_name` returns:
   `isinstance(p, Point)` names a class, and comparing its name to a string
   would make two different classes with the same name interchangeable.

   Built-in types are INTERNED by name, so `type(1) is type(2)` is True the
   way it is in CPython. Interning by name rather than by kind is what keeps
   each exception type a single object -- every `APY_EXC_K` cell shares one
   kind but names one of thirty types. */
/* REACHED THROUGH TWO FUNCTIONS so the table can move, the shape the name
   cache and the source positions use. Pairs rather than two arrays, because
   the IR side reserves one block. */
static apy_value apy_type_rows_c[64][2];
static int64_t apy_type_count_c;
APY_API apy_value apy_type_rows(void) { return (apy_value)apy_type_rows_c; }
APY_API apy_value apy_type_slot_count(void) {
    return (apy_value)&apy_type_count_c;
}
/* THROUGH THE ACCESSORS, for the reason `apy_canonical_types` gives. */
#define apy_type_rows_at(i) (((apy_value (*)[2])apy_type_rows())[i])
#define apy_type_names(i) (apy_type_rows_at(i)[1])
#define apy_type_keys(i)  ((const char *)apy_type_rows_at(i)[0])
#define apy_type_count    (*(int64_t *)apy_type_slot_count())

/* THE EXPORTED HALF, which `runtime/makers.py` replaces. The static below
   keeps the name its callers use; `_of` was already taken by this
   function's own spelling, so the export is `_for`. */
APY_API apy_value apy_type_for(apy_value v) {
    const char *key;
    int i;
    if (O(v)->kind == APY_INST_K) return O(v)->v.o.cls;
    /* AN EXCEPTION OF A CLASS THE PROGRAM WROTE answers that class, so
       `type(e).__name__` and `type(e) is AppError` say what the source does.
       Without a class it falls through to the name-keyed table below, which
       is what every exception the runtime raises itself has. */
    if (O(v)->kind == APY_EXC_K && O(v)->v.e.cls) return O(v)->v.e.cls;
    /* `type(C)` IS THE METACLASS when one made it. An ordinary class has no
       metaclass recorded and reads as `type`, which is what it is. */
    if (O(v)->kind == APY_TYPE_K && O(v)->v.t.meta) return O(v)->v.t.meta;
    if (O(v)->kind == APY_TYPE_K) return apy_type_class();
    key = apy_kind_name(v);
    /* THE SAME OBJECT THE NAME ANSWERS, when the program names that builtin
       type anywhere -- so `type(1) is int` holds. The frontend registers each
       one at the top of the entry, before any statement, which is what takes
       the evaluation order out of it: registering lazily made the answer
       depend on whether `type(1)` or `int` was reached first. */
    if (apy_canonical_types) {
        apy_value found = apy_dict_get_or(apy_canonical_types,
                                          apy_lit(key), 0);
        if (found) return found;
    }
    for (i = 0; i < apy_type_count; i++)
        if (strcmp(apy_type_keys(i), key) == 0) return apy_type_names(i);
    if (apy_type_count >= 64) return apy_type_new(apy_lit(key), 0);
    apy_type_rows_at(apy_type_count)[0] = (apy_value)(uintptr_t)key;
    apy_type_rows_at(apy_type_count)[1] = apy_type_new(apy_lit(key), 0);
    return apy_type_rows_at(apy_type_count++)[1];
}
static apy_value apy_type_of(apy_value v) {
    return apy_type_for(v);
}

APY_API apy_value apy_type_object(apy_value v) { return apy_type_of(v); }

/* `with` -- the two halves of the context-manager protocol.

   Separate entry points rather than one `apy_method1` at each call site,
   because the error text is specific: a value with neither method is
   reported as not being a context manager, naming the one it lacks, which is
   what CPython says and what tells the reader which half to write. */
/* `__aenter__` / `__aexit__`. Each ANSWERS A COROUTINE rather than a value:
   `async with` awaits what these return, which is the whole difference from
   the synchronous pair and the reason they cannot share an entry point. */
APY_API apy_value apy_aenter(apy_value cm) {
    apy_value m = apy_dunder(cm, "__aenter__");
    if (!m)
        return apy_fail2("TypeError",
                         "'%s' object does not support the asynchronous "
                         "context manager protocol%s", apy_kind_name(cm), "");
    return apy_call_n(m, NULL, 0);
}

APY_API apy_value apy_aexit(apy_value cm, apy_value exc) {
    apy_value m = apy_dunder(cm, "__aexit__"), argv[3];
    if (!m)
        return apy_fail2("TypeError",
                         "'%s' object does not support the asynchronous "
                         "context manager protocol%s", apy_kind_name(cm), "");
    /* All three from the one value, as `apy_exit` does it: the TYPE is what
       `et.__name__` reads, the VALUE is the exception, the traceback is None
       because there are none here. */
    argv[0] = O(exc)->kind == APY_EXC_K ? apy_exc_type(exc) : apy_none();
    argv[1] = exc;
    argv[2] = apy_none();
    return apy_call_n(m, argv, 3);
}

APY_API apy_value apy_enter(apy_value cm) {
    apy_value m;
    /* `__exit__` FIRST, which is CPython's order and shows in the message:
       `with 5:` reports the missing `__exit__` rather than the missing
       `__enter__`, even though both are absent. */
    if (!apy_dunder(cm, "__exit__")) {
        apy_error_clear();
        return apy_fail2("TypeError",
                         "'%s' object does not support the context manager "
                         "protocol (missed %s method)",
                         apy_kind_name(cm), "__exit__");
    }
    m = apy_dunder(cm, "__enter__");
    if (!m) {
        apy_error_clear();
        return apy_fail2("TypeError",
                         "'%s' object does not support the context manager "
                         "protocol (missed %s method)",
                         apy_kind_name(cm), "__enter__");
    }
    return apy_call_n(m, NULL, 0);
}

/* `__exit__(type, value, traceback)`. `exc` is the live exception or None.

   All three arguments come from the one value: the TYPE is what
   `et.__name__` reads, the VALUE is the exception itself, and the traceback
   is None because there are none here. Passing None for the type when there
   is an exception would make `et.__name__` fail in a manager that logs it. */
APY_API apy_value apy_exit(apy_value cm, apy_value exc) {
    apy_value argv[3];
    apy_value m = apy_dunder(cm, "__exit__");
    if (!m)
        return apy_fail2("TypeError",
                         "'%s' object does not support the context manager "
                         "protocol%s", apy_kind_name(cm), "");
    if (O(exc)->kind == APY_EXC_K) {
        argv[0] = apy_type_of(exc);
        argv[1] = exc;
    } else {
        argv[0] = apy_none();
        argv[1] = apy_none();
    }
    argv[2] = apy_none();
    return apy_call_n(m, argv, 3);
}


/* Is `cls` anywhere in `of`'s order? The `isinstance` rule for user classes.

   THROUGH THE MRO WHEN THERE IS ONE, for the same reason attribute lookup is:
   `isinstance(D(), C)` for `class D(B, C)` is True and the base chain from D
   reaches only B and A. */
APY_API int64_t apy_type_is_sub_of(apy_value of, apy_value cls) {
    if (of && O(of)->kind == APY_TYPE_K && O(of)->v.t.mro) {
        apy_value order = O(of)->v.t.mro;
        int64_t i;
        for (i = 0; i < O(order)->v.q.n; i++)
            if (O(order)->v.q.items[i] == cls) return 1;
        return 0;
    }
    while (of && O(of)->kind == APY_TYPE_K) {
        if (of == cls) return 1;
        of = O(of)->v.t.base;
    }
    return 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_type_is_sub(apy_value of, apy_value cls) {
    return (int)apy_type_is_sub_of(of, cls);
}

/* --- operator dispatch to user methods ---------------------------------- */
/* `apy_str`, `apy_eq`, `apy_add` and the rest were written against a closed
   set of kinds. An instance is not one of them, and the whole point of
   `class` is that the answer comes from the program rather than from here.

   THE PROTOCOL FOR RETURNING. These helpers answer 0 both for "the class
   defines no such method" and for "it does, and it failed". The two are told
   apart by the ERROR FLAG, which is exactly how every other fallible
   operation in this file already reports, so a caller reads

       r = apy_binary_dunder(...);
       if (r || apy_error_occurred()) return r;

   and otherwise falls through to the TypeError it would have raised anyway.
   A separate out-parameter would be one more thing for a call site to get
   wrong, and there are twenty call sites. */

APY_API apy_value apy_dunder_of(apy_value v, apy_value name) {
    apy_value m;
    if (O(v)->kind != APY_INST_K) return 0;
    m = apy_class_find(O(v)->v.o.cls, apy_name((const char *)name));
    return (m && O(m)->kind == APY_FUNC_K) ? apy_bind(m, v) : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_dunder(apy_value v, const char *name) {
    return apy_dunder_of(v, (apy_value)(uintptr_t)name);
}


APY_API apy_value apy_unary_dunder_of(apy_value v, apy_value name) {
    apy_value m = apy_dunder_of(v, name);
    return m ? apy_call_n(m, NULL, 0) : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_unary_dunder(apy_value v, const char *name) {
    return apy_unary_dunder_of(v, (apy_value)(uintptr_t)name);
}

APY_API apy_value apy_method1_of(apy_value v, apy_value name,
                                 apy_value arg) {
    apy_value m = apy_dunder_of(v, name);
    return m ? apy_call_n(m, &arg, 1) : 0;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_method1(apy_value v, const char *name, apy_value arg) {
    return apy_method1_of(v, (apy_value)(uintptr_t)name, arg);
}

/* `a + b` asks `a.__add__(b)` first and `b.__radd__(a)` second. The reflected
   form is why `1 + v` can reach a user class at all: the int on the left has
   no idea what `v` is, so the right operand gets the second word. */
APY_API apy_value apy_binary_dunder_of(apy_value a, apy_value b,
                                       apy_value name, apy_value rname) {
    apy_value r = apy_method1_of(a, name, b);
    if (apy_error_occurred()) return r;
    /* `NotImplemented` MEANS "ASK THE OTHER OPERAND", not "the answer is
       NotImplemented". Returning it as the result made `Left() == Right()`
       answer the sentinel instead of falling back to Right's `__eq__`, and a
       program printing it saw a word where its answer should have been. */
    if (r && O(r)->kind != APY_NOTIMPL_K) return r;
    {
        apy_value other = apy_method1_of(b, rname, a);
        if (apy_error_occurred()) return other;
        if (other && O(other)->kind != APY_NOTIMPL_K) return other;
        /* NEITHER SIDE ANSWERED. Nothing is returned and no error is set,
           which is how every caller here spells "fall back to the default" --
           identity for `==`, a TypeError for arithmetic. */
        return 0;
    }
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static apy_value apy_binary_dunder(apy_value a, apy_value b,
                                   const char *name,
                                   const char *rname) {
    return apy_binary_dunder_of(a, b, (apy_value)(uintptr_t)name,
                                (apy_value)(uintptr_t)rname);
}

/* True when either operand is an instance, which is the guard every operator
   below uses before paying for a lookup. */
APY_API int64_t apy_either_inst_of(apy_value a, apy_value b) {
    return O(a)->kind == APY_INST_K || O(b)->kind == APY_INST_K;
}
/* THE NAME ITS CALLERS USE, kept as a delegate: the body is IR's now,
   and the exported half above stands in when nothing is ported. */
static int apy_either_inst(apy_value a, apy_value b) {
    return (int)apy_either_inst_of(a, b);
}

"""
