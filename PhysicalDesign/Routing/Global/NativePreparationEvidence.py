"""Detached observations of native preparation work, without result authority."""

from __future__ import annotations

import json


OMISSIONS = (
    "raw-native-canonical-input-bytes",
    "full-raw-domain-values-and-claims",
    "full-frozen-candidate-input-manifests",
)


def FreezeNativePreparationDocument(Document: dict[str, object]) -> str:
    """Own diagnostics as immutable JSON rather than retain mutable run state."""
    return json.dumps(Document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _ObserveNativePreparationOutcomes(Results) -> str:
    """Copy fields from actual validated results; never infer a terminal cause."""
    Observations = []
    for Result in Results:
        Receipt = Result.NativeReceipt
        Scope = Result.ExecutionScope
        Observations.append({
            "NativeOrdinal": Result.OriginalOrdinal,
            "RequestIdentity": Result.RequestIdentity,
            "Kind": Result.Kind.value,
            "Reason": Result.Reason,
            "SearchOutcome": Result.SearchOutcome.value,
            "ClaimStrength": Result.ClaimStrength.value,
            "CommitEligibility": Result.CommitEligibility.value,
            "OutcomePhase": Receipt.OutcomePhase,
            "Started": Receipt.Started,
            "Settled": Receipt.Settled,
            "CancellationRequested": Receipt.CancellationRequested,
            "CancellationAcknowledged": Receipt.CancellationAcknowledged,
            "SearchStopped": Receipt.SearchStopped,
            "CleanupDisposition": Receipt.CleanupDisposition,
            "DeadlineAtMonotonicSeconds": Scope.DeadlineAtMonotonicSeconds,
            "ExpansionCap": Result.ExpansionCap,
            "ActualExpansionCount": Result.ActualExpansionCount,
            "RouteExpansionCount": Receipt.RouteExpansionCount,
            "ProofExpansionCount": Receipt.ProofExpansionCount,
            "Identity": {
                "BatchIdentity": Scope.BatchIdentity,
                "ContextGraphIdentity": Scope.ContextGraphIdentity,
                "RouteDomainIdentity": Scope.RouteDomainIdentity,
                "CallerSourceIdentity": Scope.CallerSourceIdentity,
                "ImmutableInputIdentity": Scope.ImmutableInputIdentity,
                "ReceiptIdentity": Scope.ReceiptIdentity,
                "NativePayloadIdentity": Scope.NativeRequestPayloadIdentity,
            },
            "Availability": {
                "ContextGraph": Receipt.ContextGraphIdentityAvailability,
                "RouteDomain": Receipt.RouteDomainIdentityAvailability,
                "ImmutableInput": Receipt.ImmutableInputIdentityAvailability,
                "Receipt": Receipt.ReceiptIdentityAvailability,
                "ReceiptDependency": Receipt.ReceiptIdentityDependency,
                "CallerEcho": Receipt.CallerEchoIdentityAvailability,
            },
        })
    return FreezeNativePreparationDocument({"NativeOutcomes": Observations})


def ObserveNativePreparationOutcomes(Results) -> str:
    """Keep a diagnostic projection failure separate from the routing result."""
    try:
        return _ObserveNativePreparationOutcomes(Results)
    except Exception as Error:
        return FreezeNativePreparationDocument({
            "UnavailableReason": _DiagnosticReason(Error),
            "NativeOutcomes": [],
        })


SCOPE_NAMES = (
    "PlacementFingerprint", "ResourceGraphFingerprint", "PortalDomainFingerprint",
    "CandidateDomainFingerprint", "LocalClaimDomainFingerprint",
    "PinAccessDomainFingerprint", "PinAccessWitnessFingerprint",
)


def CaptureNativePreparationObservation(State, *, Scope=None) -> str:
    """Snapshot published batches after admission; projection cannot change search."""
    try:
        Batches = State.TypedNativeRouteBatches
        OutcomesBySequence = State.NativePreparationOutcomesByInvocationSequence
        if type(Batches) is not list or type(OutcomesBySequence) is not dict:
            raise TypeError("native preparation collection is unavailable")
        if not Batches and OutcomesBySequence:
            raise ValueError("native results returned before batch publication")
        Detached = json.loads(FreezeNativePreparationDocument({"Batches": Batches}))["Batches"]
        Complete = True
        for Batch in Detached:
            Sequence = Batch["InvocationSequence"]
            OutcomeDocument = json.loads(OutcomesBySequence.get(Sequence, '{"NativeOutcomes":[]}'))
            if OutcomeDocument.get("UnavailableReason") is not None:
                raise ValueError(OutcomeDocument["UnavailableReason"])
            NativeOutcomes = OutcomeDocument["NativeOutcomes"]
            Batch["NativeOutcomes"] = NativeOutcomes
            CanonicalOrigins = [Origin for Origin in Batch["Origins"] if Origin["NativeOrdinal"] is not None]
            if len(CanonicalOrigins) != len(NativeOutcomes):
                raise ValueError("native preparation ordinal coverage mismatch")
            if Batch["Counters"]["CanonicalExecuted"] != len(NativeOutcomes):
                raise ValueError("native preparation submission count mismatch")
            ByOrdinal = {Outcome["NativeOrdinal"]: Outcome for Outcome in NativeOutcomes}
            if len(ByOrdinal) != len(NativeOutcomes):
                raise ValueError("native preparation repeats a native ordinal")
            for Origin in CanonicalOrigins:
                Outcome = ByOrdinal.get(Origin["NativeOrdinal"])
                if Outcome is None or (
                    Origin["CanonicalRequestId"] != Outcome["RequestIdentity"]
                    or Origin["NativeKind"] != Outcome["Kind"]
                    or Origin["NativePayloadIdentity"] != Outcome["Identity"]["NativePayloadIdentity"]
                    or Origin["CanonicalReceiptIdentity"] != Outcome["Identity"]["ReceiptIdentity"]
                ):
                    raise ValueError("native preparation origin/result mismatch")
            ByOriginOrdinal = {Origin["OriginalOrdinal"]: Origin for Origin in Batch["Origins"]}
            for Origin in Batch["Origins"]:
                if Origin["NativeKind"] == "PreNativeIncomplete":
                    continue
                CanonicalOrigin = ByOriginOrdinal.get(Origin["CanonicalOriginalOrdinal"])
                if CanonicalOrigin is None or (
                    CanonicalOrigin["OriginIdentity"] != Origin["CanonicalOriginIdentity"]
                    or CanonicalOrigin["CanonicalRequestId"] != Origin["CanonicalRequestId"]
                    or CanonicalOrigin["NativeKind"] != Origin["NativeKind"]
                    or CanonicalOrigin["CanonicalReceiptIdentity"] != Origin["CanonicalReceiptIdentity"]
                ):
                    raise ValueError("native preparation equivalent-origin mismatch")
            AdmittedOrigins = {Admission["OriginIdentity"] for Admission in Batch["Admissions"]}
            Complete = Complete and all(Origin["OriginIdentity"] in AdmittedOrigins for Origin in Batch["Origins"])
        Cache = getattr(State, "EffectiveRawPortalCache", None)
        Witness = getattr(State, "PlacementPinAccessWitness", None)
        ObservedScope = {
            "PlacementFingerprint": getattr(Cache, "PlacementGeometryFingerprint", ""),
            "ResourceGraphFingerprint": getattr(Cache, "ResourceGeometryFingerprint", ""),
            "PortalDomainFingerprint": "",
            "CandidateDomainFingerprint": "",
            "LocalClaimDomainFingerprint": getattr(State, "PreRouteLocalClaimDomainFingerprint", None) or "",
            "PinAccessDomainFingerprint": getattr(Witness, "DomainFingerprint", ""),
            "PinAccessWitnessFingerprint": getattr(Witness, "WitnessFingerprint", ""),
        }
        if Scope is not None:
            ObservedScope.update(Scope)
        if set(ObservedScope) != set(SCOPE_NAMES) or any(type(Value) is not str for Value in ObservedScope.values()):
            raise TypeError("observed preparation scope must contain exact string fingerprints")
        return FreezeNativePreparationDocument({
            "ObservationState": "Observed" if Detached else "NoNativeSubmissionObserved",
            "UnavailableReason": None,
            "PinAccessWitnessFingerprint": getattr(Witness, "WitnessFingerprint", ""),
            "Scope": ObservedScope,
            "PreparationObservationComplete": Complete,
            "Batches": Detached,
        })
    except Exception as Error:
        return FreezeNativePreparationDocument({
            "ObservationState": "Unavailable",
            "UnavailableReason": _DiagnosticReason(Error),
            "PreparationObservationComplete": False,
            "Batches": [],
        })


def _DiagnosticReason(Error: Exception) -> str:
    try:
        return f"{type(Error).__name__}: {Error}"
    except Exception:
        return "diagnostic projection failed"


def _UnavailableEvidence(Reason: str) -> dict[str, object]:
    return {
        "SchemaVersion": "native-preparation-evidence-v1",
        "ObservationState": "Unavailable",
        "UnavailableReason": Reason,
        "CandidateObservations": [],
        "Omissions": list(OMISSIONS),
    }


def UnavailableNativePreparationEvidence(Reason: str) -> str:
    """Serialize an unavailable observation without caller-owned metadata."""
    return FreezeNativePreparationDocument(_UnavailableEvidence(
        Reason if type(Reason) is str else "diagnostic projection failed",
    ))


def _CandidateEvidenceDocument(
    State, Reason, CandidateId, CandidateInputFingerprint, Scope,
    SemanticDomainComplete, Complete, Batches,
):
    return {
        "SchemaVersion": "native-preparation-evidence-v1",
        "ObservationState": State,
        "UnavailableReason": Reason,
        "CandidateObservations": [{
            "CandidateId": CandidateId,
            "CandidateInputFingerprint": CandidateInputFingerprint,
            "Scope": Scope,
            "Coverage": {
                "PreparationObservationComplete": Complete,
                "NativeBatchCount": len(Batches),
                "NativeOriginCount": sum(len(Batch["Origins"]) for Batch in Batches),
                "CanonicalOutcomeCount": sum(len(Batch["NativeOutcomes"]) for Batch in Batches),
                "SemanticDomainComplete": SemanticDomainComplete,
                "OuterPortfolioComplete": None,
            },
            "Batches": Batches,
        }],
        "Omissions": list(OMISSIONS),
    }


def BindCandidateNativePreparationEvidence(
    Observation: object,
    *,
    CandidateId: str,
    CandidateInputFingerprint: str | None,
    Scope: dict[str, object],
    SemanticDomainComplete: bool | None,
) -> str:
    """Associate an observed preparation with its exact originating candidate."""
    try:
        if type(CandidateId) is not str or not CandidateId:
            raise TypeError("candidate identity must be a nonempty string")
        if type(Scope) is not dict or set(Scope) != set(SCOPE_NAMES):
            raise TypeError("preparation scope must contain the seven declared fingerprints")
        if any(type(Value) is not str for Value in Scope.values()):
            raise TypeError("preparation scope fingerprints must be strings")
        if SemanticDomainComplete is not None and type(SemanticDomainComplete) is not bool:
            raise TypeError("semantic domain completeness must be exact bool or unavailable")
        if type(Observation) is not str:
            raise ValueError("preparation observation absent")
        Document = json.loads(Observation)
        if Document["ObservationState"] not in ("Observed", "NoNativeSubmissionObserved", "Unavailable"):
            raise ValueError("unsupported preparation observation state")
        Batches = Document["Batches"]
        if type(Batches) is not list or type(Document["PreparationObservationComplete"]) is not bool:
            raise TypeError("malformed preparation observation")
        if Document["UnavailableReason"] is not None and type(Document["UnavailableReason"]) is not str:
            raise TypeError("malformed preparation unavailable reason")
        if CandidateInputFingerprint is not None and (
            type(CandidateInputFingerprint) is not str or not CandidateInputFingerprint
        ):
            raise TypeError("candidate input identity must be nonempty or unavailable")
        if Document["ObservationState"] == "NoNativeSubmissionObserved" and Batches:
            raise ValueError("empty native observation contains batches")
        for Batch in Batches:
            if type(Batch["Origins"]) is not list or type(Batch["NativeOutcomes"]) is not list:
                raise TypeError("malformed preparation batch")
        if Document["ObservationState"] != "Unavailable":
            ObservedScope = Document.get("Scope")
            if type(ObservedScope) is not dict or set(ObservedScope) != set(SCOPE_NAMES):
                raise TypeError("observed preparation scope is unavailable")
            if any(type(Value) is not str for Value in ObservedScope.values()):
                raise TypeError("observed preparation scope fingerprints must be strings")
            if Document.get("PinAccessWitnessFingerprint") != ObservedScope["PinAccessWitnessFingerprint"]:
                raise ValueError("consumed witness observation contradicts its scope")
            for Name, Expected in Scope.items():
                if Expected and Expected != ObservedScope[Name]:
                    raise ValueError(f"preparation {Name} scope mismatch")
            # An absent selected-access authority does not erase the witness
            # actually consumed. Unknown current fields remain observations.
            Scope = {Name: Expected or ObservedScope[Name] for Name, Expected in Scope.items()}
        State = Document["ObservationState"]
        Reason = Document["UnavailableReason"]
        Complete = Document["PreparationObservationComplete"]
        return FreezeNativePreparationDocument(_CandidateEvidenceDocument(
            State, Reason, CandidateId, CandidateInputFingerprint, Scope,
            SemanticDomainComplete, Complete, Batches,
        ))
    except Exception as Error:
        Reason = _DiagnosticReason(Error)
        if type(CandidateId) is not str or not CandidateId:
            return FreezeNativePreparationDocument(_UnavailableEvidence(Reason))
        SafeScope = {
            Name: Scope.get(Name, "") if type(Scope) is dict and type(Scope.get(Name)) is str else ""
            for Name in SCOPE_NAMES
        }
        SafeInput = CandidateInputFingerprint if type(CandidateInputFingerprint) is str and CandidateInputFingerprint else None
        SafeComplete = SemanticDomainComplete if type(SemanticDomainComplete) is bool else None
        return FreezeNativePreparationDocument(_CandidateEvidenceDocument(
            "Unavailable", Reason, CandidateId, SafeInput, SafeScope,
            SafeComplete, False, [],
        ))


def _CombineNativePreparationEvidence(Observations: dict[str, str]) -> dict[str, object]:
    """Project detached candidate observations for coordinator failure publication."""
    Candidates = []
    States = []
    Reasons = []
    for CandidateId, Encoded in sorted(Observations.items()):
        try:
            Document = json.loads(Encoded)
            if Document["SchemaVersion"] != "native-preparation-evidence-v1":
                raise ValueError("unsupported native preparation evidence")
            if Document["ObservationState"] not in ("Observed", "NoNativeSubmissionObserved", "Unavailable"):
                raise ValueError("unsupported native preparation observation state")
            if Document["UnavailableReason"] is not None and type(Document["UnavailableReason"]) is not str:
                raise TypeError("malformed preparation unavailable reason")
            Members = Document["CandidateObservations"]
            if Members == [] and Document["ObservationState"] == "Unavailable":
                States.append("Unavailable")
                Reasons.append(Document["UnavailableReason"] or "candidate observation unavailable")
                continue
            if len(Members) != 1 or Members[0]["CandidateId"] != CandidateId:
                raise ValueError("native preparation candidate identity mismatch")
            Candidates.extend(Members)
            States.append(Document["ObservationState"])
            if Document["UnavailableReason"] is not None:
                Reasons.append(Document["UnavailableReason"])
        except Exception as Error:
            States.append("Unavailable")
            Reasons.append(f"{type(Error).__name__}: {Error}")
    State = (
        "Observed" if "Observed" in States
        else "NoNativeSubmissionObserved" if States and all(Value == "NoNativeSubmissionObserved" for Value in States)
        else "Unavailable"
    )
    return {
        "SchemaVersion": "native-preparation-evidence-v1",
        "ObservationState": State,
        "UnavailableReason": "; ".join(Reasons) if Reasons else ("preparation observation absent" if not States else None),
        "CandidateObservations": Candidates,
        "Omissions": list(OMISSIONS),
    }


def CombineNativePreparationEvidence(Observations: dict[str, str]) -> dict[str, object]:
    """Keep malformed transport or serialization from replacing a typed failure."""
    try:
        if type(Observations) is not dict or any(type(Key) is not str for Key in Observations):
            raise TypeError("candidate observation collection is malformed")
        return json.loads(FreezeNativePreparationDocument(_CombineNativePreparationEvidence(Observations)))
    except Exception as Error:
        return _UnavailableEvidence(_DiagnosticReason(Error))
