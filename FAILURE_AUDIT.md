# Failing-case audit, by root cause

All 285 failing cases at commit 5a9355ad, **ordered by root cause, most cases to
fewest**. A cause is listed only where it was established, not inferred from the
symptom.

Each `verified` cause was reproduced at head with a minimal probe. `strong` means
the symptom is diagnostic but the case was not individually reduced. Entries
marked **NOT root-caused** are exactly that -- they are grouped by where they fail,
because attributing them would be guessing.

274 of 285 have an `# expect:` block matching CPython 3.14 exactly.


| rank | root cause | confidence | cases |
|---|---|---|---|
| 1 | NOT root-caused - core language, fails at run time | NOT root-caused | 64 |
| 2 | NOT root-caused - stdlib case, fails at run time | NOT root-caused | 39 |
| 3 | boxing: known type recorded as any -> raw value read as a pointer | strong | 39 |
| 4 | bytes / bytearray type absent | verified | 30 |
| 5 | other compile-time refusal | verified | 15 |
| 6 | corpus defect - the test itself is wrong | verified | 11 |
| 7 | stdlib bindings: wrong signature (arity/kwargs) | verified | 11 |
| 8 | operator / indexing / iteration protocol gap | verified | 9 |
| 9 | sema inferred the wrong type (compile refusal) | verified | 9 |
| 10 | f-string: nested format spec | verified | 8 |
| 11 | boxing: value resolves to 0 / None instead of its contents | strong | 7 |
| 12 | closures / callable values not modelled (compile refusal) | verified | 7 |
| 13 | round(x, n) returns float instead of int | verified | 5 |
| 14 | stdlib bindings: function missing | verified | 5 |
| 15 | stdlib module has no bindings at all | verified | 5 |
| 16 | finally does not run on return | verified | 4 |
| 17 | arbitrary-precision int absent | verified | 3 |
| 18 | dynamic class creation: 3-argument type() | verified | 3 |
| 19 | parser: syntax not supported | verified | 3 |
| 20 | f-string: percent format spec | verified | 2 |
| 21 | sort/sorted ignores __lt__ on instances | verified | 2 |
| 22 | __getattr__ / dynamic attribute unsupported | verified | 1 |
| 23 | closure captures the wrong value | verified | 1 |
| 24 | exception __str__ ignored | verified | 1 |
| 25 | mutable default argument rejected | verified | 1 |
| | **total** | | **285** |

## 1. NOT root-caused - core language, fails at run time  (64)  — *NOT root-caused*

Localized to the statement that first diverged:

- *other expression* — 47: `algo_merge_sort.py`, `app_expression_eval.py`, `boolean_expression_complex.py`, `bound_method_frozenset_contains.py`, `complex_via_real.py`, `crash_conditional_type_change.py`, `crash_float_default_param.py`, `crash_nested_function_float.py`…
- *number formatting* — 6: `crash_float_modulo_negative.py`, `crash_recursive_float.py`, `float_scientific_upper.py`, `format_spec_percent.py`, `fstring_exp.py`, `r40_series_sum.py`
- *method call on a value* — 4: `470_static_class_registry.py`, `476_data_descriptor_precedence.py`, `str_splitlines_keepends.py`, `unicode_upper_accent.py`
- *string formatting* — 3: `crash_float_format_edge.py`, `exc_break_in_try.py`, `format_general_g.py`
- *sort / ordering* — 3: `multiple_context_vars.py`, `sorted_tuples_multi.py`, `sorted_with_none_handling.py`
- *indexing / subscript* — 1: `475_dynamic_dict_index_assign.py`

- `470_static_class_registry.py`
- `475_dynamic_dict_index_assign.py`
- `476_data_descriptor_precedence.py`
- `algo_merge_sort.py`
- `app_expression_eval.py`
- `boolean_expression_complex.py` — want `True` got `1`
- `bound_method_frozenset_contains.py`
- `complex_via_real.py`
- `crash_conditional_type_change.py` — want `1.5` got `3`
- `crash_float_default_param.py` — want `3.0 6.0` got `3.177198e-317 1.8221773e-317`
- `crash_float_format_edge.py` — want `1,234.57 1.234568e+03 1234.57` got `1,234.57 1.234568e+003 1234.57`
- `crash_float_modulo_negative.py` — want `0.5 -0.5` got `-1.5 1.5`
- `crash_nested_function_float.py` — want `6.0` got `1.951741e-317`
- `crash_recursive_float.py`
- `custom_exception_attr.py`
- `decorator_preserve_result.py`
- `decorator_with_args.py`
- `default_arg_evaluated_once.py` — want `[1]` got `[]`
- `default_none_or_list.py` — want `[1] [2]` got `[] []`
- `docstring_module_access.py`
- `dunder_radd.py`
- `exc_break_in_try.py` — want `f 2` got `done`
- `exc_custom_hierarchy.py` — want `caught as base SubErr` got `caught as base str`
- `except_hierarchy.py` — want `caught ValueError` got `caught str`
- `float_func_return.py` — want `212.0` got `1`
- `float_nan_compare.py` — want `False True` got `True False`
- `float_scientific_upper.py` — want `1.23E+04` got `1.23E+004`
- `format_align_equals.py` — want `-     42 +     42` got `-42      +42`
- `format_general_g.py` — want `1.234e-05 1.234e+06` got `1.234e-005 1.234e+006`
- `format_spec_percent.py` — want `12.3%` got `0.1234`
- `fstring_exp.py` — want `1.23e+04` got `1.23e+004`
- `generator_expr_direct.py` — want `0 1` got `0 0`
- `generator_pipeline.py`
- `init_subclass_hook.py` — want `['A', 'B']` got `[]`
- `iter_two_arg_sentinel.py`
- `list_slice_assign_resize.py` — want `[1, 9, 4]` got `[1, 9, 3, 4]`
- `list_slice_assignment_grow.py` — want `[1, 10, 20, 2, 3]` got `[1, 2, 3]`
- `metaclass_basic.py`
- `min_mixed_int_float.py` — want `1.5` got `1.5e-323`
- `multiple_context_vars.py` — want `[('a', 1), ('b', 2), ('c', 3)]` got `[]`
- `number_sign_function.py` — want `1 -1 0` got `True True False`
- `operator_overload_comparison.py` — want `True False` got `True True`
- `param_mixed_all_kinds.py`
- `path_join_manual.py`
- `prog_price_formatter.py` — want `['$9.99', '$19.50', '$100.00']` got `['$9.99', '$19.5', '$100']`
- `r39_char_histogram_sort.py` — want `i 4` got `m 1`
- `r40_series_sum.py` — want `2.0833` got `3.0`
- `repr_bool_in_list.py` — want `[True, False, True]` got `[1, 0, 1]`
- `repr_mixed_container.py` — want `{'list': [1, 2], 'tup': (3, 4)}` got `{'list': [1, 2], 'tup': [3, 4]}`
- `repr_none_in_list.py` — want `[1, None, 2, None]` got `[1, 0, 2, 0]`
- `repr_string_with_quotes.py` — want `["it's", 'a "test"']` got `['it's', 'a "test"']`
- `returning_bound_method.py`
- `slice_step_zero_error.py`
- `sorted_tuples_multi.py` — want `[(0, 9), (1, 1), (1, 2)]` got `[(0, 9), (1, 2), (1, 1)]`
- `sorted_with_none_handling.py` — want `[1, 2, 3]` got `[3, 1, 2]`
- `str_splitlines_keepends.py` — want `['a\n', 'b\n']` got `['a', 'b']`
- `str_unicode_len.py` — want `5` got `6`
- `temperature_convert.py` — want `212.0 32.0 98.6` got `1 1 1`
- `type_name_lookup.py` — want `list` got `<missing>`
- `unicode_emoji_len.py` — want `3` got `6`
- `unicode_ord_high.py` — want `20013` got `228`
- `unicode_upper_accent.py` — want `�` got `é`
- `with_suppress_exception.py`
- `zip_longest_manual.py`

## 2. NOT root-caused - stdlib case, fails at run time  (39)  — *NOT root-caused*

Localized to the statement that first diverged:

- *method call on a value* — 18: `53_dynamic_import.py`, `lib_calendar_isleap.py`, `lib_cmath_sqrt.py`, `lib_collections_counter_total.py`, `lib_difflib_close.py`, `lib_fnmatch.py`, `lib_heapq_nlargest.py`, `lib_itertools_chain_from_iter.py`…
- *other expression* — 12: `conditional_import_pattern.py`, `decimal_precision.py`, `lib_datetime_combine.py`, `lib_enum_auto.py`, `lib_enum_basic.py`, `lib_fractions_from_float.py`, `lib_itertools_cycle.py`, `lib_itertools_tee.py`…
- *indexing / subscript* — 4: `conditional_import_fallback.py`, `lib_array_basic.py`, `lib_array_typecodes.py`, `nested_dict_default.py`
- *comprehension* — 3: `lib_csv_reader.py`, `lib_glob_pattern_match.py`, `lib_itertools_groupby.py`
- *sort / ordering* — 2: `lib_collections_counter_update.py`, `lib_random_sample.py`

- `53_dynamic_import.py`
- `conditional_import_fallback.py`
- `conditional_import_pattern.py` — want `False` got `True`
- `decimal_precision.py`
- `lib_array_basic.py`
- `lib_array_typecodes.py`
- `lib_calendar_isleap.py` — want `True False` got `1 0`
- `lib_cmath_sqrt.py`
- `lib_collections_counter_total.py`
- `lib_collections_counter_update.py`
- `lib_csv_reader.py`
- `lib_datetime_combine.py`
- `lib_difflib_close.py`
- `lib_enum_auto.py`
- `lib_enum_basic.py`
- `lib_fnmatch.py` — want `True False` got `1 0`
- `lib_fractions_from_float.py` — want `1/2` got `1`
- `lib_glob_pattern_match.py` — want `['a.py', 'c.py']` got `[]`
- `lib_heapq_nlargest.py` — want `[8, 5, 3]` got `[5, 8, 2]`
- `lib_itertools_chain_from_iter.py`
- `lib_itertools_cycle.py` — want `[1, 2, 1, 2, 1]` got `[1, 2, 1, 2]`
- `lib_itertools_groupby.py`
- `lib_itertools_tee.py`
- `lib_json_parse_array.py`
- `lib_json_roundtrip.py` — want `[1, 2, 3]` got `['1', '2', '3']`
- `lib_numbers_check.py` — want `True True` got `False False`
- `lib_operator_contains.py`
- `lib_operator_methodcaller.py`
- `lib_operator_truth.py` — want `False True` got `0 1`
- `lib_pickle_roundtrip.py` — want `True` got `False`
- `lib_random_sample.py` — want `[0, 6, 9]` got `[6, 8, 9]`
- `lib_random_seeded.py` — want `82` got `76`
- `lib_random_shuffle.py` — want `[3, 4, 5, 1, 2]` got `[3, 1, 5, 4, 2]`
- `lib_re_groups.py` — want `user host` got `user@host user@host`
- `lib_re_named_groups.py`
- `lib_statistics_median.py` — want `3` got `3.0`
- `nested_dict_default.py`
- `ospath_splitext.py` — want `('file.tar', '.gz')` got `['file.tar', '.gz']`
- `type_alias_annotation.py`

## 3. boxing: known type recorded as any -> raw value read as a pointer  (39)  — *strong*

- `app_matrix_rotate.py` — want `[[3, 1], [4, 2]]` got `[8922416, 8922560]`
- `app_pagination.py` — want `4 [0, 1, 2] [9]` got `4 9512160 9512416`
- `app_validate_form.py` — want `[('age', 'too young'), ('email', 'requ` got `[('age', 5368746013)]`
- `bytes_from_str.py` — want `b'abc'` got `8397680`
- `conditional_function_selection.py` — want `5` got `8135568`
- `crash_bool_int_float_mix.py` — want `4.5` got `4612811918334230530`
- `crash_float_nested_container.py` — want `1.5` got `4609434218613702656`
- `data_sort_by_key.py` — want `['A', 'C']` got `[5368746001, 5368746007]`
- `deeply_nested_comprehension.py` — want `[[[0, 1], [1, 2]], [[1, 2], [2, 3]]]` got `[[4859088, 4859232], [4859488, 4859536`
- `dunder_format.py` — want `25C` got `9643152`
- `float_percentage_func.py` — want `25.0` got `5368736828`
- `format_binary_grouped.py` — want `1111_1111` got `11111111`
- `format_spec_grouping.py` — want `1,234,567 1111_1111` got `1,234,567 11111111`
- `int_negative_shift.py` — want `-4 255` got `9223372036854775804 255`
- `int_prog_parser.py` — want `app 1.0` got `8594944 8611216`
- `int_prog_tokenizer.py` — want `[12, '+', 34, '*', 5]` got `[12, 2040880, 34, 2041056, 5]`
- `lambda_default_arg.py` — want `15 25` got `5368758997 25`
- `lib_copy_deepcopy.py` — want `[[1, 2], [3, 4]] [[9, 2], [3, 4]]` got `[[9840096, 2], [3, 4]] [9839840, 98399`
- `lib_functools_reduce_initial.py` — want `100` got `8152448`
- `lib_itertools_accumulate_func.py` — want `[1, 2, 6, 24]` got `[5368713968, 5368713969, 5368713971, 5`
- `lib_itertools_combinations.py` — want `[(1, 2), (1, 3), (2, 3)]` got `[8987760, 8987856, 8988000]`
- `lib_itertools_compress.py` — want `['a', 'c']` got `[9315264, 9315328]`
- `lib_itertools_pairwise.py` — want `[(1, 2), (2, 3), (3, 4)]` got `[9315360, 9315440, 9315568]`
- `lib_itertools_permutations.py` — want `[(1, 2), (1, 3), (2, 1), (2, 3), (3, 1` got `[8398032, 8398128, 8398272, 8398368, 8`
- `lib_itertools_repeat.py` — want `['x', 'x', 'x']` got `[5368741930, 5368741930, 5368741930]`
- `lib_itertools_zip_longest.py` — want `[(1, 'a'), (2, '?'), (3, '?')]` got `[[1, 5368741963], [2, 8397840], [3, 83`
- `lib_mimetypes.py` — want `text/html` got `5368754374`
- `match_class_pattern.py` — want `o 3,4` got `5368741902 8284144`
- `match_guard.py` — want `neg zero pos` got `5368737792 5368737796 5368737801`
- `match_literal.py` — want `one two other` got `5368737792 5368737796 5368737800`
- `match_or_pattern.py` — want `small big` got `5368737792 5368737798`
- `match_sequence.py` — want `origin xaxis point` got `5368741912 5368741919 5368741925`
- `mixed_int_float_list_sum.py` — want `6.5` got `4612811918334230532`
- `partial_application_manual.py` — want `12` got `7611344`
- `r40_mean_variance.py` — want `5.0 5.0` got `4617315517961601024 461731551796160102`
- `reduce_with_named_function.py` — want `15` got `9266848`
- `sorted_multiple_criteria.py` — want `['alice', 'carol', 'bob']` got `[5368746018, 5368746024, 5368746028]`
- `str_encode_errors.py` — want `b'abc'` got `8790896`
- `zip_and_dict.py` — want `alice 30` got `8135696 8135744`

## 4. bytes / bytearray type absent  (30)  — *verified*

- `app_dependency_resolve.py` — want `['d', 'b', 'c', 'a']` got `[5368741906, 5368741902, 5368741904, 5`
- `app_simple_orm.py` — want `2 a` got `2 9185008`
- `bytearray_mutate.py` — want `bytearray(b'xbc')` got `[120, 98, 99]`
- `bytes_decode.py`
- `class_class_var_shared.py` — want `['a', 'b']` got `[5368741907, 5368741909]`
- `crash_float_comparison_sort_key.py` — want `b` got `a`
- `exc_args_tuple.py`
- `function_with_side_effect_list.py` — want `['a', 'b']` got `[5368737792, 5368737794]`
- `int_from_bytes.py` — want `1024` got `0`
- `int_prog_csv_aggregate.py` — want `[('a', 15), ('b', 20)]` got `[('a', 10737491980), ('b', 5368745991)`
- `int_prog_priority_queue.py` — want `a b` got `5368762410 5368762412`
- `int_prog_todo.py` — want `['b']` got `[5368746038]`
- `int_to_bytes.py` — want `b'\x04\x00'` got `[4, 0]`
- `lib_base64_encode.py` — want `b'aGVsbG8='` got `[97, 71, 86, 115, 98, 71, 56, 61]`
- `lib_binascii_hexlify.py` — want `b'4142'` got `[52, 49, 52, 50]`
- `lib_collections_ordereddict_move.py`
- `lib_functools_reduce_strings.py` — want `abc` got `21475000864`
- `lib_graphlib_topo.py`
- `lib_gzip_roundtrip.py`
- `lib_hashlib_update.py` — want `aaf4c61d` got `2cf24dba`
- `lib_itertools_product.py` — want `[(1, 'a'), (1, 'b'), (2, 'a'), (2, 'b'` got `[[1, 5368742071], [1, 5368742073], [2,`
- `lib_zlib_crc32.py` — want `True` got `False`
- `map_method_ref.py`
- `r39_priority_sort.py` — want `['b', 'a', 'c']` got `[5368746007, 5368746005, 5368746009]`
- `repr_nested_dict.py` — want `{'a': {'b': {'c': 1}}}` got `{'a': {'b': 8070576}}`
- `sorted_with_two_keys.py` — want `[('a', 1), ('a', 2), ('b', 2)]` got `[('b', 2), ('a', 2), ('a', 1)]`
- `str_format_map.py`
- `str_format_nested_field.py`
- `unicode_in_list_repr.py` — want `['a', '�', 'b']` got `['a', 'é', 'b']`
- `zip_star_unpack.py`

## 5. other compile-time refusal  (15)  — *verified*

- `479_dynamic_classvar_reads.py`
- `del_slice.py`
- `except_multiple_types.py`
- `extended_slice_assign.py`
- `fstring_equals_debug.py`
- `lib_contextlib_closing.py`
- `lib_enum_iteration.py`
- `lib_functools_cmp.py`
- `lib_re_compile.py`
- `property_deleter.py`
- `set_update_multiple.py`
- `slice_assignment_step.py`
- `str_template_manual.py`
- `string_percent_dict.py`
- `vars_of_instance.py`

## 6. corpus defect - the test itself is wrong  (11)  — *verified*

- `211_argparse_module.py`
- `296_collections_namedtuple.py`
- `462_json_dumps_options.py`
- `464_metaclass_keyword.py` — want `Valid Python class-header keyword regr` got `<missing>`
- `468_provider_type_runtime.py` — want `1` got `True`
- `469_guarded_class_string.py` — want `1` got `False`
- `470_global_property_return.py` — want `1` got `True`
- `473_chained_property_method.py` — want `1` got `True`
- `474_boolop_value_flow.py` — want `1` got `False`
- `75_assembly_func.py`
- `lib_calendar_monthrange.py` — want `(5, 29)` got `[5, 29]`

## 7. stdlib bindings: wrong signature (arity/kwargs)  (11)  — *verified*

- `double_star_merge_call.py`
- `enum_functional.py`
- `lib_collections_counter_subtract.py`
- `lib_contextlib_suppress.py`
- `lib_hashlib_md5.py`
- `lib_hashlib_sha256.py`
- `lib_itertools_product_repeat.py`
- `lib_random_randrange.py`
- `lib_string_template.py`
- `lib_types_simplenamespace.py`
- `lib_uuid_int.py`

## 8. operator / indexing / iteration protocol gap  (9)  — *verified*

- `closure_over_multiple.py`
- `dunder_getitem_slice.py`
- `lib_collections_userdict.py`
- `lib_configparser.py`
- `lib_contextlib_manager.py`
- `r39_group_consecutive.py`
- `sim_leaderboard.py`
- `tuple_concat.py`
- `tuple_repeat.py`

## 9. sema inferred the wrong type (compile refusal)  (9)  — *verified*

- `algo_count_islands.py`
- `complex_arithmetic_skip.py`
- `exc_type_error.py`
- `generator_class_iterator.py`
- `lib_collections_defaultdict.py`
- `lib_functools_partial_kw.py`
- `lib_re_sub_func.py`
- `multiple_decorators.py`
- `proj_call_factory.py`

## 10. f-string: nested format spec  (8)  — *verified*

- `app_json_config.py`
- `dict_nested_mutate.py` — want `{'x': {'y': 2}}` got `{'x': {'y': 1844720}}`
- `fstring_nested_fstring.py` — want `3.14` got `{w}.2f`
- `fstring_nested_spec.py` — want `3.14` got `{w - 3}f`
- `lib_json_nested.py`
- `lib_string_formatter.py` — want `x|` got `x}|`
- `nested_dict_comp.py` — want `{0: {0: 0, 1: 0}, 1: {0: 0, 1: 1}}` got `{0: {'0': 0, '1': 0}, 1: {'0': 0, '1':`
- `sim_text_adventure.py` — want `ended at: end` got `ended at: 5368750103`

## 11. boxing: value resolves to 0 / None instead of its contents  (7)  — *strong*

- `468_static_data_descriptor.py` — want `7` got `0`
- `callback_registry.py` — want `['h1', 'h2']` got `[0, 0]`
- `dunder_iadd.py` — want `8` got `0`
- `filter_returns_iterator.py` — want `3` got `0`
- `lambda_capturing_outer_var.py` — want `[30, 30, 30]` got `[0, 0, 0]`
- `list_comp_with_walrus_call.py` — want `[0, 1, 4]` got `[0, 0, 0, 0]`
- `pow_three_arg.py` — want `24` got `0`

## 12. closures / callable values not modelled (compile refusal)  (7)  — *verified*

- `class_hash_in_set.py`
- `complex_number_basic.py`
- `dispatch_table_class_methods.py`
- `lambda_nested.py`
- `lib_csv_dictreader.py`
- `nested_closures.py`
- `proj_event_dispatch.py`

## 13. round(x, n) returns float instead of int  (5)  — *verified*

- `float_rounding_modes.py` — want `2.67` got `2.68`
- `r40_compound_interest.py` — want `1157.62` got `5.131006077194019e+18`
- `r40_percentage_change.py` — want `50.0` got `100.0`
- `round_to_negative_places.py` — want `12300` got `12300.0`
- `sim_discount_calc.py` — want `[80.0, 40.0, 160.0]` got `[100.0, 100.0, 100.0]`

## 14. stdlib bindings: function missing  (5)  — *verified*

- `lib_math_dist.py`
- `lib_math_prod.py`
- `lib_random_gauss.py`
- `lib_time_strftime.py`
- `lib_time_struct.py`

## 15. stdlib module has no bindings at all  (5)  — *verified*

- `lib_csv_writer.py`
- `lib_hmac_new.py`
- `lib_marshal_roundtrip.py`
- `lib_reprlib_repr.py`
- `lib_unicodedata_name.py`

## 16. finally does not run on return  (4)  — *verified*

- `exc_finally_return_override.py` — want `finally` got `try`
- `exc_finally_with_exception.py` — want `finally ran` got `caught`
- `finally_on_return.py` — want `cleanup` got `1`
- `nested_try_finally.py` — want `f1` got `inner`

## 17. arbitrary-precision int absent  (3)  — *verified*

- `bignum_factorial.py` — want `15511210043330985984000000` got `7034535277573963776`
- `bignum_power.py` — want `1267650600228229401496703205376` got `0`
- `int_bignum_pow.py` — want `18446744073709551616` got `0`

## 18. dynamic class creation: 3-argument type()  (3)  — *verified*

- `lib_collections_namedtuple.py`
- `lib_collections_namedtuple_methods.py`
- `namedtuple_unpacking.py`

## 19. parser: syntax not supported  (3)  — *verified*

- `complex_literal.py`
- `dict_dict_comprehension.py`
- `generator_send_skip.py`

## 20. f-string: percent format spec  (2)  — *verified*

- `fstring_percent.py` — want `25%` got `0.25`
- `fstring_percent_format.py` — want `25.0%` got `0.25`

## 21. sort/sorted ignores __lt__ on instances  (2)  — *verified*

- `class_comparison_total.py` — want `True True 1` got `True False 1`
- `class_lt_sort.py` — want `[1, 2, 3]` got `[3, 1, 2]`

## 22. __getattr__ / dynamic attribute unsupported  (1)  — *verified*

- `class_getattr_dynamic.py` — want `dyn_foo` got `0`

## 23. closure captures the wrong value  (1)  — *verified*

- `closure_default_arg_capture.py` — want `[0, 1, 2]` got `[0, 0, 0]`

## 24. exception __str__ ignored  (1)  — *verified*

- `exc_custom_str.py` — want `custom message` got `<missing>`

## 25. mutable default argument rejected  (1)  — *verified*

- `syntax_semicolons.py`
---

# Digging into the two unattributed buckets

Both were reduced by writing minimal probes and running them at head. Everything
below was **reproduced**, not inferred from the symptom.

## stdlib bucket (39) — which function actually breaks

28 of the stdlib symbols these cases use were probed standalone. **18 failed.**
Grouped by the kind of defect the probe exposed:

| defect | symbols |
|---|---|
| crashes (access violation) inside the module | `array.array`, `cmath.sqrt`, `enum.auto`, `graphlib.TopologicalSorter`, `difflib.get_close_matches` |
| returns a list of ints — bytes type absent | `base64.b64encode`, `binascii.hexlify`, `pickle.dumps` |
| bool return degraded to int (`1` not `True`) | `calendar.isleap`, `fnmatch.fnmatch`, `operator.truth` |
| int return degraded to float (`2.0` not `2`) | `statistics.median` |
| returns pointers — boxing | `csv.reader` |
| right elements, wrong ORDER | `heapq.nlargest` |
| compile refusal in the binding | `collections.defaultdict` (E022), `hashlib.md5` (E021), `importlib.import_module` (E001) |
| unimplemented, raises at run time | `zlib.compress` |
| returns a wrong value | `typing.NamedTuple` |

The remaining 25 cases are one-to-three-line programs, so **each case is already its
own minimal repro** and the root cause is simply that API:
`Counter.update` / `sum(Counter.values())`, `date - date` → `.days`,
`Enum` with values, `Fraction(float)`, `chain.from_iterable`, `cycle`+`islice`,
`groupby`, `tee`, `json.loads` (nested), `numbers.Number` with `isinstance`,
`operator.contains` / `methodcaller`, `re.Match.group` (positional and named),
`os.path.splitext` (returns a list, not a tuple), `decimal.getcontext().prec`,
`typing.List` alias, and `try: import missing` (ImportError is never raised, so the
fallback branch is skipped).

`random.seed()` + `randint`/`sample`/`shuffle` are a category of their own: the
generator does not reproduce CPython's Mersenne Twister, so every seeded test
disagrees by construction.

## core-language bucket (64) — verified causes

| root cause | probe result |
|---|---|
| unannotated parameter whose only call site passes a NON-literal | `def merge(a,b)` called from `wrap(x)` → prints `15484640` |
| float default parameter | `def f(x=1.5)` → `3.2055295e-317` |
| float modulo with a negative operand | `-5.5 % 2.0` → `-1.5`, want `0.5` (C `fmod`, not Python's floored `%`) |
| `:e` exponent width | `1.234568e+003`, want `e+03` (C printf 3-digit exponent) |
| format spec `=` alignment | `f"{42:=+8}"` → `42`, want `+     42` |
| boolean operator result loses bool-ness | `a and not b` → `1`, want `True` |
| class used as a VALUE | `{"a": A}["a"].__name__` → crash |
| custom exception attribute | `e.code` after `raise E(7)` → crash |
| `__doc__` access | `f.__doc__` → crash |

Disproved while probing, so these are NOT the cause of any case: nested-function
floats, `__radd__`, exception-hierarchy `except`, direct generator expressions,
`frozenset` membership, decorator result passthrough, dynamic dict-index assign on
an instance, recursion with non-literal arguments, dict int keys, nested dict
comprehensions, tuple-vs-list repr, plain bool repr, float returns, instances in
f-strings, and float sort keys.
