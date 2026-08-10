"""One bounded authoritative track-assignment selection across templates.

Placement alternatives own mutually exclusive resource graphs.  They cannot
be flattened into one ordinary assignment because that would require every
placement to route simultaneously.  This module instead presents those raw
domains as one deterministic selection problem: a complete capacity core for
one template permits the next fixed template to be considered, while work or
deadline exhaustion terminates the whole problem as incomplete.

The existing Rust ``RoutingContext`` assignment binding remains authoritative
for each raw physical domain.  The aggregate selector carries one immutable
work counter and one absolute deadline across those calls; it is not a retry
or a route attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cached_property
from hashlib import sha256
from struct import pack
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from .AuthoritativePlanner import (
    BuildTrackAssignmentPreparationFromRawDomain,
    MergeRoutingResourceClaims,
    PlacementAccessStubContractRequirementName,
    RawTrackAssignmentDomain,
    RawTrackAssignmentValue,
)
from .Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from .Models import TrackAssignmentPreparation
from .Reliability import BuildStableFingerprint, RoutingDeadline
from .ResourceGraph import FindClaimConflicts, RoutingResourceClaims

try:
    from ..RustRouting import (
        SolveCompactTemplateFactorCatalogBounded as _SolveCompactTemplateFactorCatalogBounded,
        SolveTemplateAssignmentDomainsBounded as _SolveTemplateAssignmentDomainsBounded,
    )
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import (
            SolveCompactTemplateFactorCatalogBounded as _SolveCompactTemplateFactorCatalogBounded,
            SolveTemplateAssignmentDomainsBounded as _SolveTemplateAssignmentDomainsBounded,
        )
    except Exception:
        _SolveCompactTemplateFactorCatalogBounded = None
        _SolveTemplateAssignmentDomainsBounded = None


@dataclass(frozen=True)
class CompactPhysicalClaimPrimitive:
    """One complete immutable physical claim interned across the portfolio."""

    PrimitiveId: str
    PhysicalFingerprint: str
    Kind: str
    Claims: RoutingResourceClaims = field(compare=False, repr=False)
    DeferredGuideWireCells: tuple[tuple[int, int, int], ...] = field(
        default=(),
        compare=False,
        repr=False,
    )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "PrimitiveId": self.PrimitiveId,
            "PhysicalFingerprint": self.PhysicalFingerprint,
            "Kind": self.Kind,
            "ResourceCount": sum(map(len, (
                self.Claims.WireCells,
                self.Claims.SupportCells,
                self.Claims.RequiredAirCells,
                self.Claims.ElectricalCells,
            ))) + len(self.DeferredGuideWireCells),
            "DeferredGuideSpine": bool(self.DeferredGuideWireCells),
        }


@dataclass(frozen=True)
class CompactFactorValue:
    """One required-variable value referencing exact claim primitives."""

    Variable: str
    OwnerSignal: str
    FactorId: str
    SourceFactorId: str
    ValueKind: str
    PrimitiveIds: tuple[str, ...]
    Objective: tuple[int, int, int, int, int]
    ContractRequirements: tuple[tuple[str, str], ...]
    RouteGuideFactorDescriptor: Any | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    MaterializabilityCertificate: Any | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not self.Variable or not self.OwnerSignal or not self.FactorId:
            raise ValueError("compact factors require stable identities")
        if self.PrimitiveIds != tuple(sorted(set(self.PrimitiveIds))):
            raise ValueError("compact factor primitive references must be canonical")
        if self.ContractRequirements != tuple(sorted(set(
            self.ContractRequirements
        ))):
            raise ValueError("compact factor requirements must be canonical")
        Names = tuple(Name for Name, _Value in self.ContractRequirements)
        if len(Names) != len(set(Names)):
            raise ValueError("compact factor repeats requirement ownership")
        if self.ValueKind == "guide-factor" and (
            self.MaterializabilityCertificate is None
            or not self.MaterializabilityCertificate.Complete
            or not self.MaterializabilityCertificate.Supported
            or self.MaterializabilityCertificate.FactorId != self.FactorId
        ):
            raise ValueError(
                "compact guide factors require a complete supported "
                "materializability certificate"
            )

    @property
    def EncodedContractRequirements(self) -> str:
        return ";".join(
            f"{Name}={Value}"
            for Name, Value in self.ContractRequirements
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Variable": self.Variable,
            "OwnerSignal": self.OwnerSignal,
            "FactorId": self.FactorId,
            "SourceFactorId": self.SourceFactorId,
            "ValueKind": self.ValueKind,
            "PrimitiveIds": list(self.PrimitiveIds),
            "Objective": list(self.Objective),
            "ContractRequirements": [
                list(Value) for Value in self.ContractRequirements
            ],
            "MaterializabilityCertificate": (
                self.MaterializabilityCertificate.ToDictionary()
                if self.MaterializabilityCertificate is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CompactFactorMemberSource:
    """Complete physical inputs for one independently selectable world."""

    TemplateId: str
    Objective: tuple[int, ...]
    ContractRequirements: tuple[tuple[str, str], ...]
    GuideDomain: RawTrackAssignmentDomain
    Fabric: Any = field(compare=False, repr=False)
    FabricFingerprint: str = ""


@dataclass(frozen=True)
class CompactFactorMember:
    """A light member view over the portfolio-global primitive catalog."""

    TemplateId: str
    Objective: tuple[int, ...]
    ContractRequirements: tuple[tuple[str, str], ...]
    RequiredVariables: tuple[str, ...]
    Values: tuple[CompactFactorValue, ...]
    PlacementFingerprint: str
    ResourceGraphFingerprint: str
    FabricFingerprint: str
    CandidateDomainFingerprint: str
    LocalClaimDomainFingerprint: str
    PortalDomainFingerprint: str
    MaximumAssignmentExpansions: int
    Complete: bool
    IncompleteReason: str = ""
    GuideDomainDiagnostics: tuple[tuple[str, object], ...] = ()

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "ContractRequirements": [
                list(Value) for Value in self.ContractRequirements
            ],
            "RequiredVariables": list(self.RequiredVariables),
            "FactorCount": len(self.Values),
            "FactorReferenceCount": sum(
                len(Value.PrimitiveIds) for Value in self.Values
            ),
            "PlacementFingerprint": self.PlacementFingerprint,
            "ResourceGraphFingerprint": self.ResourceGraphFingerprint,
            "FabricFingerprint": self.FabricFingerprint,
            "CandidateDomainFingerprint": self.CandidateDomainFingerprint,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class CompactFactorCatalog:
    """Exact interned compact portfolio consumed by one native call."""

    ResourcePositions: tuple[tuple[int, int, int], ...]
    Primitives: tuple[CompactPhysicalClaimPrimitive, ...]
    Members: tuple[CompactFactorMember, ...]
    PrimitiveCacheHits: int
    PrimitiveCacheMisses: int
    ExpandedPrimitiveReferenceCount: int
    MaximumAssignmentExpansions: int
    NonExhaustiveTemplateDomain: bool = True

    @cached_property
    def CatalogFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "compact-factor-catalog-v1",
            "Resources": self.ResourcePositions,
            "Primitives": [Value.ToDictionary() for Value in self.Primitives],
            "Members": [Value.ToDictionary() for Value in self.Members],
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
            "NonExhaustiveTemplateDomain": self.NonExhaustiveTemplateDomain,
        })

    def ToDictionary(self) -> dict[str, object]:
        ReferenceCount = sum(
            len(Value.PrimitiveIds)
            for Member in self.Members
            for Value in Member.Values
        )
        return {
            "CatalogFingerprint": self.CatalogFingerprint,
            "DeclaredMemberCount": len(self.Members),
            "CatalogCompleteMemberCount": sum(
                Value.Complete for Value in self.Members
            ),
            "ResourcePositionCount": len(self.ResourcePositions),
            "PrimitiveCount": len(self.Primitives),
            "FactorCount": sum(len(Value.Values) for Value in self.Members),
            "FactorReferenceCount": ReferenceCount,
            "ExpandedPrimitiveReferenceCount": (
                self.ExpandedPrimitiveReferenceCount
            ),
            "PrimitiveCompressionRatio": round(
                self.ExpandedPrimitiveReferenceCount
                / max(1, len(self.Primitives)),
                6,
            ),
            "PrimitiveCacheHits": self.PrimitiveCacheHits,
            "PrimitiveCacheMisses": self.PrimitiveCacheMisses,
            "Members": [Value.ToDictionary() for Value in self.Members],
        }


def _CompactPrimitiveFingerprint(
    Kind: str,
    IdentityItems: tuple[tuple[str, object], ...],
    ClaimsFingerprint: str,
) -> str:
    # This vocabulary is closed over strings, integers and canonical tuples.
    # A single canonical tuple encoding avoids dozens of tiny hasher updates
    # per primitive while retaining every physical identity field.
    return sha256(repr((
        "compact-physical-primitive-v3",
        Kind,
        IdentityItems,
        ClaimsFingerprint,
    )).encode("utf-8")).hexdigest()[:16]


def _CompactClaimsFingerprint(Claims: RoutingResourceClaims) -> str:
    Hasher = sha256()
    Hasher.update(b"compact-physical-claims-v2")
    for Category, Values in (
        (b"w", Claims.WireCells),
        (b"s", Claims.SupportCells),
        (b"a", Claims.RequiredAirCells),
        (b"e", Claims.ElectricalCells),
    ):
        Hasher.update(Category)
        Ordered = tuple(sorted(Values))
        Hasher.update(pack(">Q", len(Ordered)))
        for X, Y, Z in Ordered:
            Hasher.update(pack(">qqq", X, Y, Z))
    return Hasher.hexdigest()[:16]


def BuildCompactFactorCatalog(
    Sources: Iterable[CompactFactorMemberSource],
    *,
    MaximumAssignmentExpansions: int,
) -> CompactFactorCatalog:
    """Intern exact access, portal and guide primitives for every member."""
    OrderedSources = tuple(sorted(
        Sources,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ))
    if not OrderedSources:
        raise ValueError("compact factor catalog requires declared members")
    if MaximumAssignmentExpansions < 1:
        raise ValueError("compact factor catalog requires a positive work cap")
    PrimitiveByFingerprint: dict[str, CompactPhysicalClaimPrimitive] = {}
    PrimitiveIdByExactInput: dict[
        tuple[
            str,
            tuple[tuple[str, object], ...],
            RoutingResourceClaims,
            tuple[tuple[int, int, int], ...],
        ],
        str,
    ] = {}
    ClaimFingerprintByClaims: dict[RoutingResourceClaims, str] = {}
    PrimitiveIdByLiveIdentity: dict[tuple[object, ...], str] = {}
    PhysicalInputByFingerprint: dict[
        str,
        tuple[
            str,
            tuple[tuple[str, object], ...],
            RoutingResourceClaims,
            tuple[tuple[int, int, int], ...],
        ],
    ] = {}
    Members: list[CompactFactorMember] = []
    ResourcePositions: set[tuple[int, int, int]] = set()
    CacheHits = 0
    CacheMisses = 0
    ExpandedReferences = 0

    def ClaimsFingerprintCached(Claims: RoutingResourceClaims) -> str:
        Fingerprint = ClaimFingerprintByClaims.get(Claims, "")
        if not Fingerprint:
            Fingerprint = _CompactClaimsFingerprint(Claims)
            ClaimFingerprintByClaims[Claims] = Fingerprint
        return Fingerprint

    def InternPrimitive(
        Kind: str,
        Claims: RoutingResourceClaims,
        Identity: Mapping[str, object],
        ClaimsFingerprintOverride: str = "",
        DeferredGuideWireCells: tuple[
            tuple[int, int, int], ...
        ] = (),
        LiveIdentity: tuple[object, ...] = (),
        IdentityFingerprintOverride: str = "",
    ) -> str:
        nonlocal CacheHits, CacheMisses, ExpandedReferences
        IdentityItems = tuple(sorted(Identity.items()))
        DeferredGuideWireCells = tuple(sorted(set(
            DeferredGuideWireCells
        )))
        ClaimsFingerprint = str(ClaimsFingerprintOverride)
        if IdentityFingerprintOverride:
            if not ClaimsFingerprint:
                ClaimsFingerprint = ClaimFingerprintByClaims.get(Claims, "")
            if not ClaimsFingerprint:
                ClaimsFingerprint = _CompactClaimsFingerprint(Claims)
                ClaimFingerprintByClaims[Claims] = ClaimsFingerprint
            Fingerprint = _CompactPrimitiveFingerprint(
                Kind,
                (("Identity", str(IdentityFingerprintOverride)),),
                ClaimsFingerprint,
            )
            Existing = PrimitiveByFingerprint.get(Fingerprint)
            ExactPhysicalInput = (
                Kind,
                IdentityItems,
                Claims,
                DeferredGuideWireCells,
            )
            if Existing is not None:
                ExistingPhysicalInput = PhysicalInputByFingerprint.get(
                    Fingerprint
                )
                if ExistingPhysicalInput != ExactPhysicalInput:
                    raise ValueError(
                        "compact primitive fingerprint collision for "
                        f"{Kind} with identity {IdentityItems!r}; "
                        "the complete physical input differs from an "
                        "existing primitive"
                    )
                if LiveIdentity:
                    PrimitiveIdByLiveIdentity[LiveIdentity] = (
                        Existing.PrimitiveId
                    )
                CacheHits += 1
                ExpandedReferences += 1
                return Existing.PrimitiveId
            Primitive = CompactPhysicalClaimPrimitive(
                PrimitiveId=Fingerprint,
                PhysicalFingerprint=Fingerprint,
                Kind=Kind,
                Claims=Claims,
                DeferredGuideWireCells=DeferredGuideWireCells,
            )
            PrimitiveByFingerprint[Fingerprint] = Primitive
            PhysicalInputByFingerprint[Fingerprint] = ExactPhysicalInput
            if LiveIdentity:
                PrimitiveIdByLiveIdentity[LiveIdentity] = (
                    Primitive.PrimitiveId
                )
            CacheMisses += 1
            ExpandedReferences += 1
            for Cells in (
                Claims.WireCells,
                Claims.SupportCells,
                Claims.RequiredAirCells,
                Claims.ElectricalCells,
            ):
                ResourcePositions.update(Cells)
            return Primitive.PrimitiveId
        ExactInput = (
            Kind,
            IdentityItems,
            Claims,
            DeferredGuideWireCells,
        )
        ExistingId = PrimitiveIdByExactInput.get(ExactInput)
        if ExistingId is not None:
            if LiveIdentity:
                PrimitiveIdByLiveIdentity[LiveIdentity] = ExistingId
            CacheHits += 1
            ExpandedReferences += 1
            return ExistingId
        if not ClaimsFingerprint:
            ClaimsFingerprint = ClaimFingerprintByClaims.get(Claims, "")
        if not ClaimsFingerprint:
            ClaimsFingerprint = _CompactClaimsFingerprint(Claims)
            ClaimFingerprintByClaims[Claims] = ClaimsFingerprint
        Fingerprint = _CompactPrimitiveFingerprint(
            Kind,
            IdentityItems,
            ClaimsFingerprint,
        )
        Existing = PrimitiveByFingerprint.get(Fingerprint)
        if Existing is not None:
            if PhysicalInputByFingerprint.get(Fingerprint) != (
                Kind,
                IdentityItems,
                Claims,
                DeferredGuideWireCells,
            ):
                raise ValueError(
                    "compact primitive fingerprint collision for "
                    f"{Kind} with identity {IdentityItems!r}"
                )
            CacheHits += 1
            ExpandedReferences += 1
            PrimitiveIdByExactInput[ExactInput] = Existing.PrimitiveId
            if LiveIdentity:
                PrimitiveIdByLiveIdentity[LiveIdentity] = (
                    Existing.PrimitiveId
                )
            return Existing.PrimitiveId
        Primitive = CompactPhysicalClaimPrimitive(
            PrimitiveId=Fingerprint,
            PhysicalFingerprint=Fingerprint,
            Kind=Kind,
            Claims=Claims,
            DeferredGuideWireCells=DeferredGuideWireCells,
        )
        PrimitiveByFingerprint[Fingerprint] = Primitive
        PhysicalInputByFingerprint[Fingerprint] = (
            Kind,
            IdentityItems,
            Claims,
            DeferredGuideWireCells,
        )
        PrimitiveIdByExactInput[ExactInput] = Primitive.PrimitiveId
        if LiveIdentity:
            PrimitiveIdByLiveIdentity[LiveIdentity] = Primitive.PrimitiveId
        CacheMisses += 1
        ExpandedReferences += 1
        for Cells in (
            Claims.WireCells,
            Claims.SupportCells,
            Claims.RequiredAirCells,
            Claims.ElectricalCells,
        ):
            ResourcePositions.update(Cells)
        return Primitive.PrimitiveId

    for Source in OrderedSources:
        Domain = Source.GuideDomain
        Requirements = tuple(sorted(set(Source.ContractRequirements)))
        Values: list[CompactFactorValue] = []
        RequiredVariables = {
            str(Signal) for Signal, _Count in Domain.CandidateCounts
        }
        for Value in Domain.Values:
            Descriptor = Value.RouteGuideFactorDescriptor
            PrimitiveIds: list[str] = []
            if Descriptor is not None:
                PortalClaims = (
                    Value.CompactPortalClaims or Value.Claims
                )
                DeferredPortalClaims = RoutingResourceClaims(
                    RequiredAirCells=PortalClaims.RequiredAirCells,
                )
                SpineClaims = (
                    Value.CompactGuideSpineClaims
                    or RoutingResourceClaims()
                )
                PrimitiveIds.append(InternPrimitive(
                    "guide-portal",
                    DeferredPortalClaims,
                    {
                        "ResourceGraph": Domain.ResourceGraphFingerprint,
                        "Fabric": Source.FabricFingerprint,
                        "Layer": int(Descriptor.Layer),
                        "RoutingY": int(Descriptor.RoutingY),
                        "PortalPaths": tuple(
                            tuple(Portal.Path)
                            for Portal in (
                                Descriptor.SourcePortal,
                                *Descriptor.TargetPortals,
                            )
                        ),
                        "WireCells": tuple(sorted(
                            PortalClaims.WireCells
                        )),
                    },
                    DeferredGuideWireCells=tuple(
                        PortalClaims.WireCells
                    ),
                ))
                PrimitiveIds.append(InternPrimitive(
                    "guide-spine",
                    SpineClaims,
                    {
                        "ResourceGraph": Domain.ResourceGraphFingerprint,
                        "Layer": int(Descriptor.Layer),
                        "RoutingY": int(Descriptor.RoutingY),
                        "Guide": tuple(sorted(Descriptor.Guide)),
                    },
                    Value.CompactGuideSpineClaimsFingerprint,
                    DeferredGuideWireCells=(
                        tuple(
                            (int(X), int(Descriptor.RoutingY), int(Z))
                            for X, Z in Descriptor.Guide
                        )
                        if Value.CompactGuideSpineClaims is None
                        else ()
                    ),
                    LiveIdentity=(
                        "guide-spine-live-v1",
                        Domain.ResourceGraphFingerprint,
                        int(Descriptor.Layer),
                        int(Descriptor.RoutingY),
                        id(Descriptor),
                        Value.CompactGuideSpineClaimsFingerprint,
                    ),
                    IdentityFingerprintOverride=(
                        f"spine:{int(Descriptor.Layer)}:"
                        f"{Value.CompactGuideSpineClaimsFingerprint}"
                    ),
                ))
                JunctionClaims = (
                    Value.CompactJunctionClaims
                    or RoutingResourceClaims()
                )
                if JunctionClaims.ResourceIds:
                    PrimitiveIds.append(InternPrimitive(
                        "guide-junction",
                        JunctionClaims,
                        {
                            "ResourceGraph": Domain.ResourceGraphFingerprint,
                            "Layer": int(Descriptor.Layer),
                            "RoutingY": int(Descriptor.RoutingY),
                            "SourceFactorId": (
                                Value.SourceCandidateId
                                or Value.CandidateId
                            ),
                        },
                    ))
            elif Value.Claims.ResourceIds:
                PrimitiveIds.append(InternPrimitive(
                    "local-claim",
                    Value.Claims,
                    {
                        "ResourceGraph": Domain.ResourceGraphFingerprint,
                        "SourceFactorId": (
                            Value.SourceCandidateId or Value.CandidateId
                        ),
                    },
                ))
            Values.append(CompactFactorValue(
                Variable=Value.Signal,
                OwnerSignal=Value.OwnerSignal or Value.Signal,
                FactorId=Value.CandidateId,
                SourceFactorId=(
                    Value.SourceCandidateId or Value.CandidateId
                ),
                ValueKind=Value.ValueKind,
                PrimitiveIds=tuple(sorted(set(PrimitiveIds))),
                Objective=(
                    int(Value.MaterialCost),
                    int(Value.FootprintGrowth),
                    int(Value.Length),
                    int(Value.BendCount),
                    int(Value.ViaCount),
                ),
                ContractRequirements=tuple(sorted({
                    *Value.ContractRequirementItems,
                    *Requirements,
                })),
                RouteGuideFactorDescriptor=Descriptor,
                MaterializabilityCertificate=(
                    Value.CompactMaterializabilityCertificate
                ),
            ))
        FabricDomains = tuple(getattr(Source.Fabric, "TerminalDomains", ()))
        FabricComplete = bool(getattr(Source.Fabric, "Complete", False)) and all(
            bool(getattr(Value, "Complete", False))
            for Value in FabricDomains
        )
        for DomainIndex, FabricDomain in enumerate(FabricDomains):
            LogicalKey = str(getattr(
                FabricDomain,
                "LogicalKey",
                "",
            ) or f"{DomainIndex}:{FabricDomain.Signal}")
            Variable = f"__access_terminal__:{LogicalKey}"
            RequiredVariables.add(Variable)
            for StubIndex, Stub in enumerate(FabricDomain.EscapeStubs):
                StubClaims = Stub.PhysicalClaims
                PrimitiveId = InternPrimitive(
                    "access-stub",
                    RoutingResourceClaims(
                        RequiredAirCells=StubClaims.RequiredAirCells,
                    ),
                    {
                        "ResourceGraph": Domain.ResourceGraphFingerprint,
                        "Fabric": Source.FabricFingerprint,
                        "Shell": str(getattr(
                            Source.Fabric,
                            "AccessRingFingerprint",
                            "",
                        )),
                        "LogicalKey": LogicalKey,
                        "Path": tuple(Stub.Path),
                        "WireCells": tuple(sorted(
                            StubClaims.WireCells
                        )),
                    },
                    ClaimsFingerprintCached(StubClaims),
                    DeferredGuideWireCells=tuple(StubClaims.WireCells),
                    LiveIdentity=(
                        "access-stub-live-v1",
                        Domain.ResourceGraphFingerprint,
                        Source.FabricFingerprint,
                        str(getattr(
                            Source.Fabric,
                            "AccessRingFingerprint",
                            "",
                        )),
                        LogicalKey,
                        id(Stub),
                        id(Stub.PhysicalClaims),
                    ),
                )
                FactorId = f"stub:{DomainIndex}:{StubIndex}"
                Claims = StubClaims
                Values.append(CompactFactorValue(
                    Variable=Variable,
                    OwnerSignal=str(FabricDomain.Signal),
                    FactorId=FactorId,
                    SourceFactorId=FactorId,
                    ValueKind="contract-claim",
                    PrimitiveIds=(PrimitiveId,),
                    Objective=(
                        len(Claims.WireCells),
                        len({
                            (X, Z)
                            for X, _Y, Z in (
                                *Claims.WireCells,
                                *Claims.SupportCells,
                            )
                        }),
                        len(Stub.Path),
                        0,
                        max(0, len({Node[1] for Node in Stub.Path}) - 1),
                    ),
                    ContractRequirements=tuple(sorted({
                        *Requirements,
                        (
                            PlacementAccessStubContractRequirementName(
                                LogicalKey
                            ),
                            str(StubIndex),
                        ),
                    })),
                ))
        Complete = bool(Domain.Complete and FabricComplete)
        Members.append(CompactFactorMember(
            TemplateId=Source.TemplateId,
            Objective=Source.Objective,
            ContractRequirements=Requirements,
            RequiredVariables=tuple(sorted(RequiredVariables)),
            Values=tuple(sorted(Values, key=lambda Value: (
                Value.Variable,
                Value.Objective,
                Value.FactorId,
            ))),
            PlacementFingerprint=Domain.PlacementFingerprint,
            ResourceGraphFingerprint=Domain.ResourceGraphFingerprint,
            FabricFingerprint=Source.FabricFingerprint,
            CandidateDomainFingerprint=Domain.CandidateDomainFingerprint,
            LocalClaimDomainFingerprint=Domain.LocalClaimDomainFingerprint,
            PortalDomainFingerprint=Domain.PortalDomainFingerprint,
            MaximumAssignmentExpansions=MaximumAssignmentExpansions,
            Complete=Complete,
            IncompleteReason=(
                Domain.IncompleteReason
                if not Domain.Complete
                else str(getattr(Source.Fabric, "IncompleteReason", ""))
                or "incomplete-access-stub-domain"
                if not FabricComplete
                else ""
            ),
            GuideDomainDiagnostics=Domain.Diagnostics,
        ))
    return CompactFactorCatalog(
        ResourcePositions=tuple(sorted(ResourcePositions)),
        Primitives=tuple(sorted(
            PrimitiveByFingerprint.values(),
            key=lambda Value: Value.PrimitiveId,
        )),
        Members=tuple(Members),
        PrimitiveCacheHits=CacheHits,
        PrimitiveCacheMisses=CacheMisses,
        ExpandedPrimitiveReferenceCount=ExpandedReferences,
        MaximumAssignmentExpansions=MaximumAssignmentExpansions,
    )


def _MaterializeCompactPrimitiveClaims(
    Primitive: CompactPhysicalClaimPrimitive,
) -> RoutingResourceClaims:
    """Expand one deferred guide only at oracle or selected-world handoff."""
    if not Primitive.DeferredGuideWireCells:
        return Primitive.Claims
    WireCells = frozenset(Primitive.DeferredGuideWireCells)
    return RoutingResourceClaims(
        WireCells=WireCells,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in WireCells
        ),
        RequiredAirCells=Primitive.Claims.RequiredAirCells,
        ElectricalCells=frozenset(
            WireCells
            | {
                Position
                for X, Y, Z in WireCells
                for Position in (
                    (X + 1, Y, Z),
                    (X - 1, Y, Z),
                    (X, Y, Z + 1),
                    (X, Y, Z - 1),
                    (X + 1, Y + 1, Z),
                    (X + 1, Y - 1, Z),
                    (X - 1, Y + 1, Z),
                    (X - 1, Y - 1, Z),
                    (X, Y + 1, Z + 1),
                    (X, Y - 1, Z + 1),
                    (X, Y + 1, Z - 1),
                    (X, Y - 1, Z - 1),
                )
            }
        ),
    )


@dataclass(frozen=True)
class CompactFactorCatalogOracleResult:
    """Small-fixture reference result; never used by production routing."""

    Success: bool
    Complete: bool
    Unsatisfiable: bool
    IncompleteReason: str
    SelectedTemplateId: str = ""
    SelectedCandidateIds: tuple[tuple[str, str], ...] = ()
    AttemptedTemplateIds: tuple[str, ...] = ()
    ExpansionCount: int = 0


def SolveCompactFactorCatalogPythonOracleForTests(
    Catalog: CompactFactorCatalog,
    *,
    MaximumAssignmentExpansions: int | None = None,
    Deadline: RoutingDeadline | None = None,
) -> CompactFactorCatalogOracleResult:
    """Brute-force the exact catalog for parity fixtures only.

    This deliberately has no call site in the compiler flow.  It provides a
    readable capacity and named-requirement oracle for tiny Rust parity tests
    without reintroducing the expanded compact-domain production fallback.
    """
    WorkLimit = (
        Catalog.MaximumAssignmentExpansions
        if MaximumAssignmentExpansions is None
        else int(MaximumAssignmentExpansions)
    )
    if WorkLimit < 1:
        raise ValueError("compact oracle requires a positive work cap")
    PrimitiveById = {
        Value.PrimitiveId: Value for Value in Catalog.Primitives
    }
    ClaimsByIdentity: dict[
        tuple[str, str, tuple[str, ...]], RoutingResourceClaims
    ] = {}

    def Claims(Value: CompactFactorValue) -> RoutingResourceClaims:
        Identity = (Value.Variable, Value.FactorId, Value.PrimitiveIds)
        Existing = ClaimsByIdentity.get(Identity)
        if Existing is None:
            Existing = MergeRoutingResourceClaims(
                _MaterializeCompactPrimitiveClaims(
                    PrimitiveById[PrimitiveId]
                )
                for PrimitiveId in Value.PrimitiveIds
            )
            ClaimsByIdentity[Identity] = Existing
        return Existing

    ExpansionCount = 0
    Attempted: list[str] = []
    for Member in sorted(
        Catalog.Members,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ):
        Attempted.append(Member.TemplateId)
        if (
            Deadline is not None
            and Deadline.RemainingMilliseconds() < 1
        ):
            return CompactFactorCatalogOracleResult(
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="assignment-deadline",
                AttemptedTemplateIds=tuple(Attempted),
                ExpansionCount=ExpansionCount,
            )
        ValuesByVariable: dict[str, tuple[CompactFactorValue, ...]] = {}
        for Variable in Member.RequiredVariables:
            ValuesByVariable[Variable] = tuple(
                Value for Value in Member.Values
                if Value.Variable == Variable
            )
        if any(not Values for Values in ValuesByVariable.values()):
            continue
        Selected: list[CompactFactorValue] = []
        Requirements: dict[str, str] = {}
        WorkExhausted = False
        DeadlineExceeded = False

        def Visit(VariableIndex: int) -> bool:
            nonlocal ExpansionCount, WorkExhausted, DeadlineExceeded
            if VariableIndex == len(Member.RequiredVariables):
                return True
            if (
                Deadline is not None
                and Deadline.RemainingMilliseconds() < 1
            ):
                DeadlineExceeded = True
                return False
            Variable = Member.RequiredVariables[VariableIndex]
            for Candidate in ValuesByVariable[Variable]:
                if ExpansionCount >= WorkLimit:
                    WorkExhausted = True
                    return False
                ExpansionCount += 1
                CandidateRequirements = dict(
                    Candidate.ContractRequirements
                )
                if any(
                    Name in Requirements
                    and Requirements[Name] != Value
                    for Name, Value in CandidateRequirements.items()
                ):
                    continue
                CandidateClaims = Claims(Candidate)
                if any(
                    Candidate.OwnerSignal != Existing.OwnerSignal
                    and FindClaimConflicts({
                        "candidate": CandidateClaims,
                        "existing": Claims(Existing),
                    })
                    for Existing in Selected
                ):
                    continue
                PreviousRequirements = dict(Requirements)
                Requirements.update(CandidateRequirements)
                Selected.append(Candidate)
                if Visit(VariableIndex + 1):
                    return True
                Selected.pop()
                Requirements.clear()
                Requirements.update(PreviousRequirements)
                if WorkExhausted or DeadlineExceeded:
                    return False
            return False

        if Visit(0):
            return CompactFactorCatalogOracleResult(
                Success=True,
                Complete=True,
                Unsatisfiable=False,
                IncompleteReason="",
                SelectedTemplateId=Member.TemplateId,
                SelectedCandidateIds=tuple(sorted(
                    (Value.Variable, Value.FactorId)
                    for Value in Selected
                )),
                AttemptedTemplateIds=tuple(Attempted),
                ExpansionCount=ExpansionCount,
            )
        if DeadlineExceeded or WorkExhausted:
            return CompactFactorCatalogOracleResult(
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason=(
                    "assignment-deadline"
                    if DeadlineExceeded else "assignment-work-cap"
                ),
                AttemptedTemplateIds=tuple(Attempted),
                ExpansionCount=ExpansionCount,
            )
    Unsatisfiable = not Catalog.NonExhaustiveTemplateDomain
    return CompactFactorCatalogOracleResult(
        Success=False,
        Complete=Unsatisfiable,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=(
            "complete-capacity-core"
            if Unsatisfiable else "non-exhaustive-template-domain"
        ),
        AttemptedTemplateIds=tuple(Attempted),
        ExpansionCount=ExpansionCount,
    )


def _BuildCompactCatalogNativePayload(
    Catalog: CompactFactorCatalog,
) -> tuple[
    list[tuple[object, ...]],
    list[tuple[object, ...]],
    list[tuple[object, ...]],
]:
    PrimitiveIndex = {
        Primitive.PrimitiveId: Index
        for Index, Primitive in enumerate(Catalog.Primitives)
    }
    PrimitivePayload = [
        (
            list(Primitive.Claims.WireCells),
            list(Primitive.Claims.SupportCells),
            list(Primitive.Claims.RequiredAirCells),
            list(Primitive.Claims.ElectricalCells),
            list(Primitive.DeferredGuideWireCells),
        )
        for Primitive in Catalog.Primitives
    ]
    FactorPayload: list[tuple[object, ...]] = []
    FactorIndexByPayload: dict[tuple[object, ...], int] = {}
    MemberPayload: list[tuple[object, ...]] = []
    for Member in Catalog.Members:
        MemberRequirements = frozenset(Member.ContractRequirements)
        FactorIndexes: list[int] = []
        for Value in Member.Values:
            FactorRequirements = tuple(
                Requirement
                for Requirement in Value.ContractRequirements
                if Requirement not in MemberRequirements
            )
            Payload = (
                Value.Variable,
                Value.FactorId,
                tuple(
                    PrimitiveIndex[PrimitiveId]
                    for PrimitiveId in Value.PrimitiveIds
                ),
                *Value.Objective,
                ";".join(
                    f"{Name}={RequirementValue}"
                    for Name, RequirementValue in FactorRequirements
                ),
                Value.OwnerSignal,
            )
            FactorIndex = FactorIndexByPayload.get(Payload)
            if FactorIndex is None:
                FactorIndex = len(FactorPayload)
                FactorIndexByPayload[Payload] = FactorIndex
                FactorPayload.append(Payload)
            FactorIndexes.append(FactorIndex)
        MemberPayload.append((
            Member.TemplateId,
            list(Member.Objective),
            list(Member.RequiredVariables),
            FactorIndexes,
        ))
    return PrimitivePayload, FactorPayload, MemberPayload


def MaterializeSelectedCompactFactorDomain(
    Catalog: CompactFactorCatalog,
    TemplateId: str,
) -> RawTrackAssignmentDomain:
    """Expand factor references only for the selected physical member."""
    MemberById = {Value.TemplateId: Value for Value in Catalog.Members}
    Member = MemberById.get(str(TemplateId))
    if Member is None:
        raise ValueError("selected compact member is outside the catalog")
    PrimitiveById = {
        Value.PrimitiveId: Value for Value in Catalog.Primitives
    }

    Values = tuple(
        RawTrackAssignmentValue(
            Signal=Value.Variable,
            OwnerSignal=Value.OwnerSignal,
            CandidateId=Value.FactorId,
            SourceCandidateId=Value.SourceFactorId,
            Claims=MergeRoutingResourceClaims(
                _MaterializeCompactPrimitiveClaims(
                    PrimitiveById[PrimitiveId]
                )
                for PrimitiveId in Value.PrimitiveIds
            ),
            MaterialCost=Value.Objective[0],
            FootprintGrowth=Value.Objective[1],
            Length=Value.Objective[2],
            BendCount=Value.Objective[3],
            ViaCount=Value.Objective[4],
            ValueKind=Value.ValueKind,
            ContractRequirements=Value.ContractRequirements,
            RouteGuideFactorDescriptor=(
                Value.RouteGuideFactorDescriptor
            ),
            CompactMaterializabilityCertificate=(
                Value.MaterializabilityCertificate
            ),
        )
        for Value in Member.Values
    )
    ResourcePositions = tuple(sorted({
        Position
        for Value in Values
        for Cells in (
            Value.Claims.WireCells,
            Value.Claims.SupportCells,
            Value.Claims.RequiredAirCells,
            Value.Claims.ElectricalCells,
        )
        for Position in Cells
    }))
    CandidateCounts = tuple(
        (
            Variable,
            sum(Value.Signal == Variable for Value in Values),
        )
        for Variable in Member.RequiredVariables
    )
    return RawTrackAssignmentDomain(
        ResourcePositions=ResourcePositions,
        Values=Values,
        BaseClaims=(),
        CandidateCounts=CandidateCounts,
        CandidateDomainFingerprint=Member.CandidateDomainFingerprint,
        LocalClaimDomainFingerprint=Member.LocalClaimDomainFingerprint,
        PlacementFingerprint=Member.PlacementFingerprint,
        ResourceGraphFingerprint=Member.ResourceGraphFingerprint,
        PortalDomainFingerprint=Member.PortalDomainFingerprint,
        Complete=Member.Complete,
        IncompleteReason=Member.IncompleteReason,
        MaximumAssignmentExpansions=Member.MaximumAssignmentExpansions,
        Diagnostics=(
            ("CompactFactorCatalog", True),
            ("CompactFactorCatalogFingerprint", Catalog.CatalogFingerprint),
            ("CompactFactorCatalogSummary", Catalog.ToDictionary()),
            ("SelectedCompactMember", Member.ToDictionary()),
            *Member.GuideDomainDiagnostics,
        ),
        DomainFingerprintOverride=BuildStableFingerprint({
            "Kind": "selected-compact-factor-domain-v1",
            "Catalog": Catalog.CatalogFingerprint,
            "Member": Member.TemplateId,
            "CandidateDomain": Member.CandidateDomainFingerprint,
            "Factors": tuple(
                (Value.Variable, Value.FactorId)
                for Value in Member.Values
            ),
        }),
    )


def SolveCompactFactorCatalogWithContext(
    Catalog: CompactFactorCatalog,
    *,
    Deadline: RoutingDeadline,
) -> RawTrackAssignmentSelection:
    """Invoke the production compact binding exactly once and freeze its witness."""
    if _SolveCompactTemplateFactorCatalogBounded is None:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceSolveIncomplete,
            Stage="PreRouteCompactCatalogUnavailable",
            Detail=(
                "the required native compact factor catalog binding is "
                "unavailable"
            ),
            RepairActions=(),
            Diagnostics={
                "CompactFactorCatalog": Catalog.ToDictionary(),
                "FallbackInvoked": False,
            },
        ))
    IncompleteMember = next((
        Value for Value in Catalog.Members if not Value.Complete
    ), None)
    if IncompleteMember is not None:
        return RawTrackAssignmentSelection(
            ProblemFingerprint=Catalog.CatalogFingerprint,
            SelectionFingerprint="",
            SelectedTemplateId="",
            SelectedObjective=(),
            Preparation=None,
            Attempts=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            Unsatisfiable=False,
            IncompleteReason=(
                f"incomplete-catalog-member:{IncompleteMember.TemplateId}:"
                f"{IncompleteMember.IncompleteReason}"
            ),
            MaterializedTemplateCount=len(Catalog.Members),
        )
    RemainingMilliseconds = Deadline.RemainingMilliseconds()
    if RemainingMilliseconds < 1:
        return RawTrackAssignmentSelection(
            ProblemFingerprint=Catalog.CatalogFingerprint,
            SelectionFingerprint="",
            SelectedTemplateId="",
            SelectedObjective=(),
            Preparation=None,
            Attempts=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            Unsatisfiable=False,
            IncompleteReason="assignment-deadline",
            MaterializedTemplateCount=len(Catalog.Members),
        )
    (
        PrimitivePayload,
        FactorPayload,
        MemberPayload,
    ) = _BuildCompactCatalogNativePayload(Catalog)
    # Payload encoding is part of the same absolute pre-route budget. Do not
    # hand Rust the stale pre-encoding remainder and accidentally extend the
    # solve beyond the compiler deadline.
    RemainingMilliseconds = Deadline.RemainingMilliseconds()
    if RemainingMilliseconds < 1:
        return RawTrackAssignmentSelection(
            ProblemFingerprint=Catalog.CatalogFingerprint,
            SelectionFingerprint="",
            SelectedTemplateId="",
            SelectedObjective=(),
            Preparation=None,
            Attempts=(),
            ExpansionCount=0,
            Success=False,
            Complete=False,
            Unsatisfiable=False,
            IncompleteReason="assignment-deadline",
            MaterializedTemplateCount=len(Catalog.Members),
        )
    NativeResult = _SolveCompactTemplateFactorCatalogBounded(
        list(Catalog.ResourcePositions),
        PrimitivePayload,
        FactorPayload,
        MemberPayload,
        Catalog.MaximumAssignmentExpansions,
        RemainingMilliseconds,
        Catalog.NonExhaustiveTemplateDomain,
    )
    MemberById = {Value.TemplateId: Value for Value in Catalog.Members}
    AttemptedIds = tuple(map(str, getattr(
        NativeResult,
        "AttemptedTemplateIds",
        (),
    )))
    SelectedTemplateId = str(getattr(
        NativeResult,
        "SelectedTemplateId",
        "",
    ) or "")
    Success = bool(getattr(NativeResult, "Success", False))
    Complete = bool(getattr(NativeResult, "Complete", False))
    SelectedMember = MemberById.get(SelectedTemplateId)
    if Success and (not Complete or SelectedMember is None):
        raise RuntimeError("native compact catalog returned an invalid witness")
    Preparation = None
    if Success and SelectedMember is not None:
        SelectedDomain = MaterializeSelectedCompactFactorDomain(
            Catalog,
            SelectedTemplateId,
        )
        Preparation = BuildTrackAssignmentPreparationFromRawDomain(
            SelectedDomain,
            NativeResult,
        )
        if not Preparation.Success or not Preparation.Complete:
            raise RuntimeError(
                "native compact catalog did not produce a complete handoff"
            )
    ExpansionCount = max(0, int(getattr(
        NativeResult,
        "ExpansionCount",
        0,
    )))
    ConflictSignals = tuple(sorted(map(str, getattr(
        NativeResult,
        "ConflictSignals",
        (),
    ))))
    ConflictResourceIndices = tuple(sorted(map(int, getattr(
        NativeResult,
        "ConflictResourceIndices",
        (),
    ))))
    AttemptPairwise = {
        str(MemberId): tuple(sorted(tuple(map(str, Pair)) for Pair in Pairs))
        for MemberId, Pairs in getattr(
            NativeResult,
            "AttemptPairwiseIncompatibleSignals",
            (),
        )
    }
    AttemptFailureNets = {
        str(MemberId): (str(FailureNet) if FailureNet is not None else "")
        for MemberId, FailureNet in getattr(
            NativeResult,
            "AttemptFailureNets",
            (),
        )
    }
    AttemptExpansionCounts = {
        str(MemberId): max(0, int(Count))
        for MemberId, Count in getattr(
            NativeResult,
            "AttemptExpansionCounts",
            (),
        )
    }
    AttemptPartialCandidateIds = {
        str(MemberId): tuple(
            (str(Variable), str(CandidateId))
            for Variable, CandidateId in CandidateIds
        )
        for MemberId, CandidateIds in getattr(
            NativeResult,
            "AttemptPartialCandidateIds",
            (),
        )
    }
    PrimitiveById = {
        Value.PrimitiveId: Value for Value in Catalog.Primitives
    }

    def BuildFailureCandidateRejections(
        TemplateId: str,
    ) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        Member = MemberById[TemplateId]
        FailureVariable = AttemptFailureNets.get(TemplateId, "")
        if not FailureVariable:
            return ()
        ValueByIdentity = {
            (Value.Variable, Value.FactorId): Value
            for Value in Member.Values
        }
        Partial = tuple(
            ValueByIdentity[Identity]
            for Identity in AttemptPartialCandidateIds.get(TemplateId, ())
            if Identity in ValueByIdentity
        )
        ClaimCache: dict[tuple[str, str], RoutingResourceClaims] = {}

        def Claims(Value: CompactFactorValue) -> RoutingResourceClaims:
            Identity = (Value.Variable, Value.FactorId)
            Existing = ClaimCache.get(Identity)
            if Existing is None:
                Existing = MergeRoutingResourceClaims(
                    PrimitiveById[PrimitiveId].Claims
                    for PrimitiveId in Value.PrimitiveIds
                )
                ClaimCache[Identity] = Existing
            return Existing

        def PhysicalConflictReason(
            First: CompactFactorValue,
            Second: CompactFactorValue,
        ) -> str:
            Kinds = tuple(sorted({
                f"{PrimitiveById[FirstPrimitiveId].Kind}/"
                f"{PrimitiveById[SecondPrimitiveId].Kind}"
                for FirstPrimitiveId in First.PrimitiveIds
                for SecondPrimitiveId in Second.PrimitiveIds
                if FindClaimConflicts({
                    "first": PrimitiveById[FirstPrimitiveId].Claims,
                    "second": PrimitiveById[SecondPrimitiveId].Claims,
                })
            }))
            return "physical-claim:" + ",".join(Kinds)

        Result = []
        for Candidate in Member.Values:
            if Candidate.Variable != FailureVariable:
                continue
            CandidateRequirements = dict(Candidate.ContractRequirements)
            Rejections = []
            for SelectedValue in Partial:
                if SelectedValue.Variable == FailureVariable:
                    continue
                SelectedRequirements = dict(
                    SelectedValue.ContractRequirements
                )
                MismatchedNames = tuple(sorted(
                    Name
                    for Name in CandidateRequirements.keys()
                    & SelectedRequirements.keys()
                    if CandidateRequirements[Name]
                    != SelectedRequirements[Name]
                ))
                if MismatchedNames:
                    Rejections.append((
                        SelectedValue.Variable,
                        "contract:" + ",".join(MismatchedNames),
                    ))
                    continue
                if (
                    Candidate.OwnerSignal != SelectedValue.OwnerSignal
                    and FindClaimConflicts({
                        "candidate": Claims(Candidate),
                        "selected": Claims(SelectedValue),
                    })
                ):
                    Rejections.append((
                        SelectedValue.Variable,
                        PhysicalConflictReason(
                            Candidate,
                            SelectedValue,
                        ),
                    ))
            Result.append((
                Candidate.FactorId,
                tuple(sorted(Rejections)),
            ))
        return tuple(Result)

    def BuildPairwiseConflictReasons(
        TemplateId: str,
    ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        Member = MemberById[TemplateId]
        ValuesByVariable: dict[str, list[CompactFactorValue]] = {}
        for Value in Member.Values:
            ValuesByVariable.setdefault(Value.Variable, []).append(Value)
        Result = []
        for FirstVariable, SecondVariable in AttemptPairwise.get(
            TemplateId,
            (),
        ):
            ReasonCounts: dict[str, int] = {}
            CandidatePairCount = 0
            for First in ValuesByVariable.get(FirstVariable, ()):
                FirstRequirements = dict(First.ContractRequirements)
                for Second in ValuesByVariable.get(SecondVariable, ()):
                    CandidatePairCount += 1
                    PairReasons: set[str] = set()
                    SecondRequirements = dict(Second.ContractRequirements)
                    for Name in (
                        FirstRequirements.keys()
                        & SecondRequirements.keys()
                    ):
                        if FirstRequirements[Name] != SecondRequirements[Name]:
                            PairReasons.add(f"contract:{Name}")
                    if First.OwnerSignal == Second.OwnerSignal:
                        for Reason in PairReasons:
                            ReasonCounts[Reason] = (
                                ReasonCounts.get(Reason, 0) + 1
                            )
                        continue
                    for FirstPrimitiveId in First.PrimitiveIds:
                        FirstPrimitive = PrimitiveById[FirstPrimitiveId]
                        for SecondPrimitiveId in Second.PrimitiveIds:
                            SecondPrimitive = PrimitiveById[SecondPrimitiveId]
                            PrimitiveConflicts = FindClaimConflicts({
                                "first": FirstPrimitive.Claims,
                                "second": SecondPrimitive.Claims,
                            })
                            if PrimitiveConflicts:
                                PairReasons.add(
                                    f"physical:{FirstPrimitive.Kind}/"
                                    f"{SecondPrimitive.Kind}"
                                )
                    for Reason in PairReasons:
                        ReasonCounts[Reason] = (
                            ReasonCounts.get(Reason, 0) + 1
                        )
            Result.append((
                FirstVariable,
                SecondVariable,
                tuple((
                    f"{Reason}:{Count}/{CandidatePairCount}"
                    for Reason, Count in sorted(ReasonCounts.items())
                )),
            ))
        return tuple(Result)

    Attempts = tuple(
        RawTrackAssignmentAttempt(
            TemplateId=TemplateId,
            Objective=MemberById[TemplateId].Objective,
            Success=Success and TemplateId == SelectedTemplateId,
            Complete=(
                Complete or Index + 1 < len(AttemptedIds)
            ),
            ExpansionCount=(
                AttemptExpansionCounts.get(TemplateId, 0)
            ),
            CumulativeExpansionCount=(
                sum(
                    AttemptExpansionCounts.get(Value, 0)
                    for Value in AttemptedIds[:Index + 1]
                )
            ),
            FailureNet=AttemptFailureNets.get(TemplateId, ""),
            PartialCandidateIds=AttemptPartialCandidateIds.get(
                TemplateId,
                (),
            ),
            FailureCandidateRejections=(
                BuildFailureCandidateRejections(TemplateId)
            ),
            ConflictSignals=(
                ConflictSignals if Index == 0 and not Success else ()
            ),
            ConflictResourceIndices=(
                ConflictResourceIndices if Index == 0 and not Success else ()
            ),
            PairwiseIncompatibleSignals=AttemptPairwise.get(TemplateId, ()),
            PairwiseIncompatibleSignalReasons=(
                BuildPairwiseConflictReasons(TemplateId)
            ),
            IncompleteReason=(
                str(getattr(NativeResult, "IncompleteReason", ""))
                if Index + 1 == len(AttemptedIds) and not Complete
                else ""
            ),
        )
        for Index, TemplateId in enumerate(AttemptedIds)
        if TemplateId in MemberById
    )
    SelectionFingerprint = (
        BuildStableFingerprint({
            "Catalog": Catalog.CatalogFingerprint,
            "SelectedTemplateId": SelectedTemplateId,
            "Preparation": (
                Preparation.ToDictionary() if Preparation is not None else None
            ),
        })
        if Success and Preparation is not None
        else ""
    )
    return RawTrackAssignmentSelection(
        ProblemFingerprint=Catalog.CatalogFingerprint,
        SelectionFingerprint=SelectionFingerprint,
        SelectedTemplateId=SelectedTemplateId if Success else "",
        SelectedObjective=(
            SelectedMember.Objective
            if Success and SelectedMember is not None else ()
        ),
        Preparation=Preparation,
        Attempts=Attempts,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=bool(getattr(NativeResult, "Unsatisfiable", False)),
        IncompleteReason=str(getattr(NativeResult, "IncompleteReason", "")),
        FirstConflictSignals=ConflictSignals,
        FirstConflictResourceIndices=ConflictResourceIndices,
        FirstPairwiseIncompatibleSignals=tuple(sorted(
            tuple(map(str, Value))
            for Value in getattr(
                NativeResult,
                "PairwiseIncompatibleSignals",
                (),
            )
        )),
        MaterializedTemplateCount=len(Catalog.Members),
    )


@dataclass(frozen=True)
class RawTrackAssignmentTemplate:
    """One immutable, mutually exclusive authoritative assignment domain."""

    TemplateId: str
    Objective: tuple[int, ...]
    Domain: RawTrackAssignmentDomain

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("raw track-assignment template requires an id")
        if any(Value < 0 for Value in self.Objective):
            raise ValueError("raw track-assignment objective cannot be negative")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Domain": self.Domain.ToDictionary(),
        }


@dataclass(frozen=True)
class RawTrackAssignmentProblem:
    """Fixed finite placement/template capacity problem.

    ``MaximumAssignmentExpansions`` is global to the problem.  Every member
    must carry the same declared cap so adding a geometry member cannot turn
    the cap into an accidental per-template budget multiplier.
    """

    Templates: tuple[RawTrackAssignmentTemplate, ...]
    MaximumAssignmentExpansions: int
    NonExhaustiveTemplateDomain: bool = True

    def __post_init__(self) -> None:
        if self.MaximumAssignmentExpansions < 1:
            raise ValueError("raw template assignment requires a positive work cap")
        TemplateIds = tuple(Value.TemplateId for Value in self.Templates)
        if len(TemplateIds) != len(set(TemplateIds)):
            raise ValueError("raw template assignment repeats a template id")
        MismatchedCaps = tuple(
            Value.TemplateId
            for Value in self.Templates
            if Value.Domain.MaximumAssignmentExpansions
            != self.MaximumAssignmentExpansions
        )
        if MismatchedCaps:
            raise ValueError(
                "raw template assignment members must share one work cap: "
                + ", ".join(MismatchedCaps)
            )
        if not self.NonExhaustiveTemplateDomain:
            TruncatedTemplates = tuple(
                Value.TemplateId
                for Value in self.Templates
                if bool(dict(Value.Domain.Diagnostics).get(
                    "ExcludedConfiguredRequestCounts",
                    (),
                ))
            )
            if TruncatedTemplates:
                raise ValueError(
                    "a raw template with excluded configured request shapes "
                    "cannot be declared exhaustive: "
                    + ", ".join(TruncatedTemplates)
                )

    @property
    def ProblemFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "raw-template-track-assignment-v1",
            "Templates": [
                Value.ToDictionary()
                for Value in sorted(
                    self.Templates,
                    key=lambda Value: (Value.Objective, Value.TemplateId),
                )
            ],
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "TemplateCount": len(self.Templates),
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        }


@dataclass(frozen=True)
class PredeclaredRawTrackAssignmentMember:
    """A fixed raw-template member declared before materialization.

    ``Objective`` is an immutable selection prefix known before materializing
    the raw domain.  Most callers provide the complete objective.  A
    placement/access portfolio may instead provide its exact geometry/layer
    prefix, then report the remaining material/access terms with the typed
    materialization.  In that form, all descriptors sharing the prefix are
    materialized before the selector chooses their resolved full objective.
    """

    TemplateId: str
    Objective: tuple[int, ...]
    MaterializationInputFingerprint: str

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("predeclared raw member requires an id")
        if any(Value < 0 for Value in self.Objective):
            raise ValueError(
                "predeclared raw member objective cannot be negative"
            )
        if not self.MaterializationInputFingerprint:
            raise ValueError(
                "predeclared raw member requires an input fingerprint"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "MaterializationInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentMemberMaterialization:
    """Typed result of constructing one predeclared raw template domain."""

    TemplateId: str
    Domain: RawTrackAssignmentDomain | None
    Complete: bool
    IncompleteReason: str = ""
    Diagnostics: tuple[tuple[str, object], ...] = ()
    ResolvedObjective: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.TemplateId:
            raise ValueError("raw template materialization requires an id")
        if self.Complete != (self.Domain is not None and self.Domain.Complete):
            raise ValueError(
                "raw template materialization completeness must match its "
                "domain"
            )
        if not self.Complete and not self.IncompleteReason:
            raise ValueError(
                "incomplete raw template materialization requires a reason"
            )
        if any(Value < 0 for Value in self.ResolvedObjective):
            raise ValueError(
                "resolved raw template objective cannot be negative"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "Domain": (
                self.Domain.ToDictionary()
                if self.Domain is not None
                else None
            ),
            "Diagnostics": dict(self.Diagnostics),
            "ResolvedObjective": list(self.ResolvedObjective),
        }

    def ToCompactDictionary(self) -> dict[str, object]:
        """Summarize a compact member without expanding physical values."""
        Domain = self.Domain
        return {
            "TemplateId": self.TemplateId,
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "ResolvedObjective": list(self.ResolvedObjective),
            "Diagnostics": dict(self.Diagnostics),
            "Domain": (
                {
                    "ValueCount": len(Domain.Values),
                    "CandidateCounts": [
                        list(Value) for Value in Domain.CandidateCounts
                    ],
                    "CandidateDomainFingerprint": (
                        Domain.CandidateDomainFingerprint
                    ),
                    "LocalClaimDomainFingerprint": (
                        Domain.LocalClaimDomainFingerprint
                    ),
                    "PlacementFingerprint": Domain.PlacementFingerprint,
                    "ResourceGraphFingerprint": (
                        Domain.ResourceGraphFingerprint
                    ),
                    "PortalDomainFingerprint": (
                        Domain.PortalDomainFingerprint
                    ),
                    "Complete": Domain.Complete,
                    "IncompleteReason": Domain.IncompleteReason,
                    "Diagnostics": dict(Domain.Diagnostics),
                }
                if Domain is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentAttempt:
    """One member result inside the aggregate capacity proof."""

    TemplateId: str
    Objective: tuple[int, ...]
    Success: bool
    Complete: bool
    ExpansionCount: int
    CumulativeExpansionCount: int
    FailureNet: str = ""
    PartialCandidateIds: tuple[tuple[str, str], ...] = ()
    FailureCandidateRejections: tuple[
        tuple[str, tuple[tuple[str, str], ...]], ...
    ] = ()
    ConflictSignals: tuple[str, ...] = ()
    ConflictResourceIndices: tuple[int, ...] = ()
    PairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = ()
    PairwiseIncompatibleSignalReasons: tuple[
        tuple[str, str, tuple[str, ...]], ...
    ] = ()
    IncompleteReason: str = ""

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Success": self.Success,
            "Complete": self.Complete,
            "ExpansionCount": self.ExpansionCount,
            "CumulativeExpansionCount": self.CumulativeExpansionCount,
            "FailureNet": self.FailureNet,
            "PartialCandidateIds": [
                list(Value) for Value in self.PartialCandidateIds
            ],
            "FailureCandidateRejections": [
                [CandidateId, [list(Value) for Value in Rejections]]
                for CandidateId, Rejections
                in self.FailureCandidateRejections
            ],
            "ConflictSignals": list(self.ConflictSignals),
            "ConflictResourceIndices": list(self.ConflictResourceIndices),
            "PairwiseIncompatibleSignals": [
                list(Value) for Value in self.PairwiseIncompatibleSignals
            ],
            "PairwiseIncompatibleSignalReasons": [
                [First, Second, list(Reasons)]
                for First, Second, Reasons
                in self.PairwiseIncompatibleSignalReasons
            ],
            "IncompleteReason": self.IncompleteReason,
        }


@dataclass(frozen=True)
class RawTrackAssignmentSelection:
    """Typed terminal result for the one aggregate template selection."""

    ProblemFingerprint: str
    SelectionFingerprint: str
    SelectedTemplateId: str
    SelectedObjective: tuple[int, ...]
    Preparation: TrackAssignmentPreparation | None
    Attempts: tuple[RawTrackAssignmentAttempt, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    Unsatisfiable: bool
    IncompleteReason: str = ""
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()
    FirstPairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = ()
    MaterializedTemplateCount: int = 0
    SkippedDominatedTemplateCount: int = 0

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "SelectionFingerprint": self.SelectionFingerprint,
            "SelectedTemplateId": self.SelectedTemplateId,
            "SelectedObjective": list(self.SelectedObjective),
            "Preparation": (
                self.Preparation.ToDictionary()
                if self.Preparation is not None
                else None
            ),
            "Attempts": [Value.ToDictionary() for Value in self.Attempts],
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "Unsatisfiable": self.Unsatisfiable,
            "IncompleteReason": self.IncompleteReason,
            "FirstConflictSignals": list(self.FirstConflictSignals),
            "FirstConflictResourceIndices": list(
                self.FirstConflictResourceIndices
            ),
            "FirstPairwiseIncompatibleSignals": [
                list(Value)
                for Value in self.FirstPairwiseIncompatibleSignals
            ],
            "MaterializedTemplateCount": self.MaterializedTemplateCount,
            "SkippedDominatedTemplateCount": (
                self.SkippedDominatedTemplateCount
            ),
        }


NativeRawAssignmentSolver = Callable[[RawTrackAssignmentDomain, int], Any]
WorkCheck = Callable[[dict[str, object]], None]
def _BuildSelection(
    Problem: RawTrackAssignmentProblem,
    *,
    Attempts: Iterable[RawTrackAssignmentAttempt],
    ExpansionCount: int,
    Success: bool,
    Complete: bool,
    Unsatisfiable: bool,
    SelectedTemplate: RawTrackAssignmentTemplate | None = None,
    Preparation: TrackAssignmentPreparation | None = None,
    IncompleteReason: str = "",
    FirstConflictSignals: tuple[str, ...] = (),
    FirstConflictResourceIndices: tuple[int, ...] = (),
    FirstPairwiseIncompatibleSignals: tuple[tuple[str, str], ...] = (),
    MaterializedTemplateCount: int = 0,
    SkippedDominatedTemplateCount: int = 0,
) -> RawTrackAssignmentSelection:
    AttemptValues = tuple(Attempts)
    SelectionFingerprint = (
        BuildStableFingerprint({
            "ProblemFingerprint": Problem.ProblemFingerprint,
            "SelectedTemplateId": (
                SelectedTemplate.TemplateId
                if SelectedTemplate is not None
                else ""
            ),
            "Preparation": (
                Preparation.ToDictionary()
                if Preparation is not None
                else None
            ),
        })
        if Success and SelectedTemplate is not None and Preparation is not None
        else ""
    )
    return RawTrackAssignmentSelection(
        ProblemFingerprint=Problem.ProblemFingerprint,
        SelectionFingerprint=SelectionFingerprint,
        SelectedTemplateId=(
            SelectedTemplate.TemplateId
            if SelectedTemplate is not None
            else ""
        ),
        SelectedObjective=(
            SelectedTemplate.Objective
            if SelectedTemplate is not None
            else ()
        ),
        Preparation=Preparation,
        Attempts=AttemptValues,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=IncompleteReason,
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals=(
            FirstPairwiseIncompatibleSignals
        ),
        MaterializedTemplateCount=MaterializedTemplateCount,
        SkippedDominatedTemplateCount=SkippedDominatedTemplateCount,
    )


def _EmptyDomainAttempt(
    Template: RawTrackAssignmentTemplate,
    ExpansionCount: int,
) -> RawTrackAssignmentAttempt | None:
    """Return an exact complete empty-domain core, if one is declared."""
    EmptySignals = tuple(
        Signal
        for Signal, Count in Template.Domain.CandidateCounts
        if Count == 0
    )
    if not EmptySignals:
        return None
    return RawTrackAssignmentAttempt(
        TemplateId=Template.TemplateId,
        Objective=Template.Objective,
        Success=False,
        Complete=True,
        ExpansionCount=0,
        CumulativeExpansionCount=ExpansionCount,
        ConflictSignals=EmptySignals,
        IncompleteReason="complete-empty-candidate-domain",
    )


def SolveRawTrackAssignmentProblem(
    Problem: RawTrackAssignmentProblem,
    NativeSolve: NativeRawAssignmentSolver,
    *,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Select one template and its authoritative witness under one cap.

    ``NativeSolve`` must run the raw domain's exact capacity-one assignment
    using at most the passed *remaining global* expansion count.  A complete
    failed member is a capacity core for that fixed member and permits the
    next, already-materialized member.  Any incomplete member terminates the
    entire non-retrying selection immediately.
    """
    OrderedTemplates = tuple(sorted(
        Problem.Templates,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ))
    Attempts: list[RawTrackAssignmentAttempt] = []
    Spent = 0
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()

    for TemplateIndex, Template in enumerate(OrderedTemplates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "raw-template-track-assignment",
                "TemplateIndex": TemplateIndex,
                "TemplateCount": len(OrderedTemplates),
                "TemplateId": Template.TemplateId,
                "ExpansionCount": Spent,
                "MaximumAssignmentExpansions": (
                    Problem.MaximumAssignmentExpansions
                ),
            })
        if not Template.Domain.Complete:
            Attempts.append(RawTrackAssignmentAttempt(
                TemplateId=Template.TemplateId,
                Objective=Template.Objective,
                Success=False,
                Complete=False,
                ExpansionCount=0,
                CumulativeExpansionCount=Spent,
                IncompleteReason=(
                    Template.Domain.IncompleteReason
                    or "incomplete-raw-template-domain"
                ),
            ))
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="incomplete-template-domain",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

        EmptyDomain = _EmptyDomainAttempt(Template, Spent)
        if EmptyDomain is not None:
            Attempts.append(EmptyDomain)
            if not FirstConflictSignals:
                FirstConflictSignals = EmptyDomain.ConflictSignals
            continue

        Remaining = Problem.MaximumAssignmentExpansions - Spent
        if Remaining < 1:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="assignment-work-cap",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        NativeResult = NativeSolve(Template.Domain, Remaining)
        ResultExpansionCount = max(
            0,
            int(getattr(NativeResult, "ExpansionCount", 0)),
        )
        Spent = min(
            Problem.MaximumAssignmentExpansions,
            Spent + ResultExpansionCount,
        )
        DeadlineExceeded = bool(
            getattr(NativeResult, "DeadlineExceeded", False)
        )
        BudgetExhausted = bool(
            getattr(NativeResult, "BudgetExhausted", False)
        )
        ResultSuccess = bool(getattr(NativeResult, "Success", False))
        ConflictSignals = tuple(sorted(map(
            str,
            getattr(NativeResult, "ConflictSignals", ()),
        )))
        ConflictResourceIndices = tuple(sorted(map(
            int,
            getattr(NativeResult, "ConflictResourceIndices", ()),
        )))
        ResultComplete = not DeadlineExceeded and not BudgetExhausted
        IncompleteReason = (
            "assignment-deadline"
            if DeadlineExceeded
            else "assignment-work-cap"
            if BudgetExhausted
            else ""
        )
        Attempt = RawTrackAssignmentAttempt(
            TemplateId=Template.TemplateId,
            Objective=Template.Objective,
            Success=ResultSuccess and ResultComplete,
            Complete=ResultComplete,
            ExpansionCount=ResultExpansionCount,
            CumulativeExpansionCount=Spent,
            ConflictSignals=ConflictSignals,
            ConflictResourceIndices=ConflictResourceIndices,
            IncompleteReason=IncompleteReason,
        )
        Attempts.append(Attempt)
        if not FirstConflictSignals and ConflictSignals:
            FirstConflictSignals = ConflictSignals
            FirstConflictResourceIndices = ConflictResourceIndices
        if not ResultComplete:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason=IncompleteReason,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        if ResultSuccess:
            Preparation = BuildTrackAssignmentPreparationFromRawDomain(
                Template.Domain,
                NativeResult,
            )
            if not Preparation.Success or not Preparation.Complete:
                raise RuntimeError(
                    "complete native raw assignment did not produce a "
                    "complete frozen track witness"
                )
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=True,
                Complete=True,
                Unsatisfiable=False,
                SelectedTemplate=Template,
                Preparation=Preparation,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

    Unsatisfiable = not Problem.NonExhaustiveTemplateDomain
    return _BuildSelection(
        Problem,
        Attempts=Attempts,
        ExpansionCount=Spent,
        Success=False,
        Complete=Unsatisfiable,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=(
            "complete-capacity-core"
            if Unsatisfiable
            else "non-exhaustive-template-domain"
        ),
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
    )


def _BuildContextNativeRawAssignmentSolver(
    Context: Any | None,
    Deadline: RoutingDeadline,
) -> NativeRawAssignmentSolver:
    """Bind the existing native assignment API to one absolute deadline."""
    def NativeSolve(
        Domain: RawTrackAssignmentDomain,
        MaximumExpansions: int,
    ) -> Any:
        ActiveContext = (
            Domain.NativeAssignmentContext
            if Domain.NativeAssignmentContext is not None
            else Context
        )
        if ActiveContext is None:
            raise ValueError(
                "raw template assignment requires a native routing context"
            )
        RemainingMilliseconds = Deadline.RemainingMilliseconds()
        if RemainingMilliseconds < 1:
            return SimpleNamespace(
                Success=False,
                SelectedCandidateIds=(),
                ExpansionCount=0,
                BudgetExhausted=False,
                DeadlineExceeded=True,
                ConflictSignals=(),
                ConflictResourceIndices=(),
            )
        CandidateValues = Domain.NativeCandidateValues()
        BaseValues = Domain.NativeBaseValues()
        Arguments = (
            CandidateValues,
            Domain.NativeResourceCount,
            MaximumExpansions,
            RemainingMilliseconds,
        )
        if BaseValues:
            return ActiveContext.PlanAuthoritativeRoutesWithBaseBounded(
                CandidateValues,
                BaseValues,
                Domain.NativeResourceCount,
                MaximumExpansions,
                RemainingMilliseconds,
            )
        return ActiveContext.PlanAuthoritativeRoutesBounded(*Arguments)

    return NativeSolve


def _BuildNativeTemplateDomainPayload(
    Problem: RawTrackAssignmentProblem,
) -> list[tuple[object, ...]]:
    """Encode mutually exclusive raw domains for one native selection call."""
    return [
        (
            Template.TemplateId,
            list(Template.Objective),
            Template.Domain.NativeResourceCount,
            [Signal for Signal, _Count in Template.Domain.CandidateCounts],
            Template.Domain.NativeCandidateValues(),
            Template.Domain.NativeBaseValues(),
        )
        for Template in sorted(
            Problem.Templates,
            key=lambda Value: (Value.Objective, Value.TemplateId),
        )
    ]


def _BuildNativeTemplateSelection(
    Problem: RawTrackAssignmentProblem,
    NativeResult: Any,
) -> RawTrackAssignmentSelection:
    """Adapt the immutable Rust portfolio result to the existing handoff."""
    TemplateById = {
        Template.TemplateId: Template
        for Template in Problem.Templates
    }
    AttemptedIds = tuple(map(
        str,
        getattr(NativeResult, "AttemptedTemplateIds", ()),
    ))
    SelectedTemplateId = str(getattr(
        NativeResult,
        "SelectedTemplateId",
        "",
    ) or "")
    SelectedTemplate = TemplateById.get(SelectedTemplateId)
    Success = bool(getattr(NativeResult, "Success", False))
    Complete = bool(getattr(NativeResult, "Complete", False))
    if Success and (SelectedTemplate is None or not Complete):
        raise RuntimeError(
            "native template assignment returned an invalid frozen witness"
        )
    ExpansionCount = max(0, int(getattr(
        NativeResult,
        "ExpansionCount",
        0,
    )))
    ConflictSignals = tuple(sorted(map(
        str,
        getattr(NativeResult, "ConflictSignals", ()),
    )))
    ConflictResourceIndices = tuple(sorted(map(
        int,
        getattr(NativeResult, "ConflictResourceIndices", ()),
    )))
    PairwiseIncompatibleSignals = tuple(sorted(
        tuple(map(str, Value))
        for Value in getattr(
            NativeResult,
            "PairwiseIncompatibleSignals",
            (),
        )
    ))
    AttemptPairwiseIncompatibleSignals = {
        str(TemplateId): tuple(sorted(
            tuple(map(str, Pair)) for Pair in Values
        ))
        for TemplateId, Values in getattr(
            NativeResult,
            "AttemptPairwiseIncompatibleSignals",
            (),
        )
    }
    Attempts = tuple(
        RawTrackAssignmentAttempt(
            TemplateId=TemplateId,
            Objective=TemplateById[TemplateId].Objective,
            Success=Success and TemplateId == SelectedTemplateId,
            # Rust proves every attempted non-winning member complete before
            # it advances.  A terminal cap/deadline is attributed to the
            # final attempted member only.
            Complete=(
                Complete
                or TemplateIndex + 1 < len(AttemptedIds)
            ),
            ExpansionCount=(
                ExpansionCount
                if TemplateIndex + 1 == len(AttemptedIds)
                else 0
            ),
            CumulativeExpansionCount=(
                ExpansionCount
                if TemplateIndex + 1 == len(AttemptedIds)
                else 0
            ),
            ConflictSignals=(
                ConflictSignals
                if TemplateIndex == 0 and not Success
                else ()
            ),
            ConflictResourceIndices=(
                ConflictResourceIndices
                if TemplateIndex == 0 and not Success
                else ()
            ),
            PairwiseIncompatibleSignals=(
                AttemptPairwiseIncompatibleSignals.get(TemplateId, ())
            ),
            IncompleteReason=(
                str(getattr(NativeResult, "IncompleteReason", ""))
                if TemplateIndex + 1 == len(AttemptedIds) and not Complete
                else ""
            ),
        )
        for TemplateIndex, TemplateId in enumerate(AttemptedIds)
        if TemplateId in TemplateById
    )
    Preparation = (
        BuildTrackAssignmentPreparationFromRawDomain(
            SelectedTemplate.Domain,
            NativeResult,
        )
        if Success and SelectedTemplate is not None
        else None
    )
    if Preparation is not None and (
        not Preparation.Success or not Preparation.Complete
    ):
        raise RuntimeError(
            "native template assignment did not produce a complete witness"
        )
    return _BuildSelection(
        Problem,
        Attempts=Attempts,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=bool(getattr(NativeResult, "Unsatisfiable", False)),
        SelectedTemplate=SelectedTemplate if Success else None,
        Preparation=Preparation,
        IncompleteReason=str(getattr(NativeResult, "IncompleteReason", "")),
        FirstConflictSignals=ConflictSignals,
        FirstConflictResourceIndices=ConflictResourceIndices,
        FirstPairwiseIncompatibleSignals=PairwiseIncompatibleSignals,
        MaterializedTemplateCount=len(AttemptedIds),
    )


def _TrySolveRawTrackAssignmentProblemNatively(
    Problem: RawTrackAssignmentProblem,
    Deadline: RoutingDeadline,
    Context: Any | None,
) -> RawTrackAssignmentSelection | None:
    """Use the one-call native template selector when the binding is present."""
    if _SolveTemplateAssignmentDomainsBounded is None:
        return None
    # Preserve explicit fixture/executor injection.  The production template
    # portfolio will opt into this binding only after all predeclared members
    # are materialized; this raw-problem adapter must not bypass a caller's
    # supplied context during that migration.
    if Context is not None:
        return None
    # The Rust input is intentionally complete-only.  The established Python
    # path publishes a richer typed reason for an incomplete member without
    # serializing it as if it were a capacity core.
    if any(not Template.Domain.Complete for Template in Problem.Templates):
        return None
    RemainingMilliseconds = Deadline.RemainingMilliseconds()
    if RemainingMilliseconds < 1:
        return None
    NativeResult = _SolveTemplateAssignmentDomainsBounded(
        _BuildNativeTemplateDomainPayload(Problem),
        Problem.MaximumAssignmentExpansions,
        RemainingMilliseconds,
        Problem.NonExhaustiveTemplateDomain,
    )
    return _BuildNativeTemplateSelection(Problem, NativeResult)


def SolveRawTrackAssignmentProblemWithContext(
    Problem: RawTrackAssignmentProblem,
    *,
    Context: Any | None = None,
    Deadline: RoutingDeadline,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Run the aggregate selector through the existing Rust binding.

    The ordinary bounded assignment API is deliberately reused.  A raw domain
    may retain the context that created its local resource index; ``Context``
    is a fallback for synthetic or fixture domains.  Each call receives only
    the global remainder and the same absolute deadline's remaining
    milliseconds, so the outer selector has one work cap and one deadline
    even though template resource indices are local.
    """
    NativeSelection = _TrySolveRawTrackAssignmentProblemNatively(
        Problem,
        Deadline,
        Context,
    )
    if NativeSelection is not None:
        return NativeSelection
    return SolveRawTrackAssignmentProblem(
        Problem,
        _BuildContextNativeRawAssignmentSolver(Context, Deadline),
        WorkCheck=WorkCheck,
    )
