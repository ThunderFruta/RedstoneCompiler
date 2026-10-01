"""Independent byte oracles for supported typed-route authority values."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath

import pytest

from PhysicalDesign.Routing.Global.TypedRouteConsumer import (
    AuthorityIdentity,
    BuildTypedRouteOriginDescriptor,
    CanonicalAuthority,
    CanonicalJson,
    TypedRouteOriginDescriptor,
    TypedRouteOriginMatchesCurrentInputs,
)
from PhysicalDesign.Routing.Planning.ChannelPlanner import NetRoutingProfile


# Frozen before implementation inspection: the required projection must preserve
# literal ASCII JSON bytes, independently hashed identities, and owned snapshots.
# These oracles challenge string fallbacks, lossy map keys, container ordering,
# projection precedence, signed zero, nonfinite values, and shallow ownership.
# Expected bytes are handwritten; stdlib JSON only serializes the actual result.
def _AssertCanonicalBytes(Value, Expected):
    ActualProjection = json.dumps(
        CanonicalAuthority(Value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    assert ActualProjection == Expected
    assert CanonicalJson(Value).encode("ascii") == Expected
    assert AuthorityIdentity(Value) == hashlib.sha256(Expected).hexdigest()


@pytest.mark.parametrize(
    ("Value", "Expected"),
    (
        (None, b"null"),
        (False, b"false"),
        (True, b"true"),
        (-123456789012345678901234567890, b"-123456789012345678901234567890"),
        (0, b"0"),
        (0.0, b"0.0"),
        (-0.0, b"-0.0"),
        (1.25, b"1.25"),
        (1e20, b"1e+20"),
        (1e-7, b"1e-07"),
        ("", b'""'),
        ('\u00e9\n"\\\U0001f600', b'"\\u00e9\\n\\"\\\\\\ud83d\\ude00"'),
    ),
)
def test_scalar_canonical_bytes_and_sha256(Value, Expected):
    _AssertCanonicalBytes(Value, Expected)


def test_string_object_keys_sort_before_ascii_escaping():
    Value = {"\U0001f600": 4, "\ue000": 3, "\u00e9": 2, "a": 1}
    Expected = b'{"a":1,"\\u00e9":2,"\\ue000":3,"\\ud83d\\ude00":4}'
    _AssertCanonicalBytes(Value, Expected)
    _AssertCanonicalBytes(dict(reversed(tuple(Value.items()))), Expected)


class _AuthorityEnum(Enum):
    Selected = ("\u00e9", -0.0)


@dataclass
class _AuthorityRecord:
    z: object
    a: object


def test_sequences_enum_path_and_dataclass_keep_their_projection():
    Value = _AuthorityRecord(
        z=[_AuthorityEnum.Selected, (True, None)],
        a=PurePosixPath("routes/\u00e9.v"),
    )
    Expected = (
        b'{"a":{"SchemaVersion":"joint-canonical-source-path-v1",'
        b'"Value":"routes/\\u00e9.v"},"z":[["\\u00e9",-0.0],[true,null]]}'
    )
    _AssertCanonicalBytes(Value, Expected)


class _AuthorityMapping(Mapping):
    def __init__(self, Items):
        self.Items = dict(Items)

    def __getitem__(self, Key):
        return self.Items[Key]

    def __iter__(self):
        return iter(self.Items)

    def __len__(self):
        return len(self.Items)


class _ProjectedMapping(_AuthorityMapping):
    def ToDictionary(self):
        return {"tag": "export", "chosen": (2, 1)}


@dataclass
class _ProjectedRecord:
    Hidden: object

    def ToDictionary(self):
        return {"tag": "export", "chosen": (2, 1)}


@pytest.mark.parametrize(
    "Value",
    (
        _ProjectedMapping({"hidden": object()}),
        _ProjectedRecord(object()),
    ),
    ids=("mapping", "dataclass"),
)
def test_callable_dictionary_projection_precedes_container_fields(Value):
    _AssertCanonicalBytes(Value, b'{"chosen":[2,1],"tag":"export"}')


def test_custom_string_mapping_uses_the_object_schema():
    _AssertCanonicalBytes(
        _AuthorityMapping((("z", (2, 1)), ("a", {"\u00e9": False}))),
        b'{"a":{"\\u00e9":false},"z":[2,1]}',
    )


def test_mixed_map_keys_keep_types_and_sort_by_canonical_key_json():
    Items = (
        ((2, "x"), "tuple"),
        (2, "two"),
        (10, "ten"),
        (None, "null"),
        ("\ue000", "private"),
        ("\U0001f600", "emoji"),
        ("2", "string"),
    )
    Expected = (
        b'{"Entries":[{"Key":"2","Value":"string"},'
        b'{"Key":"\\ud83d\\ude00","Value":"emoji"},'
        b'{"Key":"\\ue000","Value":"private"},'
        b'{"Key":10,"Value":"ten"},{"Key":2,"Value":"two"},'
        b'{"Key":[2,"x"],"Value":"tuple"},{"Key":null,"Value":"null"}],'
        b'"SchemaVersion":"joint-canonical-authority-map-v1"}'
    )
    for OrderedItems in (Items, tuple(reversed(Items))):
        _AssertCanonicalBytes(dict(OrderedItems), Expected)
        _AssertCanonicalBytes(_AuthorityMapping(OrderedItems), Expected)


def _AssertFreshCanonicalBytes(Build, Expected):
    """Each API receives a fresh input when projection itself changes its source."""
    ActualProjection = json.dumps(
        CanonicalAuthority(Build()),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    assert ActualProjection == Expected
    assert CanonicalJson(Build()).encode("ascii") == Expected
    assert AuthorityIdentity(Build()) == hashlib.sha256(Expected).hexdigest()


@pytest.mark.parametrize(
    ("HasSecondKey", "Replacement", "Expected"),
    (
        (
            False,
            {1: 7},
            b'{"Entries":[{"Key":"a","Value":"projected"}],'
            b'"SchemaVersion":"joint-canonical-authority-map-v1"}',
        ),
        (
            True,
            {"new": 7, (1, 2): 9},
            b'{"Entries":[{"Key":"a","Value":"projected"},'
            b'{"Key":[1,2],"Value":9}],'
            b'"SchemaVersion":"joint-canonical-authority-map-v1"}',
        ),
        (
            True,
            {1: 7, "a": 9},
            b'{"Entries":[{"Key":"a","Value":"projected"},'
            b'{"Key":"a","Value":9}],'
            b'"SchemaVersion":"joint-canonical-authority-map-v1"}',
        ),
        (True, {"new": 7, "a": 9}, b'{"a":9}'),
    ),
    ids=(
        "classify-after-descendants", "project-newly-observed-key",
        "retain-stable-duplicate-entries", "collapse-duplicate-object-keys",
    ),
)
def test_mutating_descendants_preserve_observed_entries_and_final_map_classification(
    HasSecondKey, Replacement, Expected
):
    # Compatibility includes descendant hooks: preserve entries observed during
    # iteration, then classify against the source keys left after projection.
    def Build():
        Source = {}

        class Producer:
            def ToDictionary(self):
                Source.clear()
                Source.update(Replacement)
                return "projected"

        Source["a"] = Producer()
        if HasSecondKey:
            Source["b"] = 0
        return Source

    _AssertFreshCanonicalBytes(Build, Expected)


def test_mapping_hooks_keep_key_value_order_and_stable_equal_canonical_keys():
    Observations = []

    def Build():
        Events = []
        Observations.append(Events)

        class Producer:
            def __init__(self, Event, Result):
                self.Event = Event
                self.Result = Result

            def ToDictionary(self):
                Events.append(self.Event)
                return self.Result

        return {
            Producer("key-1", "same"): Producer("value-1", "first"),
            Producer("key-2", "same"): Producer("value-2", "second"),
        }

    Expected = (
        b'{"Entries":[{"Key":"same","Value":"first"},'
        b'{"Key":"same","Value":"second"}],'
        b'"SchemaVersion":"joint-canonical-authority-map-v1"}'
    )
    _AssertFreshCanonicalBytes(Build, Expected)
    assert Observations == [
        ["key-1", "value-1", "key-2", "value-2"],
        ["key-1", "value-1", "key-2", "value-2"],
        ["key-1", "value-1", "key-2", "value-2"],
    ]


@pytest.mark.parametrize("Container", (set, frozenset), ids=("set", "frozenset"))
def test_unordered_values_sort_by_canonical_json_not_python_value(Container):
    Value = Container(("\u00e9", "Z", "a", "\U0001f600", 10, 2, (1, "x"), None))
    Expected = b'["Z","\\u00e9","\\ud83d\\ude00","a",10,2,[1,"x"],null]'
    _AssertCanonicalBytes(Value, Expected)


@pytest.mark.parametrize(
    "Value",
    (
        float("nan"),
        float("inf"),
        float("-inf"),
        {"nested": [float("nan")]},
        {float("inf"): "invalid key"},
        frozenset((float("-inf"),)),
        object(),
        b"not text",
        complex(1, 2),
        {"nested": [object()]},
    ),
    ids=(
        "nan", "positive-infinity", "negative-infinity", "nested-nan",
        "infinite-map-key", "infinite-set-member", "object", "bytes", "complex",
        "nested-object",
    ),
)
def test_nonfinite_and_unsupported_authorities_are_rejected(Value):
    for Project in (CanonicalAuthority, CanonicalJson, AuthorityIdentity):
        with pytest.raises((TypeError, ValueError)):
            Project(Value)


def test_frozen_origin_owns_nested_inputs_and_each_export():
    Source = {"nested": [1, {"tag": "source"}]}
    Target = {"nested": [2, {"tag": "target"}]}
    Fragments = {"nodes": [{"position": [1, 2, 3]}]}
    Origin = TypedRouteOriginDescriptor(
        Signal="wire",
        SourcePortal=Source,
        TargetPortals=(Target,),
        Guide=((0, 0), (1, 0)),
        Layer=0,
        Axis="X",
        Lane=0,
        Variant=0,
        ImmutableFragments=Fragments,
    )
    FragmentBytes = (
        b'{"Fragments":{"nodes":[{"position":[1,2,3]}]},'
        b'"SchemaVersion":"joint-typed-route-immutable-fragments-v1"}'
    )
    Expected = (
        b'{"Axis":"X","Guide":[[0,0],[1,0]],"ImmutableFragmentIdentity":'
        b'"782b0b7531b463651a6d0357f00e2621b0fe4932a3a788d8e4e4c0c3060c4e24",'
        b'"ImmutableFragments":{"nodes":[{"position":[1,2,3]}]},'
        b'"Lane":0,"Layer":0,"SchemaVersion":"joint-typed-route-origin-descriptor-v1",'
        b'"Signal":"wire","SourcePortal":{"nested":[1,{"tag":"source"}]},'
        b'"TargetPortals":[{"nested":[2,{"tag":"target"}]}],"Variant":0}'
    )
    # Mutate sources before the first identity/export read to defeat a cached
    # identity hiding a shallow snapshot. Mutate an export before the next read.
    Source["nested"][1]["tag"] = "changed source"
    Target["nested"].append(3)
    Fragments["nodes"][0]["position"][0] = 99
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.ImmutableFragmentIdentity == hashlib.sha256(FragmentBytes).hexdigest()
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()

    Exported = Origin.ToDictionary()
    Exported["SourcePortal"]["nested"][1]["tag"] = "changed export"
    Exported["TargetPortals"][0]["nested"].clear()
    Exported["ImmutableFragments"]["nodes"][0]["position"].append(4)
    Exported["Guide"][0][0] = 99
    _AssertCanonicalBytes(Origin, Expected)
    _AssertCanonicalBytes(Origin.ToDictionary(), Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()


def test_origin_subclass_identity_canonicalizes_its_exported_document():
    class ExtendedOrigin(TypedRouteOriginDescriptor):
        def ToDictionary(self):
            return {"labels": {"z", "a"}}

    Origin = ExtendedOrigin(
        Signal="wire",
        SourcePortal=None,
        TargetPortals=(),
        Guide=(),
        Layer=0,
        Axis="X",
        Lane=0,
        Variant=0,
        ImmutableFragments=None,
    )
    Expected = b'{"labels":["a","z"]}'
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()


def test_origin_identity_canonicalizes_an_accepted_custom_axis():
    class ProjectedAxis:
        def __eq__(self, Other):
            return type(Other) is str and Other == "X"

        def ToDictionary(self):
            return {"direction": "X"}

    Origin = TypedRouteOriginDescriptor(
        Signal="wire",
        SourcePortal=None,
        TargetPortals=(),
        Guide=(),
        Layer=0,
        Axis=ProjectedAxis(),
        Lane=0,
        Variant=0,
        ImmutableFragments=None,
    )
    Expected = (
        b'{"Axis":{"direction":"X"},"Guide":[],"ImmutableFragmentIdentity":'
        b'"ce73fa38a482721239751f74e59ac0e99bde5c18d43808eabe21d0a36a21d2fc",'
        b'"ImmutableFragments":null,"Lane":0,"Layer":0,'
        b'"SchemaVersion":"joint-typed-route-origin-descriptor-v1","Signal":"wire",'
        b'"SourcePortal":null,"TargetPortals":[],"Variant":0}'
    )
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()


def test_origin_identity_canonicalizes_an_accepted_nested_tuple_enum():
    class Inner(Enum):
        Labels = {"z", "a"}

    class Outer(tuple, Enum):
        Fields = (Inner.Labels,)

    Origin = TypedRouteOriginDescriptor(
        Signal="wire",
        SourcePortal=Outer.Fields,
        TargetPortals=(),
        Guide=(),
        Layer=0,
        Axis="X",
        Lane=0,
        Variant=0,
        ImmutableFragments=None,
    )
    Expected = (
        b'{"Axis":"X","Guide":[],"ImmutableFragmentIdentity":'
        b'"ce73fa38a482721239751f74e59ac0e99bde5c18d43808eabe21d0a36a21d2fc",'
        b'"ImmutableFragments":null,"Lane":0,"Layer":0,'
        b'"SchemaVersion":"joint-typed-route-origin-descriptor-v1","Signal":"wire",'
        b'"SourcePortal":[["a","z"]],"TargetPortals":[],"Variant":0}'
    )
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()


def test_origin_identity_rejects_a_nonfinite_key_in_an_accepted_dict_enum():
    class Inner(Enum):
        Invalid = float("nan")

    class Outer(dict, Enum):
        Fields = {Inner.Invalid: 1}

    Origin = TypedRouteOriginDescriptor(
        Signal="wire",
        SourcePortal=Outer.Fields,
        TargetPortals=(),
        Guide=(),
        Layer=0,
        Axis="X",
        Lane=0,
        Variant=0,
        ImmutableFragments=None,
    )
    for Project in (CanonicalAuthority, CanonicalJson, AuthorityIdentity):
        with pytest.raises(TypeError):
            Project(Origin)
    with pytest.raises(TypeError):
        _ = Origin.Identity


@dataclass
class _CurrentInputPortal:
    Path: tuple
    Marker: object
    Event: str
    Events: list | None

    def ToDictionary(self):
        if self.Events is not None:
            self.Events.append(self.Event)
        return {"Marker": self.Marker, "Path": self.Path}


def _CurrentInputs(SourceMarker="source", Events=None):
    Source = _CurrentInputPortal(((0, 0, 0),), SourceMarker, "source", Events)
    Target = _CurrentInputPortal(((1, 0, 0),), "target", "target", Events)
    Profile = NetRoutingProfile(
        Signal="wire", Root=(0, 0, 0), Targets=((1, 0, 0),), Span=1,
        Fanout=1, RetryCount=0, Criticality=0, IsTrunk=False,
        SourceAccessPath=((0, 0, 0),),
        TargetAccessPaths={(1, 0, 0): ((1, 0, 0),)},
    )
    Metadata = (Source, (Target,), ((0, 0), (1, 0)), 0, "X", 0, 0)
    return Profile, Metadata


def test_current_input_matcher_preserves_the_literal_origin_identity():
    Profile, Metadata = _CurrentInputs()
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    Expected = (
        b'{"Axis":"X","Guide":[[0,0],[1,0]],"ImmutableFragmentIdentity":'
        b'"549344d428fe2cc6bc4a90f2764a7d5cefe2adaacdff19ac7e81b40bf160b31f",'
        b'"ImmutableFragments":{"SchemaVersion":"joint-typed-route-immutable-fragments-v1",'
        b'"SeedLocalClaims":[],"SourceAccessPath":[[0,0,0]],"SourcePortalPath":[[0,0,0]],'
        b'"TargetFragments":[{"AccessPath":[[1,0,0]],"Portal":{"Marker":"target",'
        b'"Path":[[1,0,0]]},"Target":[1,0,0]}]},"Lane":0,"Layer":0,'
        b'"SchemaVersion":"joint-typed-route-origin-descriptor-v1","Signal":"wire",'
        b'"SourcePortal":{"Marker":"source","Path":[[0,0,0]]},'
        b'"TargetPortals":[{"Marker":"target","Path":[[1,0,0]]}],"Variant":0}'
    )
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()
    assert TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, Metadata) is True
    FreshProfile, FreshMetadata = _CurrentInputs()
    assert TypedRouteOriginMatchesCurrentInputs(
        Origin, "wire", FreshProfile, FreshMetadata
    ) is True


@pytest.mark.parametrize("Changed", ("source", "target", "guide", "access-path", "signal"))
def test_current_input_matcher_rejects_observable_origin_drift(Changed):
    Profile, Metadata = _CurrentInputs()
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    Signal = "wire"
    if Changed == "source":
        Metadata[0].Marker = "changed"
    elif Changed == "target":
        Metadata[1][0].Marker = "changed"
    elif Changed == "guide":
        Metadata = (*Metadata[:2], ((0, 0), (2, 0)), *Metadata[3:])
    elif Changed == "access-path":
        Profile = replace(Profile, SourceAccessPath=((0, 0, 0), (0, 0, 1)))
    else:
        Signal = "another-wire"
    assert TypedRouteOriginMatchesCurrentInputs(Origin, Signal, Profile, Metadata) is False


@pytest.mark.parametrize(
    ("Old", "New", "OldBytes", "NewBytes"),
    (
        (0, False, b"0", b"false"),
        (0, 0.0, b"0", b"0.0"),
        (0.0, -0.0, b"0.0", b"-0.0"),
    ),
    ids=("integer-to-bool", "integer-to-float", "float-signed-zero"),
)
def test_current_input_matcher_uses_canonical_bytes_not_python_numeric_equality(
    Old, New, OldBytes, NewBytes
):
    assert Old == New
    Profile, Metadata = _CurrentInputs(Old)
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    _AssertCanonicalBytes(Origin.SourcePortal["Marker"], OldBytes)
    Metadata[0].Marker = New
    _AssertCanonicalBytes(Metadata[0].Marker, NewBytes)
    assert TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, Metadata) is False


class _EquivalentSourceMarker(Enum):
    Source = "source"


class _ProjectedSourceMarker:
    def ToDictionary(self):
        return "source"


class _ProjectedCurrentAxis:
    def __eq__(self, Other):
        return type(Other) is str and Other == "X"

    def ToDictionary(self):
        return "X"


@pytest.mark.parametrize(
    ("Marker", "Axis"),
    (
        (_EquivalentSourceMarker.Source, "X"),
        (_ProjectedSourceMarker(), "X"),
        ("source", _ProjectedCurrentAxis()),
    ),
    ids=("enum-marker", "projected-marker", "projected-axis"),
)
def test_current_input_matcher_accepts_equal_canonical_custom_projections(Marker, Axis):
    Profile, Metadata = _CurrentInputs()
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    Metadata[0].Marker = Marker
    Metadata = (*Metadata[:4], Axis, *Metadata[5:])
    _AssertCanonicalBytes(Marker, b'"source"')
    _AssertCanonicalBytes(Axis, b'"X"')
    assert TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, Metadata) is True


def test_current_input_matcher_accepts_distinct_strings_with_identical_ascii_bytes():
    Profile, Metadata = _CurrentInputs("\U0001f600")
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    Metadata[0].Marker = "\ud83d\ude00"
    assert "\U0001f600" != Metadata[0].Marker
    _AssertCanonicalBytes(Origin.SourcePortal["Marker"], b'"\\ud83d\\ude00"')
    _AssertCanonicalBytes(Metadata[0].Marker, b'"\\ud83d\\ude00"')
    assert TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, Metadata) is True


@pytest.mark.parametrize("Field", ("guide", "layer", "lane", "variant"))
def test_current_input_matcher_preserves_invalid_bool_coordinate_and_scalar_errors(Field):
    Profile, Metadata = _CurrentInputs()
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    Changed = list(Metadata)
    if Field == "guide":
        Changed[2] = ((False, 0), (1, 0))
    else:
        Changed[{"layer": 3, "lane": 5, "variant": 6}[Field]] = False
    assert tuple(Changed) == Metadata
    with pytest.raises(TypeError):
        TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, tuple(Changed))


def test_current_input_fallback_preserves_one_complete_producer_observation():
    Events = []
    Profile, Metadata = _CurrentInputs(Events=Events)
    Origin = BuildTypedRouteOriginDescriptor("wire", Profile, Metadata)
    assert Events == ["target", "source", "target"]
    Events.clear()
    Metadata[0].Marker = "changed"
    assert TypedRouteOriginMatchesCurrentInputs(Origin, "wire", Profile, Metadata) is False
    assert Events == ["target", "source", "target"]


def test_owned_origin_sorts_keys_and_detaches_shared_source_and_export_lists():
    Shared = [{"z": 2, "a": 1}]
    Source = {"z": Shared, "a": Shared}
    Origin = TypedRouteOriginDescriptor(
        Signal="wire", SourcePortal=Source, TargetPortals=(), Guide=(),
        Layer=0, Axis="X", Lane=0, Variant=0, ImmutableFragments=None,
    )
    Expected = (
        b'{"Axis":"X","Guide":[],"ImmutableFragmentIdentity":'
        b'"ce73fa38a482721239751f74e59ac0e99bde5c18d43808eabe21d0a36a21d2fc",'
        b'"ImmutableFragments":null,"Lane":0,"Layer":0,'
        b'"SchemaVersion":"joint-typed-route-origin-descriptor-v1","Signal":"wire",'
        b'"SourcePortal":{"a":[{"a":1,"z":2}],"z":[{"a":1,"z":2}]},'
        b'"TargetPortals":[],"Variant":0}'
    )
    Shared[0]["a"] = 90
    Shared.append({"a": 3})
    Source["new"] = []
    assert list(Origin.SourcePortal) == ["a", "z"]
    assert list(Origin.SourcePortal["a"][0]) == ["a", "z"]
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()

    Exported = Origin.ToDictionary()
    Exported["SourcePortal"]["a"][0]["a"] = 99
    Exported["SourcePortal"]["a"].append({"a": 4})
    assert Exported["SourcePortal"]["z"] == [{"a": 1, "z": 2}]
    _AssertCanonicalBytes(Origin.ToDictionary(), Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()


def test_origin_ownership_fallback_does_not_speculatively_call_descendant_producers():
    Events = []

    class Producer(list):
        def __init__(self, Event, Values):
            super().__init__(Values)
            self.Event = Event

        def ToDictionary(self):
            Events.append(self.Event)
            return list(self)

    Source = {
        "a": [0, None, True],
        "c": Producer("second", [2]),
        "b": Producer("first", [1]),
    }
    Origin = TypedRouteOriginDescriptor(
        Signal="wire", SourcePortal=Source, TargetPortals=(), Guide=(),
        Layer=0, Axis="X", Lane=0, Variant=0, ImmutableFragments=None,
    )
    # Ordinary prefix traversal must not evaluate the unsupported descendants.
    # The original fallback observes their insertion order once, before sorting.
    assert Events == ["second", "first"]
    Expected = (
        b'{"Axis":"X","Guide":[],"ImmutableFragmentIdentity":'
        b'"ce73fa38a482721239751f74e59ac0e99bde5c18d43808eabe21d0a36a21d2fc",'
        b'"ImmutableFragments":null,"Lane":0,"Layer":0,'
        b'"SchemaVersion":"joint-typed-route-origin-descriptor-v1","Signal":"wire",'
        b'"SourcePortal":{"a":[0,null,true],"b":[1],"c":[2]},'
        b'"TargetPortals":[],"Variant":0}'
    )
    _AssertCanonicalBytes(Origin, Expected)
    assert Origin.Identity == hashlib.sha256(Expected).hexdigest()
