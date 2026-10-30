"""hxInflate writer/reader round-trip tests (the appserver's serialization format).

The grammar these assert comes from the library the client links (proletariatgames/hxinflate,
serialization/Deflater.hx + Inflater.hx) and is documented in DECOMPILE_NOTES "R-spec". If the
writer and the reader here agree, a stream is structurally valid before it ever reaches the guest.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import hxinflate  # noqa: E402

MODEL_ENTITY = "model.entity.ModelEntity"
DSUR = "appserver.remoting.DataStoreUpdateResponse"


def test_header_and_purpose():
    cache = hxinflate.type_cache([], purpose=None)
    assert cache == "ZVER3n", cache
    r = hxinflate.Reader(cache)
    assert r.version == 3
    assert r.purpose is None
    assert r.read_typeinfo_loop() == []

    cache2 = hxinflate.type_cache([], purpose="login")
    assert cache2 == "ZVER3y5:login", cache2
    assert hxinflate.Reader(cache2).purpose == "login"


def test_custom_class_declaration_has_no_field_list():
    decl = hxinflate.Decl(MODEL_ENTITY, custom=True)
    cache = hxinflate.type_cache([decl], purpose=None)
    assert cache == "ZVER3nVy24:%s:i-1:z:t:" % MODEL_ENTITY, cache
    types = hxinflate.Reader(cache).read_typeinfo_loop()
    assert [t.name for t in types] == [MODEL_ENTITY]
    assert types[0].custom is True
    assert types[0].fields == []
    assert types[0].base == -1
    assert types[0].version == 0


def test_non_custom_class_carries_field_names():
    decl = hxinflate.Decl(DSUR, fields=["updatedEntities", "entityTypeInfo", "returnVal"])
    cache = hxinflate.type_cache([decl], purpose=None)
    assert ":i-1:z:f:3:" in cache, cache
    assert cache.endswith("y15:updatedEntitiesy14:entityTypeInfoy9:returnVal:"), cache
    types = hxinflate.Reader(cache).read_typeinfo_loop()
    assert types[0].fields == ["updatedEntities", "entityTypeInfo", "returnVal"]
    assert types[0].custom is False


def test_bools_zero_and_ints_are_encoded_per_spec():
    # z = 0, i<n> otherwise, t/f for bools - not "i0"/"i1"
    assert hxinflate.Writer(header=False, purpose=None).value(0).text() == "nz"
    assert hxinflate.Writer(header=False, purpose=None).value(7).text() == "ni7"
    assert hxinflate.Writer(header=False, purpose=None).value(-1).text() == "ni-1"
    assert hxinflate.Writer(header=False, purpose=None).value(True).text() == "nt"
    assert hxinflate.Writer(header=False, purpose=None).value(False).text() == "nf"


def test_intmap_keys_are_raw_digits():
    text = hxinflate.Writer(header=False, purpose=None).value({3: True, 12: None}).text()
    assert text == "nq:3t:12nh", text
    assert hxinflate.Reader(text, skip_header=True).value() == {3: True, 12: None}


def test_strings_use_a_table_with_R_refs():
    w = hxinflate.Writer(header=False, purpose=None)
    w.value(["dup", "dup"])
    text = w.text()
    assert text == 'nay3:dupR0h', text
    assert hxinflate.Reader(text, skip_header=True).value() == ["dup", "dup"]


def test_object_stream_round_trip_with_declaration():
    decl = hxinflate.Decl(MODEL_ENTITY, custom=True)
    text = hxinflate.object_stream(hxinflate.Instance(values=[1, "x"], decl=decl))
    assert text == "nVy24:%s:i-1:z:t:i1y1:xg" % MODEL_ENTITY, text
    r = hxinflate.Reader(text, skip_header=True)      # entity streams are read with skipHeader
    assert r.version == 3
    val = r.value()
    assert val["decl"].name == MODEL_ENTITY
    assert val["body"] == [1, "x"]


def test_cached_type_reference_form():
    decl = hxinflate.Decl(MODEL_ENTITY, custom=True)
    text = hxinflate.object_stream(hxinflate.Instance(values=[True, [1, None, 2]], idx=0))
    assert text == "nT0:tai1ni2hg", text
    r = hxinflate.Reader(text, skip_header=True)
    r.types = [decl]
    val = r.value()
    assert val["type"] == 0
    assert val["body"] == [True, [1, None, 2]]


def test_arrays_use_null_runs():
    text = hxinflate.Writer(header=False, purpose=None).value([None, None, 5]).text()
    assert text == "nau2i5h", text
    assert hxinflate.Reader(text, skip_header=True).value() == [None, None, 5]


def test_string_map_uses_b_and_h():
    text = hxinflate.Writer(header=False, purpose=None).value({"k": 1}).text()
    assert text == "nby1:ki1h", text
    assert hxinflate.Reader(text, skip_header=True).value() == {"k": 1}


def test_skip_code_is_S_for_version_3():
    # a non-custom instance may mark a field as not-serialized with "S" (was "E" before v3)
    decl = hxinflate.Decl(DSUR, fields=["a", "b"])
    text = hxinflate.object_stream(hxinflate.Instance(values=[hxinflate.Raw("S"), 1], decl=decl))
    r = hxinflate.Reader(text, skip_header=True)
    assert r.skip_code == "S"
    val = r.value()
    assert val["body"] == ["<skip>", 1]


def test_radix_insert_matches_real_game_data():
    # the actual prefix of the game's compiled template blob (TPLC…ZVER3!0:4:game…)
    r = hxinflate.Reader("ZVER3!0:4:game", radix_relative=True)
    assert r.version == 3
    assert r.purpose == "game"


def test_radix_split_concatenates_the_prefix():
    # !0:6:abcdef then #1:3:3:xyz splits node 1 after 3 chars and appends "xyz"
    r = hxinflate.Reader("ZVER3!0:6:abcdef#1:3:3:xyz", radix_relative=True)
    assert r.purpose == "abcdef"
    assert r.value() == "abcxyz"


def test_radix_reference_token():
    # ~ re-references an existing node
    r = hxinflate.Reader("ZVER3!0:4:game~0", radix_relative=True)
    assert r.purpose == "game"
    # lastOffset was 0 for the first insert, so ~0 re-reads node 0
    assert r.value() == ""


def test_enum_value_reads_its_params():
    # = construct ':' typeIndex ':' enumIndex ':' numParams ':' then the params
    r = hxinflate.Reader("ZVER3n=y4:Test:0:1:2:tz")
    assert r.purpose is None
    v = r.value()
    assert v["enum"] == "Test" and v["params"] == [True, 0], v
