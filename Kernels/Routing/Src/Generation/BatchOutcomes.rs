//! Versioned, per-request authoritative outcomes for native route batches.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{Position, RouteTreeSearchResult, RoutingContext};
use crate::Core::Runtime::RoutingThreadPool;
use crate::Core::WorkAdmission::{ExpansionWorkPhase, RequestExpansionAdmissionV1};
use crate::Generation::DetailedTrees::{
    DeadlineAwareElectricalResult, FindSelfExcitingRepeaterCyclesWithDeadline,
    PropagateCanonicalRoutePowerWithDeadline, RepeaterInputFacing,
};
#[cfg(test)]
use crate::Generation::DetailedTrees::{
    FindSelfExcitingRepeaterCycles, PropagateCanonicalRoutePower,
};
use pyo3::prelude::*;
use pyo3::types::{PyModule, PyString, PyStringData};
use rayon::prelude::*;
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::io::{Error as IoError, ErrorKind as IoErrorKind, Write};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const CONTRACT_VERSION: &str = "native-route-batch-outcomes/v1";
const COARSE_REQUEST_KIND: &str = "CoarseColumnsV1";
const DETAILED_REQUEST_KIND: &str = "DetailedNodesV1";
const VERIFIED: &str = "Verified";
const UNCOMPUTED_DEADLINE: &str = "UncomputedDueToDeadline";
const UNSUPPORTED_INPUT: &str = "UnavailableUnsupportedInput";
const PRODUCER_FAILURE: &str = "ProducerFailure";
const DEPENDENCY_UNAVAILABLE: &str = "DependencyUnavailable";
const REQUIRED_CALLER_BINDINGS: [&str; 7] = [
    "DependencySnapshotIdentity",
    "ModelIdentity",
    "PlacementIdentity",
    "PolicyIdentity",
    "ResourceIdentity",
    "SelectedAccessIdentity",
    "TechnologyIdentity",
];

#[cfg(test)]
static TEST_PANIC_AFTER_NORMALIZATION_BATCH: std::sync::Mutex<Option<(&'static str, usize)>> =
    std::sync::Mutex::new(None);
#[cfg(test)]
static TEST_PANIC_DURING_NORMALIZATION_BATCH: std::sync::Mutex<Option<(&'static str, usize)>> =
    std::sync::Mutex::new(None);

type Bounds3d = (i32, i32, i32, i32, i32, i32);
type Bounds2d = (i32, i32, i32, i32);

enum RetainedBatchIdentitySourceV1 {
    #[cfg(test)]
    Owned(Arc<str>),
    Python(Arc<Py<PyString>>),
}

pub(crate) struct RetainedBatchIdentityV1 {
    Source: RetainedBatchIdentitySourceV1,
    SealedUtf8: Option<Arc<str>>,
    RetentionStatus: &'static str,
}

impl RetainedBatchIdentityV1 {
    #[cfg(test)]
    fn Owned(Value: &str) -> Arc<Self> {
        let Value: Arc<str> = Arc::from(Value);
        Arc::new(Self {
            Source: RetainedBatchIdentitySourceV1::Owned(Value.clone()),
            SealedUtf8: Some(Value),
            RetentionStatus: "SealedUtf8",
        })
    }

    pub(crate) fn FromPython(
        PythonValue: Python<'_>,
        Value: Py<PyString>,
        Deadline: &RuntimeDeadline,
    ) -> PyResult<Arc<Self>> {
        if !Value.bind(PythonValue).is_exact_instance_of::<PyString>() {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "batch identity must be an exact Python str",
            ));
        }
        let CharacterCount = Value.bind(PythonValue).len()?;
        if CharacterCount == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "batch identity must not be empty",
            ));
        }
        let SealedUtf8 = if Deadline.Check() {
            None
        } else {
            let Data = unsafe { Value.bind(PythonValue).data()? };
            let MaximumUtf8Bytes = CharacterCount.checked_mul(4).ok_or_else(|| {
                pyo3::exceptions::PyOverflowError::new_err(
                    "batch identity is too large to represent as UTF-8",
                )
            })?;
            let mut Text = String::with_capacity(MaximumUtf8Bytes);
            let mut Interrupted = false;
            let mut AppendCodePoint = |Index: usize, CodePoint: u32| -> PyResult<bool> {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Ok(false);
                }
                let Character = char::from_u32(CodePoint).ok_or_else(|| {
                    pyo3::exceptions::PyUnicodeError::new_err(
                        "batch identity contains an unpaired surrogate",
                    )
                })?;
                Text.push(Character);
                Ok(true)
            };
            match Data {
                PyStringData::Ucs1(Values) => {
                    for (Index, Value) in Values.iter().copied().enumerate() {
                        if !AppendCodePoint(Index, u32::from(Value))? {
                            Interrupted = true;
                            break;
                        }
                    }
                }
                PyStringData::Ucs2(Values) => {
                    for (Index, Value) in Values.iter().copied().enumerate() {
                        if !AppendCodePoint(Index, u32::from(Value))? {
                            Interrupted = true;
                            break;
                        }
                    }
                }
                PyStringData::Ucs4(Values) => {
                    for (Index, Value) in Values.iter().copied().enumerate() {
                        if !AppendCodePoint(Index, Value)? {
                            Interrupted = true;
                            break;
                        }
                    }
                }
            };
            if Interrupted || Deadline.Check() {
                None
            } else {
                Some(Arc::from(Text))
            }
        };
        Ok(Arc::new(Self {
            Source: RetainedBatchIdentitySourceV1::Python(Arc::new(Value)),
            RetentionStatus: if SealedUtf8.is_some() {
                "SealedUtf8"
            } else {
                "RetainedPythonObject"
            },
            SealedUtf8,
        }))
    }

    fn ToPython(&self, PythonValue: Python<'_>) -> PyObject {
        match &self.Source {
            #[cfg(test)]
            RetainedBatchIdentitySourceV1::Owned(Value) => {
                PyString::new_bound(PythonValue, Value).into_py(PythonValue)
            }
            RetainedBatchIdentitySourceV1::Python(Value) => {
                Value.clone_ref(PythonValue).into_py(PythonValue)
            }
        }
    }
}

#[derive(Clone)]
pub(crate) struct SealedCoarseRequestV1 {
    RequestId: Arc<str>,
    CallerEchoBindings: Arc<Vec<(String, String)>>,
    CancellationRequestedBeforeStart: bool,
    Starts: Arc<Vec<Position>>,
    TargetBranches: Arc<Vec<Vec<Position>>>,
    AllowedColumns: Arc<Vec<(i32, i32)>>,
    RequiredNodes: Arc<Vec<Position>>,
    BlockedNodeValues: Arc<Vec<Position>>,
    PreferredColumns: Arc<Vec<(i32, i32)>>,
    PreferredRoutingY: i32,
    GuidePenalty: i32,
    BendPenalty: i32,
    ViaPenalty: i32,
    MaximumExpansionCount: usize,
    NativePayloadCanonicalJson: Arc<str>,
    NativePayloadSha256: Arc<str>,
    ImmutableInputCanonicalJson: Arc<str>,
    ImmutableInputSha256: Arc<str>,
    RawCallerEchoScopeCanonicalJson: Arc<str>,
    RawCallerEchoScopeSha256: Arc<str>,
    CanonicalCallerEchoScopeCanonicalJson: Option<Arc<str>>,
    CanonicalCallerEchoScopeSha256: Option<Arc<str>>,
}

#[derive(Clone)]
pub(crate) struct SealedDetailedRequestV1 {
    RequestId: Arc<str>,
    CallerEchoBindings: Arc<Vec<(String, String)>>,
    CancellationRequestedBeforeStart: bool,
    Starts: Arc<Vec<Position>>,
    TargetBranches: Arc<Vec<Vec<Position>>>,
    AllowedNodeValues: Arc<Vec<Position>>,
    BlockedNodeValues: Arc<Vec<Position>>,
    PreferredColumns: Arc<Vec<(i32, i32)>>,
    NodeCostValues: Arc<Vec<(Position, i32)>>,
    PreferredRoutingY: i32,
    GuidePenalty: i32,
    BendPenalty: i32,
    ViaPenalty: i32,
    EnforceSignalStrength: bool,
    MaximumExpansionCount: usize,
    NativePayloadCanonicalJson: Arc<str>,
    NativePayloadSha256: Arc<str>,
    ImmutableInputCanonicalJson: Arc<str>,
    ImmutableInputSha256: Arc<str>,
    RawCallerEchoScopeCanonicalJson: Arc<str>,
    RawCallerEchoScopeSha256: Arc<str>,
    CanonicalCallerEchoScopeCanonicalJson: Option<Arc<str>>,
    CanonicalCallerEchoScopeSha256: Option<Arc<str>>,
}

#[pyclass(frozen)]
#[derive(Clone)]
pub(crate) struct RouteTreeCoarseRequestV1 {
    Sealed: Arc<SealedCoarseRequestV1>,
    #[pyo3(get)]
    pub(crate) RequestId: String,
    #[pyo3(get)]
    pub(crate) CallerEchoBindings: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) DeclaredBounds: Bounds3d,
    #[pyo3(get)]
    pub(crate) DeclaredPlacementBounds: Bounds2d,
    #[pyo3(get)]
    pub(crate) CancellationRequestedBeforeStart: bool,
    #[pyo3(get)]
    pub(crate) Starts: Vec<Position>,
    #[pyo3(get)]
    pub(crate) TargetBranches: Vec<Vec<Position>>,
    #[pyo3(get)]
    pub(crate) AllowedColumns: Vec<(i32, i32)>,
    #[pyo3(get)]
    pub(crate) RequiredNodes: Vec<Position>,
    #[pyo3(get)]
    pub(crate) BlockedNodeValues: Vec<Position>,
    #[pyo3(get)]
    pub(crate) PreferredColumns: Vec<(i32, i32)>,
    #[pyo3(get)]
    pub(crate) PreferredRoutingY: i32,
    #[pyo3(get)]
    pub(crate) GuidePenalty: i32,
    #[pyo3(get)]
    pub(crate) BendPenalty: i32,
    #[pyo3(get)]
    pub(crate) ViaPenalty: i32,
    #[pyo3(get)]
    pub(crate) MaximumExpansionCount: usize,
}

#[pymethods]
impl RouteTreeCoarseRequestV1 {
    #[getter]
    fn ContractVersion(&self) -> &'static str {
        CONTRACT_VERSION
    }

    #[getter]
    fn RequestKind(&self) -> &'static str {
        COARSE_REQUEST_KIND
    }

    #[getter]
    fn NativePayloadCanonicalJson(&self) -> &str {
        &self.Sealed.NativePayloadCanonicalJson
    }

    #[getter]
    fn NativePayloadSha256(&self) -> &str {
        &self.Sealed.NativePayloadSha256
    }

    #[getter]
    fn ImmutableInputSha256(&self) -> &str {
        &self.Sealed.ImmutableInputSha256
    }

    #[getter]
    fn CallerEchoScopeSha256(&self) -> &str {
        self.Sealed
            .CanonicalCallerEchoScopeSha256
            .as_deref()
            .unwrap_or(&self.Sealed.RawCallerEchoScopeSha256)
    }

    #[new]
    #[allow(clippy::too_many_arguments)]
    fn New(
        RequestId: String,
        CallerEchoBindings: Vec<(String, String)>,
        DeclaredBounds: Bounds3d,
        DeclaredPlacementBounds: Bounds2d,
        CancellationRequestedBeforeStart: bool,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredNodes: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Self {
        let Sealed = SealCoarseRequestV1(
            &RequestId,
            &CallerEchoBindings,
            DeclaredBounds,
            DeclaredPlacementBounds,
            CancellationRequestedBeforeStart,
            &Starts,
            &TargetBranches,
            &AllowedColumns,
            &RequiredNodes,
            &BlockedNodeValues,
            &PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
        );
        Self {
            Sealed,
            RequestId,
            CallerEchoBindings,
            DeclaredBounds,
            DeclaredPlacementBounds,
            CancellationRequestedBeforeStart,
            Starts,
            TargetBranches,
            AllowedColumns,
            RequiredNodes,
            BlockedNodeValues,
            PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
        }
    }
}

#[pyclass(frozen)]
#[derive(Clone)]
pub(crate) struct RouteTreeDetailedRequestV1 {
    Sealed: Arc<SealedDetailedRequestV1>,
    #[pyo3(get)]
    pub(crate) RequestId: String,
    #[pyo3(get)]
    pub(crate) CallerEchoBindings: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) DeclaredBounds: Bounds3d,
    #[pyo3(get)]
    pub(crate) DeclaredPlacementBounds: Bounds2d,
    #[pyo3(get)]
    pub(crate) CancellationRequestedBeforeStart: bool,
    #[pyo3(get)]
    pub(crate) Starts: Vec<Position>,
    #[pyo3(get)]
    pub(crate) TargetBranches: Vec<Vec<Position>>,
    #[pyo3(get)]
    pub(crate) AllowedNodeValues: Vec<Position>,
    #[pyo3(get)]
    pub(crate) BlockedNodeValues: Vec<Position>,
    #[pyo3(get)]
    pub(crate) PreferredColumns: Vec<(i32, i32)>,
    #[pyo3(get)]
    pub(crate) NodeCostValues: Vec<(Position, i32)>,
    #[pyo3(get)]
    pub(crate) PreferredRoutingY: i32,
    #[pyo3(get)]
    pub(crate) GuidePenalty: i32,
    #[pyo3(get)]
    pub(crate) BendPenalty: i32,
    #[pyo3(get)]
    pub(crate) ViaPenalty: i32,
    #[pyo3(get)]
    pub(crate) EnforceSignalStrength: bool,
    #[pyo3(get)]
    pub(crate) MaximumExpansionCount: usize,
}

#[pymethods]
impl RouteTreeDetailedRequestV1 {
    #[getter]
    fn ContractVersion(&self) -> &'static str {
        CONTRACT_VERSION
    }

    #[getter]
    fn RequestKind(&self) -> &'static str {
        DETAILED_REQUEST_KIND
    }

    #[getter]
    fn NativePayloadCanonicalJson(&self) -> &str {
        &self.Sealed.NativePayloadCanonicalJson
    }

    #[getter]
    fn NativePayloadSha256(&self) -> &str {
        &self.Sealed.NativePayloadSha256
    }

    #[getter]
    fn ImmutableInputSha256(&self) -> &str {
        &self.Sealed.ImmutableInputSha256
    }

    #[getter]
    fn CallerEchoScopeSha256(&self) -> &str {
        self.Sealed
            .CanonicalCallerEchoScopeSha256
            .as_deref()
            .unwrap_or(&self.Sealed.RawCallerEchoScopeSha256)
    }

    #[new]
    #[allow(clippy::too_many_arguments)]
    fn New(
        RequestId: String,
        CallerEchoBindings: Vec<(String, String)>,
        DeclaredBounds: Bounds3d,
        DeclaredPlacementBounds: Bounds2d,
        CancellationRequestedBeforeStart: bool,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        NodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
    ) -> Self {
        let Sealed = SealDetailedRequestV1(
            &RequestId,
            &CallerEchoBindings,
            DeclaredBounds,
            DeclaredPlacementBounds,
            CancellationRequestedBeforeStart,
            &Starts,
            &TargetBranches,
            &AllowedNodeValues,
            &BlockedNodeValues,
            &PreferredColumns,
            &NodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
        );
        Self {
            Sealed,
            RequestId,
            CallerEchoBindings,
            DeclaredBounds,
            DeclaredPlacementBounds,
            CancellationRequestedBeforeStart,
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            NodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
        }
    }
}

#[pyclass(frozen)]
#[derive(Clone)]
pub(crate) struct RouteTreeNoPathProofV1 {
    #[pyo3(get)]
    pub(crate) ProofKind: String,
    #[pyo3(get)]
    pub(crate) UnreachableTargetBranchOrdinals: Vec<usize>,
    #[pyo3(get)]
    pub(crate) UnreachableAttachmentNodes: Vec<Position>,
    #[pyo3(get)]
    pub(crate) ReachedNodeCount: usize,
    #[pyo3(get)]
    pub(crate) ExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) Complete: bool,
    #[pyo3(get)]
    pub(crate) ClaimScope: String,
    #[pyo3(get)]
    pub(crate) ContextGraphSha256: String,
    #[pyo3(get)]
    pub(crate) RouteDomainScopeSha256: String,
    #[pyo3(get)]
    pub(crate) ImmutableInputSha256: String,
    #[pyo3(get)]
    pub(crate) ReceiptScopeSha256: String,
}

#[pyclass(frozen)]
#[derive(Clone)]
pub(crate) struct RouteTreeRequestReceiptV1 {
    #[pyo3(get)]
    pub(crate) ContractVersion: String,
    pub(crate) BatchIdentity: Arc<RetainedBatchIdentityV1>,
    #[pyo3(get)]
    pub(crate) BatchIdentityRetentionStatus: String,
    #[pyo3(get)]
    pub(crate) RequestKind: String,
    pub(crate) RequestId: Arc<str>,
    #[pyo3(get)]
    pub(crate) OriginalOrdinal: usize,
    #[pyo3(get)]
    pub(crate) SearchOutcome: String,
    #[pyo3(get)]
    pub(crate) TerminalReason: String,
    #[pyo3(get)]
    pub(crate) RuntimeSearchOutcome: String,
    #[pyo3(get)]
    pub(crate) RuntimeClaimStrength: String,
    #[pyo3(get)]
    pub(crate) RuntimeCommitEligibility: String,
    #[pyo3(get)]
    pub(crate) Started: bool,
    #[pyo3(get)]
    pub(crate) Settled: bool,
    #[pyo3(get)]
    pub(crate) StartedAtMicroseconds: Option<u64>,
    #[pyo3(get)]
    pub(crate) CompletedAtMicroseconds: Option<u64>,
    #[pyo3(get)]
    pub(crate) DeadlineAtMonotonicSeconds: f64,
    #[pyo3(get)]
    pub(crate) BoundaryMonotonicSampleSeconds: f64,
    #[pyo3(get)]
    pub(crate) NativeRemainingNanoseconds: u64,
    #[pyo3(get)]
    pub(crate) WorkUnit: String,
    #[pyo3(get)]
    pub(crate) MaximumExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) RouteExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) ProofExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) TotalExpansionCount: usize,
    pub(crate) NativePayloadCanonicalJson: Arc<str>,
    pub(crate) NativePayloadSha256: Arc<str>,
    #[pyo3(get)]
    pub(crate) RawInputRetentionStatus: String,
    #[pyo3(get)]
    pub(crate) CancellationSnapshotStatus: String,
    #[pyo3(get)]
    pub(crate) OutcomePhase: String,
    #[pyo3(get)]
    pub(crate) ContextGraphIdentityAvailability: String,
    pub(crate) ContextGraphSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) RouteDomainIdentityAvailability: String,
    pub(crate) RouteDomainScopeCanonicalJson: Option<Arc<str>>,
    pub(crate) RouteDomainScopeSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) ImmutableInputIdentityAvailability: String,
    pub(crate) ImmutableInputCanonicalJson: Option<Arc<str>>,
    pub(crate) ImmutableInputSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) ReceiptIdentityAvailability: String,
    #[pyo3(get)]
    pub(crate) ReceiptIdentityDependency: Option<String>,
    pub(crate) ReceiptScopeCanonicalJson: Option<Arc<str>>,
    pub(crate) ReceiptScopeSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) CallerEchoIdentityAvailability: String,
    pub(crate) CallerEchoScopeCanonicalJson: Option<Arc<str>>,
    pub(crate) CallerEchoScopeSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) Candidate: Option<RouteTreeSearchResult>,
    #[pyo3(get)]
    pub(crate) NoPathProof: Option<RouteTreeNoPathProofV1>,
    #[pyo3(get)]
    pub(crate) CancellationRequested: bool,
    #[pyo3(get)]
    pub(crate) CancellationAcknowledged: bool,
    #[pyo3(get)]
    pub(crate) SearchStopped: bool,
    #[pyo3(get)]
    pub(crate) CleanupDisposition: String,
}

#[pyclass(frozen)]
pub(crate) struct RouteTreeBatchOutcomesV1 {
    #[pyo3(get)]
    pub(crate) ContractVersion: String,
    pub(crate) BatchIdentity: Arc<RetainedBatchIdentityV1>,
    #[pyo3(get)]
    pub(crate) BatchIdentityRetentionStatus: String,
    #[pyo3(get)]
    pub(crate) DeadlineAtMonotonicSeconds: f64,
    #[pyo3(get)]
    pub(crate) BoundaryMonotonicSampleSeconds: f64,
    #[pyo3(get)]
    pub(crate) NativeRemainingNanoseconds: u64,
    #[pyo3(get)]
    pub(crate) ContextGraphIdentityAvailability: String,
    pub(crate) ContextGraphCanonicalJson: Option<Arc<str>>,
    pub(crate) ContextGraphSha256: Option<Arc<str>>,
    #[pyo3(get)]
    pub(crate) Receipts: Vec<RouteTreeRequestReceiptV1>,
    #[pyo3(get)]
    pub(crate) TotalRequestCount: usize,
    #[pyo3(get)]
    pub(crate) StartedRequestCount: usize,
    #[pyo3(get)]
    pub(crate) SettledReceiptCount: usize,
    #[pyo3(get)]
    pub(crate) FoundCount: usize,
    #[pyo3(get)]
    pub(crate) ProvenNoPathCount: usize,
    #[pyo3(get)]
    pub(crate) IncompleteCount: usize,
    #[pyo3(get)]
    pub(crate) AggregateRouteExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) AggregateProofExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) AggregateExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
}

#[pymethods]
impl RouteTreeRequestReceiptV1 {
    #[getter]
    fn BatchIdentity(&self, PythonValue: Python<'_>) -> PyObject {
        self.BatchIdentity.ToPython(PythonValue)
    }

    #[getter]
    fn RequestId(&self) -> &str {
        &self.RequestId
    }

    #[getter]
    fn ContextGraphSha256(&self) -> Option<&str> {
        self.ContextGraphSha256.as_deref()
    }

    #[getter]
    fn RouteDomainScopeCanonicalJson(&self) -> Option<&str> {
        self.RouteDomainScopeCanonicalJson.as_deref()
    }

    #[getter]
    fn RouteDomainScopeSha256(&self) -> Option<&str> {
        self.RouteDomainScopeSha256.as_deref()
    }

    #[getter]
    fn ImmutableInputCanonicalJson(&self) -> Option<&str> {
        self.ImmutableInputCanonicalJson.as_deref()
    }

    #[getter]
    fn ImmutableInputSha256(&self) -> Option<&str> {
        self.ImmutableInputSha256.as_deref()
    }

    #[getter]
    fn NativePayloadCanonicalJson(&self) -> &str {
        &self.NativePayloadCanonicalJson
    }

    #[getter]
    fn NativePayloadSha256(&self) -> &str {
        &self.NativePayloadSha256
    }

    #[getter]
    fn ReceiptScopeCanonicalJson(&self) -> Option<&str> {
        self.ReceiptScopeCanonicalJson.as_deref()
    }

    #[getter]
    fn ReceiptScopeSha256(&self) -> Option<&str> {
        self.ReceiptScopeSha256.as_deref()
    }

    #[getter]
    fn CallerEchoScopeCanonicalJson(&self) -> Option<&str> {
        self.CallerEchoScopeCanonicalJson.as_deref()
    }

    #[getter]
    fn CallerEchoScopeSha256(&self) -> Option<&str> {
        self.CallerEchoScopeSha256.as_deref()
    }
}

#[pymethods]
impl RouteTreeBatchOutcomesV1 {
    #[getter]
    fn BatchIdentity(&self, PythonValue: Python<'_>) -> PyObject {
        self.BatchIdentity.ToPython(PythonValue)
    }

    #[getter]
    fn ContextGraphCanonicalJson(&self) -> Option<&str> {
        self.ContextGraphCanonicalJson.as_deref()
    }

    #[getter]
    fn ContextGraphSha256(&self) -> Option<&str> {
        self.ContextGraphSha256.as_deref()
    }
}

#[derive(Clone)]
pub(crate) enum AuthoritativeRouteRequestV1 {
    Coarse(Arc<SealedCoarseRequestV1>),
    Detailed(Arc<SealedDetailedRequestV1>),
}

impl RouteTreeCoarseRequestV1 {
    pub(crate) fn AuthoritativeRequest(&self) -> AuthoritativeRouteRequestV1 {
        AuthoritativeRouteRequestV1::Coarse(self.Sealed.clone())
    }
}

impl RouteTreeDetailedRequestV1 {
    pub(crate) fn AuthoritativeRequest(&self) -> AuthoritativeRouteRequestV1 {
        AuthoritativeRouteRequestV1::Detailed(self.Sealed.clone())
    }
}

#[derive(Clone, PartialEq, Eq)]
struct CanonicalRouteRequestV1 {
    RequestKind: &'static str,
    RequestId: Arc<str>,
    CancellationRequestedBeforeStart: bool,
    Starts: Arc<Vec<Position>>,
    TargetBranches: Arc<Vec<Vec<Position>>>,
    AllowedNodes: Arc<Vec<Position>>,
    BlockedNodes: Arc<Vec<Position>>,
    PreferredColumns: Arc<Vec<(i32, i32)>>,
    NodeCosts: Arc<Vec<(Position, i32)>>,
    PreferredRoutingY: i32,
    GuidePenalty: i32,
    BendPenalty: i32,
    ViaPenalty: i32,
    EnforceSignalStrength: bool,
    MaximumExpansionCount: usize,
    RouteDomainScopeCanonicalJson: Arc<str>,
    RouteDomainScopeSha256: Arc<str>,
    CallerEchoScopeCanonicalJson: Arc<str>,
    CallerEchoScopeSha256: Arc<str>,
}

#[derive(Clone, PartialEq, Eq)]
struct PendingIdentityV1 {
    Availability: &'static str,
    CanonicalJson: Option<Arc<str>>,
    Sha256: Option<Arc<str>>,
}

impl PendingIdentityV1 {
    fn Verified(CanonicalJson: Arc<str>, Sha256: Arc<str>) -> Self {
        Self {
            Availability: VERIFIED,
            CanonicalJson: Some(CanonicalJson),
            Sha256: Some(Sha256),
        }
    }

    fn Unavailable(Availability: &'static str) -> Self {
        Self {
            Availability,
            CanonicalJson: None,
            Sha256: None,
        }
    }

    fn IsCoherent(&self) -> bool {
        (self.Availability == VERIFIED) == (self.CanonicalJson.is_some() && self.Sha256.is_some())
            && (self.Availability == VERIFIED
                || (self.CanonicalJson.is_none() && self.Sha256.is_none()))
    }

    fn IsSameSeal(&self, Other: &Self) -> bool {
        self.Availability == Other.Availability
            && match (&self.CanonicalJson, &Other.CanonicalJson) {
                (Some(First), Some(Second)) => Arc::ptr_eq(First, Second),
                (None, None) => true,
                _ => false,
            }
            && match (&self.Sha256, &Other.Sha256) {
                (Some(First), Some(Second)) => Arc::ptr_eq(First, Second),
                (None, None) => true,
                _ => false,
            }
    }
}

struct PendingReceiptV1 {
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    RequestKind: String,
    RequestId: Arc<str>,
    OriginalOrdinal: usize,
    SearchOutcome: String,
    TerminalReason: String,
    Started: bool,
    StartedAtMicroseconds: Option<u64>,
    CompletedAtMicroseconds: Option<u64>,
    MaximumExpansionCount: usize,
    RouteExpansionCount: usize,
    ProofExpansionCount: usize,
    OutcomePhase: &'static str,
    RouteDomainIdentity: PendingIdentityV1,
    ImmutableInputIdentity: PendingIdentityV1,
    CallerEchoIdentity: PendingIdentityV1,
    Candidate: Option<RouteTreeSearchResult>,
    NoPathProof: Option<RouteTreeNoPathProofV1>,
    OriginalRequest: Option<AuthoritativeRouteRequestV1>,
    CanonicalRequest: Option<Arc<CanonicalRouteRequestV1>>,
    ProofReachedNodes: Vec<Position>,
    CancellationRequested: bool,
    CancellationAcknowledged: bool,
    SearchStopped: bool,
    CleanupDisposition: String,
    ProducerFacts: Option<ProducerReceiptFactsV1>,
}

#[derive(Clone)]
struct ProducerReceiptFactsV1 {
    RequestKind: String,
    RequestId: Arc<str>,
    OriginalOrdinal: usize,
    SearchOutcome: String,
    TerminalReason: String,
    Started: bool,
    StartedAtMicroseconds: Option<u64>,
    CompletedAtMicroseconds: Option<u64>,
    MaximumExpansionCount: usize,
    RouteExpansionCount: usize,
    ProofExpansionCount: usize,
    OutcomePhase: &'static str,
    RouteDomainIdentity: PendingIdentityV1,
    OriginalRequest: Option<AuthoritativeRouteRequestV1>,
    CanonicalRequest: Option<Arc<CanonicalRouteRequestV1>>,
    CancellationRequested: bool,
    CancellationAcknowledged: bool,
    SearchStopped: bool,
    CleanupDisposition: String,
}

impl ProducerReceiptFactsV1 {
    fn Capture(Receipt: &PendingReceiptV1) -> Self {
        Self {
            RequestKind: Receipt.RequestKind.clone(),
            RequestId: Receipt.RequestId.clone(),
            OriginalOrdinal: Receipt.OriginalOrdinal,
            SearchOutcome: Receipt.SearchOutcome.clone(),
            TerminalReason: Receipt.TerminalReason.clone(),
            Started: Receipt.Started,
            StartedAtMicroseconds: Receipt.StartedAtMicroseconds,
            CompletedAtMicroseconds: Receipt.CompletedAtMicroseconds,
            MaximumExpansionCount: Receipt.MaximumExpansionCount,
            RouteExpansionCount: Receipt.RouteExpansionCount,
            ProofExpansionCount: Receipt.ProofExpansionCount,
            OutcomePhase: Receipt.OutcomePhase,
            RouteDomainIdentity: Receipt.RouteDomainIdentity.clone(),
            OriginalRequest: Receipt.OriginalRequest.clone(),
            CanonicalRequest: Receipt.CanonicalRequest.clone(),
            CancellationRequested: Receipt.CancellationRequested,
            CancellationAcknowledged: Receipt.CancellationAcknowledged,
            SearchStopped: Receipt.SearchStopped,
            CleanupDisposition: Receipt.CleanupDisposition.clone(),
        }
    }

    fn Matches(&self, Receipt: &PendingReceiptV1) -> bool {
        self.RequestKind == Receipt.RequestKind
            && Arc::ptr_eq(&self.RequestId, &Receipt.RequestId)
            && self.OriginalOrdinal == Receipt.OriginalOrdinal
            && self.SearchOutcome == Receipt.SearchOutcome
            && self.TerminalReason == Receipt.TerminalReason
            && self.Started == Receipt.Started
            && self.StartedAtMicroseconds == Receipt.StartedAtMicroseconds
            && self.CompletedAtMicroseconds == Receipt.CompletedAtMicroseconds
            && self.MaximumExpansionCount == Receipt.MaximumExpansionCount
            && self.RouteExpansionCount == Receipt.RouteExpansionCount
            && self.ProofExpansionCount == Receipt.ProofExpansionCount
            && self.OutcomePhase == Receipt.OutcomePhase
            && self
                .RouteDomainIdentity
                .IsSameSeal(&Receipt.RouteDomainIdentity)
            && self.OriginalRequest.is_some() == Receipt.OriginalRequest.is_some()
            && self.CanonicalRequest.is_some() == Receipt.CanonicalRequest.is_some()
            && self.CancellationRequested == Receipt.CancellationRequested
            && self.CancellationAcknowledged == Receipt.CancellationAcknowledged
            && self.SearchStopped == Receipt.SearchStopped
            && self.CleanupDisposition == Receipt.CleanupDisposition
    }

    fn Restore(&self, Receipt: &mut PendingReceiptV1) {
        Receipt.RequestKind = self.RequestKind.clone();
        Receipt.RequestId = self.RequestId.clone();
        Receipt.OriginalOrdinal = self.OriginalOrdinal;
        Receipt.SearchOutcome = self.SearchOutcome.clone();
        Receipt.TerminalReason = self.TerminalReason.clone();
        Receipt.Started = self.Started;
        Receipt.StartedAtMicroseconds = self.StartedAtMicroseconds;
        Receipt.CompletedAtMicroseconds = self.CompletedAtMicroseconds;
        Receipt.MaximumExpansionCount = self.MaximumExpansionCount;
        Receipt.RouteExpansionCount = self.RouteExpansionCount;
        Receipt.ProofExpansionCount = self.ProofExpansionCount;
        Receipt.OutcomePhase = self.OutcomePhase;
        Receipt.RouteDomainIdentity = self.RouteDomainIdentity.clone();
        Receipt.OriginalRequest = self.OriginalRequest.clone();
        Receipt.CanonicalRequest = self.CanonicalRequest.clone();
        Receipt.CancellationRequested = self.CancellationRequested;
        Receipt.CancellationAcknowledged = self.CancellationAcknowledged;
        Receipt.SearchStopped = self.SearchStopped;
        Receipt.CleanupDisposition = self.CleanupDisposition.clone();
    }
}

impl PendingReceiptV1 {
    fn SealProducerFacts(&mut self) {
        debug_assert!(self.ProducerFacts.is_none());
        self.ProducerFacts = Some(ProducerReceiptFactsV1::Capture(self));
    }
}

pub(crate) struct PendingBatchOutcomesV1 {
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    DeadlineAtMonotonicSeconds: f64,
    BoundaryMonotonicSampleSeconds: f64,
    NativeRemainingNanoseconds: u64,
    ContextGraphCanonicalJson: Arc<str>,
    ContextGraphSha256: Arc<str>,
    Receipts: Vec<PendingReceiptV1>,
    Deadline: RuntimeDeadline,
}

enum RelaxedConnectivityResultV1 {
    Disconnected(RouteTreeNoPathProofV1, Vec<Position>),
    Connected,
    WorkCapExhausted,
    DeadlineExhausted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RequestNormalizationErrorV1 {
    Unsupported,
    DeadlineExhausted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum FinalValidationResultV1 {
    Valid,
    Invalid,
    DeadlineExhausted,
}

fn SortedUniqueWithDeadline<T: Clone + Ord>(
    Values: &[T],
    Deadline: &RuntimeDeadline,
) -> Result<Vec<T>, RequestNormalizationErrorV1> {
    let CheckDeadline = Values.len() >= DEADLINE_CHECK_INTERVAL;
    if CheckDeadline && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    let mut Ordered = BTreeSet::new();
    for (Index, Value) in Values.iter().enumerate() {
        if CheckDeadline && Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        Ordered.insert(Value.clone());
    }
    let mut Result = Vec::with_capacity(Ordered.len());
    for (Index, Value) in Ordered.into_iter().enumerate() {
        if CheckDeadline && Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        Result.push(Value);
    }
    if CheckDeadline && Deadline.Check() {
        Err(RequestNormalizationErrorV1::DeadlineExhausted)
    } else {
        Ok(Result)
    }
}

fn NormalizedNodeCostsWithDeadline(
    Values: &[(Position, i32)],
    Deadline: &RuntimeDeadline,
) -> Result<Vec<(Position, i32)>, RequestNormalizationErrorV1> {
    let CheckDeadline = Values.len() >= DEADLINE_CHECK_INTERVAL;
    if CheckDeadline && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    let mut Costs = BTreeMap::new();
    for (Index, (PositionValue, Cost)) in Values.iter().enumerate() {
        if CheckDeadline && Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        Costs.insert(*PositionValue, *Cost);
    }
    let mut Result = Vec::with_capacity(Costs.len());
    for (Index, Value) in Costs.into_iter().enumerate() {
        if CheckDeadline && Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        Result.push(Value);
    }
    if CheckDeadline && Deadline.Check() {
        Err(RequestNormalizationErrorV1::DeadlineExhausted)
    } else {
        Ok(Result)
    }
}

struct DeadlineJsonWriter<'a> {
    Bytes: Vec<u8>,
    Hasher: Sha256,
    Deadline: &'a RuntimeDeadline,
    CheckDeadline: bool,
}

impl Write for DeadlineJsonWriter<'_> {
    fn write(&mut self, Buffer: &[u8]) -> std::io::Result<usize> {
        for Chunk in Buffer.chunks(DEADLINE_CHECK_INTERVAL.max(1)) {
            if self.CheckDeadline && self.Deadline.Check() {
                return Err(IoError::new(
                    IoErrorKind::TimedOut,
                    "routing deadline expired",
                ));
            }
            self.Bytes.extend_from_slice(Chunk);
            self.Hasher.update(Chunk);
        }
        Ok(Buffer.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn CanonicalJsonAndShaWithDeadline<T: Serialize>(
    Value: &T,
    Deadline: &RuntimeDeadline,
    CheckDeadline: bool,
) -> Result<(Arc<str>, Arc<str>), RequestNormalizationErrorV1> {
    if CheckDeadline && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    let mut Writer = DeadlineJsonWriter {
        Bytes: Vec::new(),
        Hasher: Sha256::new(),
        Deadline,
        CheckDeadline,
    };
    serde_json::to_writer(&mut Writer, Value)
        .map_err(|_Reason| RequestNormalizationErrorV1::DeadlineExhausted)?;
    if CheckDeadline && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    let Canonical =
        String::from_utf8(Writer.Bytes).expect("serde_json produces valid UTF-8 canonical bytes");
    let Digest = format!("{:x}", Writer.Hasher.finalize());
    Ok((Arc::from(Canonical), Arc::from(Digest)))
}

fn CallerEchoCanonicalJson(
    Bindings: &[(String, String)],
    DeclaredBounds: Bounds3d,
    DeclaredPlacementBounds: Bounds2d,
) -> Result<(Vec<(String, String)>, String), &'static str> {
    let mut Values = Bindings.to_vec();
    Values.sort();
    if Values
        .iter()
        .any(|(Name, Value)| Name.is_empty() || Value.is_empty())
        || Values.windows(2).any(|Pair| Pair[0].0 == Pair[1].0)
        || Values
            .iter()
            .map(|Value| Value.0.as_str())
            .collect::<Vec<_>>()
            != REQUIRED_CALLER_BINDINGS
    {
        return Err("caller echo bindings must contain each required identity exactly once");
    }
    let Canonical = serde_json::to_string(&json!([
        "native-route-caller-echo-scope-v1",
        Values,
        DeclaredBounds,
        DeclaredPlacementBounds,
    ]))
    .expect("caller scope JSON values are serializable");
    Ok((Values, Canonical))
}

#[allow(clippy::too_many_arguments)]
fn SealCoarseRequestV1(
    RequestId: &str,
    CallerEchoBindings: &[(String, String)],
    DeclaredBounds: Bounds3d,
    DeclaredPlacementBounds: Bounds2d,
    CancellationRequestedBeforeStart: bool,
    Starts: &[Position],
    TargetBranches: &[Vec<Position>],
    AllowedColumns: &[(i32, i32)],
    RequiredNodes: &[Position],
    BlockedNodeValues: &[Position],
    PreferredColumns: &[(i32, i32)],
    PreferredRoutingY: i32,
    GuidePenalty: i32,
    BendPenalty: i32,
    ViaPenalty: i32,
    MaximumExpansionCount: usize,
) -> Arc<SealedCoarseRequestV1> {
    let CanonicalAllowedColumns: Vec<_> = AllowedColumns
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalRequiredNodes: Vec<_> = RequiredNodes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalBlockedNodes: Vec<_> = BlockedNodeValues
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalPreferredColumns: Vec<_> = PreferredColumns
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let NativePayloadCanonicalJson = serde_json::to_string(&json!([
        "native-coarse-route-request-payload-v1",
        Starts,
        TargetBranches,
        CanonicalAllowedColumns,
        CanonicalRequiredNodes,
        CanonicalBlockedNodes,
        CanonicalPreferredColumns,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        MaximumExpansionCount,
        CancellationRequestedBeforeStart,
    ]))
    .expect("native coarse payload JSON values are serializable");
    let ImmutableInputCanonicalJson = serde_json::to_string(&json!([
        "raw-native-coarse-route-request-v1",
        RequestId,
        Starts,
        TargetBranches,
        AllowedColumns,
        RequiredNodes,
        BlockedNodeValues,
        PreferredColumns,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        MaximumExpansionCount,
        CancellationRequestedBeforeStart,
        CallerEchoBindings,
        DeclaredBounds,
        DeclaredPlacementBounds,
    ]))
    .expect("raw request JSON values are serializable");
    let RawCallerEchoScopeCanonicalJson = serde_json::to_string(&json!([
        "raw-native-route-caller-echo-scope-v1",
        CallerEchoBindings,
        DeclaredBounds,
        DeclaredPlacementBounds,
    ]))
    .expect("raw caller echo JSON values are serializable");
    let CanonicalCaller =
        CallerEchoCanonicalJson(CallerEchoBindings, DeclaredBounds, DeclaredPlacementBounds)
            .ok()
            .map(|(_Values, Canonical)| {
                let Digest = NativeSha256(&Canonical);
                (Arc::<str>::from(Canonical), Arc::<str>::from(Digest))
            });
    Arc::new(SealedCoarseRequestV1 {
        RequestId: Arc::from(RequestId),
        CallerEchoBindings: Arc::new(CallerEchoBindings.to_vec()),
        CancellationRequestedBeforeStart,
        Starts: Arc::new(Starts.to_vec()),
        TargetBranches: Arc::new(TargetBranches.to_vec()),
        AllowedColumns: Arc::new(AllowedColumns.to_vec()),
        RequiredNodes: Arc::new(RequiredNodes.to_vec()),
        BlockedNodeValues: Arc::new(BlockedNodeValues.to_vec()),
        PreferredColumns: Arc::new(PreferredColumns.to_vec()),
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        MaximumExpansionCount,
        NativePayloadSha256: Arc::from(NativeSha256(&NativePayloadCanonicalJson)),
        NativePayloadCanonicalJson: Arc::from(NativePayloadCanonicalJson),
        ImmutableInputSha256: Arc::from(NativeSha256(&ImmutableInputCanonicalJson)),
        ImmutableInputCanonicalJson: Arc::from(ImmutableInputCanonicalJson),
        RawCallerEchoScopeSha256: Arc::from(NativeSha256(&RawCallerEchoScopeCanonicalJson)),
        RawCallerEchoScopeCanonicalJson: Arc::from(RawCallerEchoScopeCanonicalJson),
        CanonicalCallerEchoScopeCanonicalJson: CanonicalCaller
            .as_ref()
            .map(|Value| Value.0.clone()),
        CanonicalCallerEchoScopeSha256: CanonicalCaller.map(|Value| Value.1),
    })
}

#[allow(clippy::too_many_arguments)]
fn SealDetailedRequestV1(
    RequestId: &str,
    CallerEchoBindings: &[(String, String)],
    DeclaredBounds: Bounds3d,
    DeclaredPlacementBounds: Bounds2d,
    CancellationRequestedBeforeStart: bool,
    Starts: &[Position],
    TargetBranches: &[Vec<Position>],
    AllowedNodeValues: &[Position],
    BlockedNodeValues: &[Position],
    PreferredColumns: &[(i32, i32)],
    NodeCostValues: &[(Position, i32)],
    PreferredRoutingY: i32,
    GuidePenalty: i32,
    BendPenalty: i32,
    ViaPenalty: i32,
    EnforceSignalStrength: bool,
    MaximumExpansionCount: usize,
) -> Arc<SealedDetailedRequestV1> {
    let CanonicalAllowedNodes: Vec<_> = AllowedNodeValues
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalBlockedNodes: Vec<_> = BlockedNodeValues
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalPreferredColumns: Vec<_> = PreferredColumns
        .iter()
        .copied()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    let CanonicalNodeCosts: Vec<_> = NodeCostValues
        .iter()
        .copied()
        .collect::<BTreeMap<_, _>>()
        .into_iter()
        .collect();
    let NativePayloadCanonicalJson = serde_json::to_string(&json!([
        "native-detailed-route-request-payload-v1",
        Starts,
        TargetBranches,
        CanonicalAllowedNodes,
        CanonicalBlockedNodes,
        CanonicalPreferredColumns,
        CanonicalNodeCosts,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
        CancellationRequestedBeforeStart,
    ]))
    .expect("native detailed payload JSON values are serializable");
    let ImmutableInputCanonicalJson = serde_json::to_string(&json!([
        "raw-native-detailed-route-request-v1",
        RequestId,
        Starts,
        TargetBranches,
        AllowedNodeValues,
        BlockedNodeValues,
        PreferredColumns,
        NodeCostValues,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
        CancellationRequestedBeforeStart,
        CallerEchoBindings,
        DeclaredBounds,
        DeclaredPlacementBounds,
    ]))
    .expect("raw request JSON values are serializable");
    let RawCallerEchoScopeCanonicalJson = serde_json::to_string(&json!([
        "raw-native-route-caller-echo-scope-v1",
        CallerEchoBindings,
        DeclaredBounds,
        DeclaredPlacementBounds,
    ]))
    .expect("raw caller echo JSON values are serializable");
    let CanonicalCaller =
        CallerEchoCanonicalJson(CallerEchoBindings, DeclaredBounds, DeclaredPlacementBounds)
            .ok()
            .map(|(_Values, Canonical)| {
                let Digest = NativeSha256(&Canonical);
                (Arc::<str>::from(Canonical), Arc::<str>::from(Digest))
            });
    Arc::new(SealedDetailedRequestV1 {
        RequestId: Arc::from(RequestId),
        CallerEchoBindings: Arc::new(CallerEchoBindings.to_vec()),
        CancellationRequestedBeforeStart,
        Starts: Arc::new(Starts.to_vec()),
        TargetBranches: Arc::new(TargetBranches.to_vec()),
        AllowedNodeValues: Arc::new(AllowedNodeValues.to_vec()),
        BlockedNodeValues: Arc::new(BlockedNodeValues.to_vec()),
        PreferredColumns: Arc::new(PreferredColumns.to_vec()),
        NodeCostValues: Arc::new(NodeCostValues.to_vec()),
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
        NativePayloadSha256: Arc::from(NativeSha256(&NativePayloadCanonicalJson)),
        NativePayloadCanonicalJson: Arc::from(NativePayloadCanonicalJson),
        ImmutableInputSha256: Arc::from(NativeSha256(&ImmutableInputCanonicalJson)),
        ImmutableInputCanonicalJson: Arc::from(ImmutableInputCanonicalJson),
        RawCallerEchoScopeSha256: Arc::from(NativeSha256(&RawCallerEchoScopeCanonicalJson)),
        RawCallerEchoScopeCanonicalJson: Arc::from(RawCallerEchoScopeCanonicalJson),
        CanonicalCallerEchoScopeCanonicalJson: CanonicalCaller
            .as_ref()
            .map(|Value| Value.0.clone()),
        CanonicalCallerEchoScopeSha256: CanonicalCaller.map(|Value| Value.1),
    })
}

#[cfg(test)]
fn ContextCanonicalJson(Context: &RoutingContext) -> Arc<str> {
    Context.AuthoritativeIdentityV1.CanonicalJson.clone()
}

fn ImmutableInputCanonicalJson(Request: &AuthoritativeRouteRequestV1) -> Arc<str> {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.ImmutableInputCanonicalJson.clone(),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.ImmutableInputCanonicalJson.clone(),
    }
}

fn ImmutableInputSha256(Request: &AuthoritativeRouteRequestV1) -> Arc<str> {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.ImmutableInputSha256.clone(),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.ImmutableInputSha256.clone(),
    }
}

fn NativePayloadCanonicalJson(Request: &AuthoritativeRouteRequestV1) -> Arc<str> {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.NativePayloadCanonicalJson.clone(),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.NativePayloadCanonicalJson.clone(),
    }
}

fn NativePayloadSha256(Request: &AuthoritativeRouteRequestV1) -> Arc<str> {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.NativePayloadSha256.clone(),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.NativePayloadSha256.clone(),
    }
}

fn SealedCallerEchoIdentity(Request: &AuthoritativeRouteRequestV1) -> PendingIdentityV1 {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => match (
            Value.CanonicalCallerEchoScopeCanonicalJson.clone(),
            Value.CanonicalCallerEchoScopeSha256.clone(),
        ) {
            (Some(Canonical), Some(Sha256)) => PendingIdentityV1::Verified(Canonical, Sha256),
            _ => PendingIdentityV1::Verified(
                Value.RawCallerEchoScopeCanonicalJson.clone(),
                Value.RawCallerEchoScopeSha256.clone(),
            ),
        },
        AuthoritativeRouteRequestV1::Detailed(Value) => match (
            Value.CanonicalCallerEchoScopeCanonicalJson.clone(),
            Value.CanonicalCallerEchoScopeSha256.clone(),
        ) {
            (Some(Canonical), Some(Sha256)) => PendingIdentityV1::Verified(Canonical, Sha256),
            _ => PendingIdentityV1::Verified(
                Value.RawCallerEchoScopeCanonicalJson.clone(),
                Value.RawCallerEchoScopeSha256.clone(),
            ),
        },
    }
}

fn ValidateBranchEdgesWithDeadline(
    Context: &RoutingContext,
    TargetBranches: &[Vec<Position>],
    Deadline: &RuntimeDeadline,
    ValidationIndex: &mut usize,
) -> Result<(), RequestNormalizationErrorV1> {
    if Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    for Branch in TargetBranches {
        *ValidationIndex = ValidationIndex
            .checked_add(1)
            .ok_or(RequestNormalizationErrorV1::Unsupported)?;
        if *ValidationIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        for Pair in Branch.windows(2) {
            *ValidationIndex = ValidationIndex
                .checked_add(1)
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            if *ValidationIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Err(RequestNormalizationErrorV1::DeadlineExhausted);
            }
            match CandidateEdgeIsValidWithDeadline(
                Context,
                Pair[0],
                Pair[1],
                Deadline,
                ValidationIndex,
            ) {
                Ok(true) => {}
                Ok(false)
                | Err(FinalValidationResultV1::Invalid)
                | Err(FinalValidationResultV1::Valid) => {
                    return Err(RequestNormalizationErrorV1::Unsupported);
                }
                Err(FinalValidationResultV1::DeadlineExhausted) => {
                    return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                }
            }
        }
    }
    if Deadline.Check() {
        Err(RequestNormalizationErrorV1::DeadlineExhausted)
    } else {
        Ok(())
    }
}

fn NormalizeRequest(
    Context: &RoutingContext,
    Request: AuthoritativeRouteRequestV1,
    Deadline: &RuntimeDeadline,
) -> Result<CanonicalRouteRequestV1, RequestNormalizationErrorV1> {
    let Branches = match &Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => &Value.TargetBranches,
        AuthoritativeRouteRequestV1::Detailed(Value) => &Value.TargetBranches,
    };
    let mut BranchNodeCount = 0usize;
    for (Index, Branch) in Branches.iter().enumerate() {
        if Branches.len() >= DEADLINE_CHECK_INTERVAL
            && Index > 0
            && Index % DEADLINE_CHECK_INTERVAL == 0
            && Deadline.Check()
        {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        BranchNodeCount = BranchNodeCount
            .checked_add(Branch.len())
            .ok_or(RequestNormalizationErrorV1::Unsupported)?;
    }
    let ValidationItemCount = match &Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value
            .Starts
            .len()
            .checked_add(BranchNodeCount)
            .and_then(|Count| Count.checked_add(Value.AllowedColumns.len()))
            .and_then(|Count| Count.checked_add(Value.RequiredNodes.len()))
            .and_then(|Count| Count.checked_add(Value.BlockedNodeValues.len()))
            .and_then(|Count| Count.checked_add(Value.PreferredColumns.len()))
            .and_then(|Count| Count.checked_add(Value.CallerEchoBindings.len())),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value
            .Starts
            .len()
            .checked_add(BranchNodeCount)
            .and_then(|Count| Count.checked_add(Value.AllowedNodeValues.len()))
            .and_then(|Count| Count.checked_add(Value.BlockedNodeValues.len()))
            .and_then(|Count| Count.checked_add(Value.PreferredColumns.len()))
            .and_then(|Count| Count.checked_add(Value.NodeCostValues.len()))
            .and_then(|Count| Count.checked_add(Value.CallerEchoBindings.len())),
    }
    .ok_or(RequestNormalizationErrorV1::Unsupported)?;
    if ValidationItemCount >= DEADLINE_CHECK_INTERVAL && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    let (
        RequestKind,
        RequestId,
        CancellationRequestedBeforeStart,
        Starts,
        TargetBranches,
        AllowedNodes,
        BlockedNodes,
        PreferredColumns,
        NodeCosts,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
        CallerEchoScopeCanonicalJson,
        CallerEchoScopeSha256,
    ) = match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => {
            let AllowedColumns = Arc::new(SortedUniqueWithDeadline(
                Value.AllowedColumns.as_ref(),
                Deadline,
            )?);
            let RequiredNodes = Arc::new(SortedUniqueWithDeadline(
                Value.RequiredNodes.as_ref(),
                Deadline,
            )?);
            for (Index, PositionValue) in RequiredNodes.iter().enumerate() {
                if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                }
                if !Context.Adjacency.contains_key(PositionValue) {
                    return Err(RequestNormalizationErrorV1::Unsupported);
                }
            }
            let mut AllowedNodeSet = BTreeSet::new();
            let mut VisitedNodeCount = 0usize;
            for (Index, Column) in AllowedColumns.iter().enumerate() {
                if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                }
                if let Some(Positions) = Context.NodesByColumn.get(Column) {
                    for PositionValue in Positions {
                        VisitedNodeCount = VisitedNodeCount
                            .checked_add(1)
                            .ok_or(RequestNormalizationErrorV1::Unsupported)?;
                        if VisitedNodeCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                        }
                        AllowedNodeSet.insert(*PositionValue);
                    }
                }
            }
            for (Index, PositionValue) in RequiredNodes.iter().copied().enumerate() {
                if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                }
                AllowedNodeSet.insert(PositionValue);
            }
            let mut AllowedNodes = Vec::with_capacity(AllowedNodeSet.len());
            for (Index, PositionValue) in AllowedNodeSet.into_iter().enumerate() {
                if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Err(RequestNormalizationErrorV1::DeadlineExhausted);
                }
                AllowedNodes.push(PositionValue);
            }
            let CallerEchoScopeCanonicalJson = Value
                .CanonicalCallerEchoScopeCanonicalJson
                .clone()
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            let CallerEchoScopeSha256 = Value
                .CanonicalCallerEchoScopeSha256
                .clone()
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            (
                COARSE_REQUEST_KIND,
                Value.RequestId.clone(),
                Value.CancellationRequestedBeforeStart,
                Value.Starts.clone(),
                Value.TargetBranches.clone(),
                Arc::new(AllowedNodes),
                Arc::new(SortedUniqueWithDeadline(
                    Value.BlockedNodeValues.as_ref(),
                    Deadline,
                )?),
                Arc::new(SortedUniqueWithDeadline(
                    Value.PreferredColumns.as_ref(),
                    Deadline,
                )?),
                Arc::new(Vec::new()),
                Value.PreferredRoutingY,
                Value.GuidePenalty,
                Value.BendPenalty,
                Value.ViaPenalty,
                false,
                Value.MaximumExpansionCount,
                CallerEchoScopeCanonicalJson,
                CallerEchoScopeSha256,
            )
        }
        AuthoritativeRouteRequestV1::Detailed(Value) => {
            let CallerEchoScopeCanonicalJson = Value
                .CanonicalCallerEchoScopeCanonicalJson
                .clone()
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            let CallerEchoScopeSha256 = Value
                .CanonicalCallerEchoScopeSha256
                .clone()
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            (
                DETAILED_REQUEST_KIND,
                Value.RequestId.clone(),
                Value.CancellationRequestedBeforeStart,
                Value.Starts.clone(),
                Value.TargetBranches.clone(),
                Arc::new(SortedUniqueWithDeadline(
                    Value.AllowedNodeValues.as_ref(),
                    Deadline,
                )?),
                Arc::new(SortedUniqueWithDeadline(
                    Value.BlockedNodeValues.as_ref(),
                    Deadline,
                )?),
                Arc::new(SortedUniqueWithDeadline(
                    Value.PreferredColumns.as_ref(),
                    Deadline,
                )?),
                Arc::new(NormalizedNodeCostsWithDeadline(
                    Value.NodeCostValues.as_ref(),
                    Deadline,
                )?),
                Value.PreferredRoutingY,
                Value.GuidePenalty,
                Value.BendPenalty,
                Value.ViaPenalty,
                Value.EnforceSignalStrength,
                Value.MaximumExpansionCount,
                CallerEchoScopeCanonicalJson,
                CallerEchoScopeSha256,
            )
        }
    };

    if RequestId.is_empty() {
        return Err(RequestNormalizationErrorV1::Unsupported);
    }
    if ValidationItemCount >= DEADLINE_CHECK_INTERVAL && Deadline.Check() {
        return Err(RequestNormalizationErrorV1::DeadlineExhausted);
    }
    if Starts.is_empty() || TargetBranches.is_empty() {
        return Err(RequestNormalizationErrorV1::Unsupported);
    }
    let PositionIsAllowed = |PositionValue: &Position| {
        Context.Adjacency.contains_key(PositionValue)
            && AllowedNodes.binary_search(PositionValue).is_ok()
            && BlockedNodes.binary_search(PositionValue).is_err()
    };
    let mut ValidationIndex = 0usize;
    for Start in Starts.iter() {
        ValidationIndex = ValidationIndex
            .checked_add(1)
            .ok_or(RequestNormalizationErrorV1::Unsupported)?;
        if ValidationIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        if !PositionIsAllowed(Start) {
            return Err(RequestNormalizationErrorV1::Unsupported);
        }
    }
    for Branch in TargetBranches.iter() {
        if Branch.is_empty() {
            return Err(RequestNormalizationErrorV1::Unsupported);
        }
        for PositionValue in Branch {
            ValidationIndex = ValidationIndex
                .checked_add(1)
                .ok_or(RequestNormalizationErrorV1::Unsupported)?;
            if ValidationIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Err(RequestNormalizationErrorV1::DeadlineExhausted);
            }
            if !PositionIsAllowed(PositionValue) {
                return Err(RequestNormalizationErrorV1::Unsupported);
            }
        }
    }
    ValidateBranchEdgesWithDeadline(
        Context,
        TargetBranches.as_ref(),
        Deadline,
        &mut ValidationIndex,
    )?;
    let mut BranchRoles = Vec::with_capacity(TargetBranches.len());
    for (Index, Branch) in TargetBranches.iter().enumerate() {
        if Index > 0 && Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Err(RequestNormalizationErrorV1::DeadlineExhausted);
        }
        BranchRoles.push((
            Branch[0],
            *Branch.last().expect("validated nonempty branch"),
        ));
    }
    let DomainDocument = (
        "native-route-domain-scope-v1",
        Starts.as_ref(),
        TargetBranches.as_ref(),
        &BranchRoles,
        AllowedNodes.as_ref(),
        BlockedNodes.as_ref(),
        PreferredColumns.as_ref(),
        NodeCosts.as_ref(),
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
    );
    let (RouteDomainScopeCanonicalJson, RouteDomainScopeSha256) = CanonicalJsonAndShaWithDeadline(
        &DomainDocument,
        Deadline,
        ValidationItemCount >= DEADLINE_CHECK_INTERVAL,
    )?;
    Ok(CanonicalRouteRequestV1 {
        RequestKind,
        RequestId,
        CancellationRequestedBeforeStart,
        Starts,
        TargetBranches,
        AllowedNodes,
        BlockedNodes,
        PreferredColumns,
        NodeCosts,
        PreferredRoutingY,
        GuidePenalty,
        BendPenalty,
        ViaPenalty,
        EnforceSignalStrength,
        MaximumExpansionCount,
        RouteDomainScopeCanonicalJson,
        RouteDomainScopeSha256,
        CallerEchoScopeCanonicalJson,
        CallerEchoScopeSha256,
    })
}

fn ElapsedMicroseconds(Started: Instant) -> u64 {
    Started.elapsed().as_micros().min(u128::from(u64::MAX)) as u64
}

fn EmptyReceipt(
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    RequestKind: &str,
    RequestId: Arc<str>,
    OriginalOrdinal: usize,
    MaximumExpansionCount: usize,
    TerminalReason: &str,
    OutcomePhase: &'static str,
    RouteDomainIdentity: PendingIdentityV1,
    ImmutableInputIdentity: PendingIdentityV1,
    CallerEchoIdentity: PendingIdentityV1,
) -> PendingReceiptV1 {
    PendingReceiptV1 {
        BatchIdentity,
        RequestKind: RequestKind.to_string(),
        RequestId,
        OriginalOrdinal,
        SearchOutcome: "Incomplete".to_string(),
        TerminalReason: TerminalReason.to_string(),
        Started: false,
        StartedAtMicroseconds: None,
        CompletedAtMicroseconds: None,
        MaximumExpansionCount,
        RouteExpansionCount: 0,
        ProofExpansionCount: 0,
        OutcomePhase,
        RouteDomainIdentity,
        ImmutableInputIdentity,
        CallerEchoIdentity,
        Candidate: None,
        NoPathProof: None,
        OriginalRequest: None,
        CanonicalRequest: None,
        ProofReachedNodes: Vec::new(),
        CancellationRequested: false,
        CancellationAcknowledged: false,
        SearchStopped: false,
        CleanupDisposition: "NotApplicableNoProcess".to_string(),
        ProducerFacts: None,
    }
}

#[cfg(test)]
fn ValidateFoundCandidate(
    Context: &RoutingContext,
    Request: &CanonicalRouteRequestV1,
    Result: &RouteTreeSearchResult,
) -> bool {
    matches!(
        ValidateFoundCandidateWithDeadline(
            Context,
            Request,
            Result,
            Result.ExpansionCount,
            &RuntimeDeadline::Unlimited(),
        ),
        FinalValidationResultV1::Valid
    )
}

fn AdvanceValidationStep(
    Steps: &mut usize,
    Deadline: &RuntimeDeadline,
) -> Result<(), FinalValidationResultV1> {
    *Steps = Steps
        .checked_add(1)
        .ok_or(FinalValidationResultV1::Invalid)?;
    if *Steps % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
        Err(FinalValidationResultV1::DeadlineExhausted)
    } else {
        Ok(())
    }
}

fn CandidateEdgeIsValidWithDeadline(
    Context: &RoutingContext,
    First: Position,
    Second: Position,
    Deadline: &RuntimeDeadline,
    Steps: &mut usize,
) -> Result<bool, FinalValidationResultV1> {
    if Deadline.Check() {
        return Err(FinalValidationResultV1::DeadlineExhausted);
    }
    for Neighbor in Context.Adjacency.get(&First).into_iter().flatten() {
        AdvanceValidationStep(Steps, Deadline)?;
        if *Neighbor == Second {
            return Ok(true);
        }
    }
    for Neighbor in Context.Adjacency.get(&Second).into_iter().flatten() {
        AdvanceValidationStep(Steps, Deadline)?;
        if *Neighbor == First {
            return Ok(true);
        }
    }
    Ok(false)
}

fn ValidateFoundCandidateWithDeadline(
    Context: &RoutingContext,
    Request: &CanonicalRouteRequestV1,
    Result: &RouteTreeSearchResult,
    ExpectedRouteExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) -> FinalValidationResultV1 {
    if Deadline.Check() {
        return FinalValidationResultV1::DeadlineExhausted;
    }
    if !Result.IsRouted
        || Result.IsBudgetExpired
        || Result.Status != "Routed"
        || !Result.NoPathReason.is_empty()
        || Result.Nodes.is_empty()
        || Result.TargetPaths.len() != Request.TargetBranches.len()
        || !Result.ConflictResources.is_empty()
        || Result.ExpansionCount != ExpectedRouteExpansionCount
    {
        return FinalValidationResultV1::Invalid;
    }
    let mut ValidationSteps = 0usize;
    macro_rules! ValidateStep {
        () => {
            if let Err(Result) = AdvanceValidationStep(&mut ValidationSteps, Deadline) {
                return Result;
            }
        };
    }
    let mut Allowed = HashSet::with_capacity(Request.AllowedNodes.len());
    for Value in Request.AllowedNodes.iter().copied() {
        ValidateStep!();
        Allowed.insert(Value);
    }
    let mut Blocked = HashSet::with_capacity(Request.BlockedNodes.len());
    for Value in Request.BlockedNodes.iter().copied() {
        ValidateStep!();
        Blocked.insert(Value);
    }
    let mut NodeSet = HashSet::with_capacity(Result.Nodes.len());
    for Value in Result.Nodes.iter().copied() {
        ValidateStep!();
        if !NodeSet.insert(Value) || !Allowed.contains(&Value) || Blocked.contains(&Value) {
            return FinalValidationResultV1::Invalid;
        }
    }
    let mut StartSet = HashSet::with_capacity(Request.Starts.len());
    for Value in Request.Starts.iter().copied() {
        ValidateStep!();
        StartSet.insert(Value);
    }
    for Branch in Request.TargetBranches.iter() {
        for Value in Branch {
            ValidateStep!();
            if !NodeSet.contains(Value) {
                return FinalValidationResultV1::Invalid;
            }
        }
    }
    let mut MatchedBranches = vec![false; Request.TargetBranches.len()];
    let mut PathNodes = HashSet::with_capacity(Result.Nodes.len());
    for (Target, Path) in &Result.TargetPaths {
        if Path.is_empty()
            || !Path.first().is_some_and(|Start| StartSet.contains(Start))
            || Path.last().copied() != Some(*Target)
        {
            return FinalValidationResultV1::Invalid;
        }
        let mut UniquePathNodes = HashSet::with_capacity(Path.len());
        for Value in Path.iter().copied() {
            ValidateStep!();
            if !UniquePathNodes.insert(Value)
                || !NodeSet.contains(&Value)
                || !Allowed.contains(&Value)
                || Blocked.contains(&Value)
            {
                return FinalValidationResultV1::Invalid;
            }
            PathNodes.insert(Value);
        }
        for Pair in Path.windows(2) {
            ValidateStep!();
            match CandidateEdgeIsValidWithDeadline(
                Context,
                Pair[0],
                Pair[1],
                Deadline,
                &mut ValidationSteps,
            ) {
                Ok(true) => {}
                Ok(false) => return FinalValidationResultV1::Invalid,
                Err(Result) => return Result,
            }
        }
        let mut BranchIndex = None;
        for (Index, Branch) in Request.TargetBranches.iter().enumerate() {
            ValidateStep!();
            if MatchedBranches[Index]
                || Branch.last().copied() != Some(*Target)
                || Path.len() < Branch.len()
            {
                continue;
            }
            let Offset = Path.len() - Branch.len();
            let mut Matches = true;
            for (PathValue, BranchValue) in Path[Offset..].iter().zip(Branch.iter()) {
                ValidateStep!();
                if PathValue != BranchValue {
                    Matches = false;
                    break;
                }
            }
            if Matches {
                BranchIndex = Some(Index);
                break;
            }
        }
        let Some(BranchIndex) = BranchIndex else {
            return FinalValidationResultV1::Invalid;
        };
        MatchedBranches[BranchIndex] = true;
    }
    for Matched in &MatchedBranches {
        ValidateStep!();
        if !*Matched {
            return FinalValidationResultV1::Invalid;
        }
    }
    if PathNodes.len() != NodeSet.len() {
        return FinalValidationResultV1::Invalid;
    }
    for Value in &NodeSet {
        ValidateStep!();
        if !PathNodes.contains(Value) {
            return FinalValidationResultV1::Invalid;
        }
    }
    let mut BoundaryFrontierNodes = HashSet::with_capacity(Result.BoundaryFrontierNodes.len());
    for Value in Result.BoundaryFrontierNodes.iter().copied() {
        ValidateStep!();
        if !BoundaryFrontierNodes.insert(Value) || !NodeSet.contains(&Value) {
            return FinalValidationResultV1::Invalid;
        }
    }

    let mut CandidateRoot = None;
    for Value in Request.Starts.iter().copied() {
        ValidateStep!();
        if CandidateRoot.is_none() && NodeSet.contains(&Value) {
            CandidateRoot = Some(Value);
        }
    }
    let Some(CandidateRoot) = CandidateRoot else {
        return FinalValidationResultV1::Invalid;
    };
    let mut Reached = HashSet::from([CandidateRoot]);
    let mut Pending = VecDeque::from([CandidateRoot]);
    while let Some(Current) = Pending.pop_front() {
        ValidateStep!();
        for Neighbor in Context.Adjacency.get(&Current).into_iter().flatten() {
            ValidateStep!();
            if NodeSet.contains(Neighbor) && Reached.insert(*Neighbor) {
                Pending.push_back(*Neighbor);
            }
        }
    }
    if Reached.len() != NodeSet.len() {
        return FinalValidationResultV1::Invalid;
    }
    for Value in &NodeSet {
        ValidateStep!();
        if !Reached.contains(Value) {
            return FinalValidationResultV1::Invalid;
        }
    }

    let mut RepeaterMap = HashMap::with_capacity(Result.RepeaterReservations.len());
    for (PositionValue, Facing) in &Result.RepeaterReservations {
        ValidateStep!();
        if !NodeSet.contains(PositionValue)
            || !matches!(Facing.as_str(), "west" | "east" | "north" | "south")
        {
            return FinalValidationResultV1::Invalid;
        }
        if RepeaterMap.insert(*PositionValue, Facing.clone()).is_some() {
            return FinalValidationResultV1::Invalid;
        }
    }
    if !Request.EnforceSignalStrength && !RepeaterMap.is_empty() {
        return FinalValidationResultV1::Invalid;
    }
    for (Repeater, Facing) in &RepeaterMap {
        ValidateStep!();
        let mut ValidOccurrence = false;
        for (_Target, Path) in &Result.TargetPaths {
            ValidateStep!();
            for (Index, PositionValue) in Path.iter().enumerate() {
                ValidateStep!();
                if PositionValue != Repeater {
                    continue;
                }
                let Some(Previous) = Index
                    .checked_sub(1)
                    .and_then(|Value| Path.get(Value))
                    .copied()
                else {
                    return FinalValidationResultV1::Invalid;
                };
                let Some(Next) = Path.get(Index + 1).copied() else {
                    return FinalValidationResultV1::Invalid;
                };
                let OutputDelta = (
                    Next.0 - Repeater.0,
                    Next.1 - Repeater.1,
                    Next.2 - Repeater.2,
                );
                let ExpectedInput = (
                    Repeater.0 - OutputDelta.0,
                    Repeater.1 - OutputDelta.1,
                    Repeater.2 - OutputDelta.2,
                );
                if Previous != ExpectedInput
                    || RepeaterInputFacing(*Repeater, Next).as_deref() != Some(Facing.as_str())
                {
                    return FinalValidationResultV1::Invalid;
                }
                match CandidateEdgeIsValidWithDeadline(
                    Context,
                    Previous,
                    *Repeater,
                    Deadline,
                    &mut ValidationSteps,
                ) {
                    Ok(true) => {}
                    Ok(false) => return FinalValidationResultV1::Invalid,
                    Err(Result) => return Result,
                }
                ValidOccurrence = true;
            }
        }
        if !ValidOccurrence {
            return FinalValidationResultV1::Invalid;
        }
    }
    if Request.EnforceSignalStrength {
        match FindSelfExcitingRepeaterCyclesWithDeadline(
            &NodeSet,
            &Result.RepeaterReservations,
            Deadline,
        ) {
            DeadlineAwareElectricalResult::Complete(Cycles) if Cycles.is_empty() => {}
            DeadlineAwareElectricalResult::Complete(_) => {
                return FinalValidationResultV1::Invalid;
            }
            DeadlineAwareElectricalResult::DeadlineExhausted => {
                return FinalValidationResultV1::DeadlineExhausted;
            }
        }
        let mut Targets = Vec::with_capacity(Request.TargetBranches.len());
        for Branch in Request.TargetBranches.iter() {
            ValidateStep!();
            if let Some(Target) = Branch.last().copied() {
                Targets.push(Target);
            }
        }
        let mut Powered = false;
        for Start in Request.Starts.iter().copied() {
            ValidateStep!();
            if !NodeSet.contains(&Start) {
                continue;
            }
            let Powers = match PropagateCanonicalRoutePowerWithDeadline(
                Start,
                &NodeSet,
                &RepeaterMap,
                &Context.Adjacency,
                Deadline,
            ) {
                DeadlineAwareElectricalResult::Complete(Powers) => Powers,
                DeadlineAwareElectricalResult::DeadlineExhausted => {
                    return FinalValidationResultV1::DeadlineExhausted;
                }
            };
            let mut AllPowered = true;
            for Target in &Targets {
                ValidateStep!();
                if Powers.get(Target).copied().unwrap_or(0) == 0 {
                    AllPowered = false;
                    break;
                }
            }
            if AllPowered {
                Powered = true;
                break;
            }
        }
        if !Powered {
            return FinalValidationResultV1::Invalid;
        }
    }
    if Deadline.Check() {
        FinalValidationResultV1::DeadlineExhausted
    } else {
        FinalValidationResultV1::Valid
    }
}

fn RelaxedConnectivityProof(
    Context: &RoutingContext,
    Request: &CanonicalRouteRequestV1,
    Admission: &RequestExpansionAdmissionV1,
    Deadline: &RuntimeDeadline,
) -> RelaxedConnectivityResultV1 {
    if Deadline.Check() {
        return RelaxedConnectivityResultV1::DeadlineExhausted;
    }
    let mut ProofSteps = 0usize;
    macro_rules! ProofStep {
        () => {
            ProofSteps = match ProofSteps.checked_add(1) {
                Some(Value) => Value,
                None => return RelaxedConnectivityResultV1::DeadlineExhausted,
            };
            if ProofSteps % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return RelaxedConnectivityResultV1::DeadlineExhausted;
            }
        };
    }
    let mut Domain = HashSet::with_capacity(Request.AllowedNodes.len());
    for PositionValue in Request.AllowedNodes.iter().copied() {
        ProofStep!();
        if Request.BlockedNodes.binary_search(&PositionValue).is_err() {
            Domain.insert(PositionValue);
        }
    }
    let mut Adjacency: HashMap<Position, Vec<Position>> = HashMap::with_capacity(Domain.len());
    for PositionValue in Domain.iter().copied() {
        ProofStep!();
        Adjacency.insert(PositionValue, Vec::new());
    }
    for (First, Neighbors) in Context.Adjacency.iter() {
        ProofStep!();
        if !Domain.contains(First) {
            continue;
        }
        for Second in Neighbors {
            ProofStep!();
            if Domain.contains(Second) {
                Adjacency.get_mut(First).unwrap().push(*Second);
            }
        }
    }
    let Starts = match SortedUniqueWithDeadline(Request.Starts.as_ref(), Deadline) {
        Ok(Value) => Value,
        Err(_) => return RelaxedConnectivityResultV1::DeadlineExhausted,
    };
    let mut Reached = HashSet::with_capacity(Starts.len());
    for Start in Starts.iter().copied() {
        ProofStep!();
        Reached.insert(Start);
    }
    let mut Pending = VecDeque::from(Starts);
    while let Some(Current) = Pending.pop_front() {
        ProofStep!();
        if !Admission.TryAdmitOne(ExpansionWorkPhase::Proof) {
            return RelaxedConnectivityResultV1::WorkCapExhausted;
        }
        for Neighbor in Adjacency.get(&Current).into_iter().flatten() {
            ProofStep!();
            if Reached.insert(*Neighbor) {
                Pending.push_back(*Neighbor);
            }
        }
        let mut EveryTargetReached = true;
        for Branch in Request.TargetBranches.iter() {
            ProofStep!();
            if !Reached.contains(&Branch[0]) {
                EveryTargetReached = false;
                break;
            }
        }
        if EveryTargetReached {
            return RelaxedConnectivityResultV1::Connected;
        }
    }
    if Deadline.Check() {
        return RelaxedConnectivityResultV1::DeadlineExhausted;
    }
    let mut Unreachable = Vec::new();
    for (Index, Branch) in Request.TargetBranches.iter().enumerate() {
        ProofStep!();
        if !Reached.contains(&Branch[0]) {
            Unreachable.push((Index, Branch[0]));
        }
    }
    if Unreachable.is_empty() {
        return RelaxedConnectivityResultV1::Connected;
    }
    let mut UnsortedReached = Vec::with_capacity(Reached.len());
    for PositionValue in Reached {
        ProofStep!();
        UnsortedReached.push(PositionValue);
    }
    let ReachedNodes = match SortedUniqueWithDeadline(&UnsortedReached, Deadline) {
        Ok(Value) => Value,
        Err(_) => return RelaxedConnectivityResultV1::DeadlineExhausted,
    };
    let mut UnreachableTargetBranchOrdinals = Vec::with_capacity(Unreachable.len());
    let mut UnreachableAttachmentNodes = Vec::with_capacity(Unreachable.len());
    for (Ordinal, PositionValue) in Unreachable.iter().copied() {
        ProofStep!();
        UnreachableTargetBranchOrdinals.push(Ordinal);
        UnreachableAttachmentNodes.push(PositionValue);
    }
    RelaxedConnectivityResultV1::Disconnected(
        RouteTreeNoPathProofV1 {
            ProofKind: "RequiredAttachmentDisconnectedInRelaxedGraphV1".to_string(),
            UnreachableTargetBranchOrdinals,
            UnreachableAttachmentNodes,
            ReachedNodeCount: ReachedNodes.len(),
            ExpansionCount: Admission.ProofCount(),
            Complete: true,
            ClaimScope: "OneOriginalRouteRequest".to_string(),
            ContextGraphSha256: String::new(),
            RouteDomainScopeSha256: String::new(),
            ImmutableInputSha256: String::new(),
            ReceiptScopeSha256: String::new(),
        },
        ReachedNodes,
    )
}

fn ValidateNoPathProofWithDeadline(
    Context: &RoutingContext,
    Request: &CanonicalRouteRequestV1,
    Proof: &RouteTreeNoPathProofV1,
    ReachedNodes: &[Position],
    ExpectedProofExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) -> FinalValidationResultV1 {
    if Deadline.Check() {
        return FinalValidationResultV1::DeadlineExhausted;
    }
    if Proof.ProofKind != "RequiredAttachmentDisconnectedInRelaxedGraphV1"
        || !Proof.Complete
        || Proof.ClaimScope != "OneOriginalRouteRequest"
        || Proof.UnreachableTargetBranchOrdinals.len() != Proof.UnreachableAttachmentNodes.len()
        || Proof.UnreachableTargetBranchOrdinals.is_empty()
        || Proof.ReachedNodeCount != ReachedNodes.len()
        || Proof.ExpansionCount != ExpectedProofExpansionCount
        || Proof.ExpansionCount != ReachedNodes.len()
    {
        return FinalValidationResultV1::Invalid;
    }
    let mut ValidationSteps = 0usize;
    macro_rules! ValidateStep {
        () => {
            if let Err(Result) = AdvanceValidationStep(&mut ValidationSteps, Deadline) {
                return Result;
            }
        };
    }
    for Pair in ReachedNodes.windows(2) {
        ValidateStep!();
        if Pair[0] >= Pair[1] {
            return FinalValidationResultV1::Invalid;
        }
    }
    let mut Domain = HashSet::with_capacity(Request.AllowedNodes.len());
    for Value in Request.AllowedNodes.iter().copied() {
        ValidateStep!();
        Domain.insert(Value);
    }
    for Value in Request.BlockedNodes.iter() {
        ValidateStep!();
        Domain.remove(Value);
    }
    let mut Reached = HashSet::with_capacity(ReachedNodes.len());
    for Value in ReachedNodes.iter().copied() {
        ValidateStep!();
        if !Reached.insert(Value) || !Domain.contains(&Value) {
            return FinalValidationResultV1::Invalid;
        }
    }
    for Start in Request.Starts.iter() {
        ValidateStep!();
        if !Reached.contains(Start) {
            return FinalValidationResultV1::Invalid;
        }
    }
    for Current in ReachedNodes.iter() {
        ValidateStep!();
        for Neighbor in Context.Adjacency.get(Current).into_iter().flatten() {
            ValidateStep!();
            if Domain.contains(Neighbor) && !Reached.contains(Neighbor) {
                return FinalValidationResultV1::Invalid;
            }
        }
    }
    let mut Expected = Vec::new();
    for (Index, Branch) in Request.TargetBranches.iter().enumerate() {
        ValidateStep!();
        if !Reached.contains(&Branch[0]) {
            Expected.push((Index, Branch[0]));
        }
    }
    let mut Actual = Vec::with_capacity(Proof.UnreachableTargetBranchOrdinals.len());
    for Pair in Proof
        .UnreachableTargetBranchOrdinals
        .iter()
        .copied()
        .zip(Proof.UnreachableAttachmentNodes.iter().copied())
    {
        ValidateStep!();
        Actual.push(Pair);
    }
    if Actual.len() != Expected.len() {
        return FinalValidationResultV1::Invalid;
    }
    for (ActualValue, ExpectedValue) in Actual.iter().zip(Expected.iter()) {
        ValidateStep!();
        if ActualValue != ExpectedValue {
            return FinalValidationResultV1::Invalid;
        }
    }
    if Deadline.Check() {
        FinalValidationResultV1::DeadlineExhausted
    } else {
        FinalValidationResultV1::Valid
    }
}

fn ExecuteCanonicalRequest(
    Context: &RoutingContext,
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    OriginalOrdinal: usize,
    Request: Arc<CanonicalRouteRequestV1>,
    BatchStarted: Instant,
    Deadline: &RuntimeDeadline,
    Admission: &RequestExpansionAdmissionV1,
    SearchStartedAtMicroseconds: &AtomicU64,
    ImmutableInputIdentity: PendingIdentityV1,
) -> PendingReceiptV1 {
    #[cfg(test)]
    let BatchIdentityValue = BatchIdentity
        .SealedUtf8
        .as_deref()
        .expect("canonical execution requires a sealed batch identity");
    let mut Receipt = EmptyReceipt(
        BatchIdentity.clone(),
        Request.RequestKind,
        Request.RequestId.clone(),
        OriginalOrdinal,
        Request.MaximumExpansionCount,
        "DetailedSearchIncomplete",
        "Search",
        PendingIdentityV1::Verified(
            Request.RouteDomainScopeCanonicalJson.clone(),
            Request.RouteDomainScopeSha256.clone(),
        ),
        ImmutableInputIdentity,
        PendingIdentityV1::Verified(
            Request.CallerEchoScopeCanonicalJson.clone(),
            Request.CallerEchoScopeSha256.clone(),
        ),
    );
    Receipt.CanonicalRequest = Some(Request.clone());
    Receipt.CancellationRequested = Request.CancellationRequestedBeforeStart;
    if Request.CancellationRequestedBeforeStart {
        Receipt.TerminalReason = "Cancelled".to_string();
        Receipt.OutcomePhase = "Completed";
        Receipt.CancellationAcknowledged = true;
        Receipt.SearchStopped = true;
        Receipt.CleanupDisposition = "NotApplicableNoDispatch".to_string();
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
        return Receipt;
    }
    if Deadline.Check() {
        Receipt.TerminalReason = "DeadlineExhausted".to_string();
        Receipt.OutcomePhase = "Search";
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
        return Receipt;
    }
    if Admission.Maximum() == 0 {
        Receipt.TerminalReason = "WorkCapExhausted".to_string();
        Receipt.OutcomePhase = "Search";
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
        return Receipt;
    }
    Receipt.Started = true;
    let StartedAt = ElapsedMicroseconds(BatchStarted);
    SearchStartedAtMicroseconds.store(StartedAt, Ordering::Relaxed);
    Receipt.StartedAtMicroseconds = Some(StartedAt);
    #[cfg(test)]
    if TEST_PANIC_AFTER_NORMALIZATION_BATCH
        .lock()
        .expect("test panic selector lock")
        .is_some_and(|Value| Value == (BatchIdentityValue, OriginalOrdinal))
    {
        assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Route));
        panic!("test-only panic after normalization and admitted route work");
    }
    let Result = Context.GenerateRouteTreeDetailedWithAdmissionNative(
        Request.Starts.as_ref(),
        Request.TargetBranches.as_ref(),
        Request.AllowedNodes.as_ref(),
        Request.BlockedNodes.as_ref(),
        Request.PreferredColumns.as_ref(),
        Request.NodeCosts.as_ref(),
        Request.PreferredRoutingY,
        Request.GuidePenalty,
        Request.BendPenalty,
        Request.ViaPenalty,
        Request.EnforceSignalStrength,
        Request.MaximumExpansionCount,
        Some(Admission),
        Deadline,
    );
    if Result.IsRouted {
        match ValidateFoundCandidateWithDeadline(
            Context,
            &Request,
            &Result,
            Admission.RouteCount(),
            Deadline,
        ) {
            FinalValidationResultV1::Valid => {
                if Deadline.Check() {
                    Receipt.TerminalReason = "DeadlineExhausted".to_string();
                } else {
                    Receipt.SearchOutcome = "Found".to_string();
                    Receipt.TerminalReason = "Found".to_string();
                    Receipt.OutcomePhase = "Completed";
                    Receipt.Candidate = Some(Result);
                }
            }
            FinalValidationResultV1::Invalid => {
                Receipt.TerminalReason = "InvalidProducerResult".to_string();
                Receipt.OutcomePhase = "CandidateValidation";
            }
            FinalValidationResultV1::DeadlineExhausted => {
                Receipt.TerminalReason = "DeadlineExhausted".to_string();
                Receipt.OutcomePhase = "CandidateValidation";
            }
        }
    } else if Deadline.Check() || Result.IsBudgetExpired {
        Receipt.TerminalReason = "DeadlineExhausted".to_string();
    } else if Admission.TotalCount() >= Admission.Maximum() {
        Receipt.TerminalReason = "WorkCapExhausted".to_string();
    } else {
        match RelaxedConnectivityProof(Context, &Request, Admission, Deadline) {
            RelaxedConnectivityResultV1::Disconnected(Proof, ReachedNodes) => {
                match ValidateNoPathProofWithDeadline(
                    Context,
                    &Request,
                    &Proof,
                    &ReachedNodes,
                    Admission.ProofCount(),
                    Deadline,
                ) {
                    FinalValidationResultV1::Valid => {
                        Receipt.SearchOutcome = "ProvenNoPath".to_string();
                        Receipt.TerminalReason = "RelaxedGraphDisconnected".to_string();
                        Receipt.OutcomePhase = "Completed";
                        Receipt.NoPathProof = Some(Proof);
                        Receipt.ProofReachedNodes = ReachedNodes;
                    }
                    FinalValidationResultV1::Invalid => {
                        Receipt.TerminalReason = "InvalidProducerResult".to_string();
                        Receipt.OutcomePhase = "ProofValidation";
                    }
                    FinalValidationResultV1::DeadlineExhausted => {
                        Receipt.TerminalReason = "DeadlineExhausted".to_string();
                        Receipt.OutcomePhase = "ProofValidation";
                    }
                }
            }
            RelaxedConnectivityResultV1::Connected => {
                Receipt.TerminalReason = "DetailedSearchIncomplete".to_string();
                Receipt.OutcomePhase = "Proof";
            }
            RelaxedConnectivityResultV1::WorkCapExhausted => {
                Receipt.TerminalReason = "WorkCapExhausted".to_string();
                Receipt.OutcomePhase = "Proof";
            }
            RelaxedConnectivityResultV1::DeadlineExhausted => {
                Receipt.TerminalReason = "DeadlineExhausted".to_string();
                Receipt.OutcomePhase = "Proof";
            }
        }
    }
    Receipt.RouteExpansionCount = Admission.RouteCount();
    Receipt.ProofExpansionCount = Admission.ProofCount();
    Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
    Receipt
}

fn MinimalRequestIdentity(Request: &AuthoritativeRouteRequestV1) -> (&str, &'static str, usize) {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => (
            &Value.RequestId,
            COARSE_REQUEST_KIND,
            Value.MaximumExpansionCount,
        ),
        AuthoritativeRouteRequestV1::Detailed(Value) => (
            &Value.RequestId,
            DETAILED_REQUEST_KIND,
            Value.MaximumExpansionCount,
        ),
    }
}

fn SealedRequestId(Request: &AuthoritativeRouteRequestV1) -> Arc<str> {
    match Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.RequestId.clone(),
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.RequestId.clone(),
    }
}

fn CatchRequestWorkerV1<T, F>(Work: F) -> Result<T, ()>
where
    F: FnOnce() -> T,
{
    catch_unwind(AssertUnwindSafe(Work)).map_err(|_Panic| ())
}

#[allow(clippy::too_many_arguments)]
#[cfg(test)]
fn ExecuteRequestWorkerBoundaryV1<F>(
    BatchIdentity: &str,
    RequestKind: &str,
    RequestId: &str,
    OriginalOrdinal: usize,
    MaximumExpansionCount: usize,
    ImmutableInputCanonicalJson: String,
    RawCallerEchoScopeCanonicalJson: String,
    BatchStarted: Instant,
    Work: F,
) -> PendingReceiptV1
where
    F: FnOnce(&RequestExpansionAdmissionV1, &AtomicU64) -> PendingReceiptV1,
{
    let Admission = RequestExpansionAdmissionV1::New(MaximumExpansionCount);
    let SearchStartedAtMicroseconds = AtomicU64::new(u64::MAX);
    let Outcome = CatchRequestWorkerV1(|| Work(&Admission, &SearchStartedAtMicroseconds));
    let mut Receipt = match Outcome {
        Ok(Value) => Value,
        Err(()) => EmptyReceipt(
            RetainedBatchIdentityV1::Owned(BatchIdentity),
            RequestKind,
            Arc::from(RequestId),
            OriginalOrdinal,
            MaximumExpansionCount,
            "WorkerFailure",
            "Search",
            PendingIdentityV1::Unavailable(PRODUCER_FAILURE),
            PendingIdentityV1::Verified(
                Arc::from(ImmutableInputCanonicalJson.clone()),
                Arc::from(NativeSha256(&ImmutableInputCanonicalJson)),
            ),
            PendingIdentityV1::Verified(
                Arc::from(RawCallerEchoScopeCanonicalJson.clone()),
                Arc::from(NativeSha256(&RawCallerEchoScopeCanonicalJson)),
            ),
        ),
    };
    Receipt.RouteExpansionCount = Admission.RouteCount();
    Receipt.ProofExpansionCount = Admission.ProofCount();
    let ObservedStartedAt = SearchStartedAtMicroseconds.load(Ordering::Relaxed);
    if ObservedStartedAt != u64::MAX {
        Receipt.Started = true;
        Receipt.StartedAtMicroseconds = Some(ObservedStartedAt);
    }
    if Receipt.CompletedAtMicroseconds.is_none() {
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
    }
    Receipt.SealProducerFacts();
    Receipt
}

fn ExecuteRequestCaught(
    Context: &RoutingContext,
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    OriginalOrdinal: usize,
    Request: AuthoritativeRouteRequestV1,
    BatchStarted: Instant,
    Deadline: &RuntimeDeadline,
) -> PendingReceiptV1 {
    let (_RequestId, RequestKind, MaximumExpansionCount) = MinimalRequestIdentity(&Request);
    let OriginalRequest = Request.clone();
    let RequestId = SealedRequestId(&Request);
    let ImmutableInputIdentity = PendingIdentityV1::Verified(
        ImmutableInputCanonicalJson(&Request),
        ImmutableInputSha256(&Request),
    );
    let CallerEchoIdentity = SealedCallerEchoIdentity(&Request);
    let CancellationRequested = match &Request {
        AuthoritativeRouteRequestV1::Coarse(Value) => Value.CancellationRequestedBeforeStart,
        AuthoritativeRouteRequestV1::Detailed(Value) => Value.CancellationRequestedBeforeStart,
    };
    if Deadline.Check() {
        let mut Receipt = EmptyReceipt(
            BatchIdentity.clone(),
            RequestKind,
            RequestId.clone(),
            OriginalOrdinal,
            MaximumExpansionCount,
            "DeadlineExhaustedAtEntry",
            "Entry",
            PendingIdentityV1::Unavailable(UNCOMPUTED_DEADLINE),
            ImmutableInputIdentity,
            CallerEchoIdentity,
        );
        Receipt.CancellationRequested = CancellationRequested;
        Receipt.OriginalRequest = Some(OriginalRequest);
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
        Receipt.SealProducerFacts();
        return Receipt;
    }
    let Normalized = CatchRequestWorkerV1(|| {
        #[cfg(test)]
        if TEST_PANIC_DURING_NORMALIZATION_BATCH
            .lock()
            .expect("test normalization panic selector lock")
            .is_some_and(|Value| {
                BatchIdentity.SealedUtf8.as_deref() == Some(Value.0) && OriginalOrdinal == Value.1
            })
        {
            panic!("test-only panic during normalization");
        }
        NormalizeRequest(Context, Request.clone(), Deadline)
    });
    let mut Receipt = match Normalized {
        Err(()) => EmptyReceipt(
            BatchIdentity.clone(),
            RequestKind,
            RequestId.clone(),
            OriginalOrdinal,
            MaximumExpansionCount,
            "WorkerFailure",
            "DomainCanonicalization",
            PendingIdentityV1::Unavailable(PRODUCER_FAILURE),
            ImmutableInputIdentity.clone(),
            CallerEchoIdentity.clone(),
        ),
        Ok(Err(Reason)) => EmptyReceipt(
            BatchIdentity.clone(),
            RequestKind,
            RequestId.clone(),
            OriginalOrdinal,
            MaximumExpansionCount,
            match Reason {
                RequestNormalizationErrorV1::Unsupported => "UnsupportedRequest",
                RequestNormalizationErrorV1::DeadlineExhausted => {
                    "DeadlineExhaustedDuringValidation"
                }
            },
            "DomainCanonicalization",
            PendingIdentityV1::Unavailable(match Reason {
                RequestNormalizationErrorV1::Unsupported => UNSUPPORTED_INPUT,
                RequestNormalizationErrorV1::DeadlineExhausted => UNCOMPUTED_DEADLINE,
            }),
            ImmutableInputIdentity.clone(),
            CallerEchoIdentity.clone(),
        ),
        Ok(Ok(Canonical)) => {
            let Canonical = Arc::new(Canonical);
            let Admission = RequestExpansionAdmissionV1::New(MaximumExpansionCount);
            let SearchStartedAtMicroseconds = AtomicU64::new(u64::MAX);
            let Outcome = CatchRequestWorkerV1(|| {
                ExecuteCanonicalRequest(
                    Context,
                    BatchIdentity.clone(),
                    OriginalOrdinal,
                    Canonical.clone(),
                    BatchStarted,
                    Deadline,
                    &Admission,
                    &SearchStartedAtMicroseconds,
                    ImmutableInputIdentity.clone(),
                )
            });
            let mut Value = match Outcome {
                Ok(Value) => Value,
                Err(()) => {
                    let mut Failed = EmptyReceipt(
                        BatchIdentity.clone(),
                        RequestKind,
                        RequestId.clone(),
                        OriginalOrdinal,
                        MaximumExpansionCount,
                        "WorkerFailure",
                        "Search",
                        PendingIdentityV1::Verified(
                            Canonical.RouteDomainScopeCanonicalJson.clone(),
                            Canonical.RouteDomainScopeSha256.clone(),
                        ),
                        ImmutableInputIdentity.clone(),
                        PendingIdentityV1::Verified(
                            Canonical.CallerEchoScopeCanonicalJson.clone(),
                            Canonical.CallerEchoScopeSha256.clone(),
                        ),
                    );
                    Failed.CanonicalRequest = Some(Canonical.clone());
                    Failed
                }
            };
            Value.RouteExpansionCount = Admission.RouteCount();
            Value.ProofExpansionCount = Admission.ProofCount();
            let ObservedStartedAt = SearchStartedAtMicroseconds.load(Ordering::Relaxed);
            if ObservedStartedAt != u64::MAX {
                Value.Started = true;
                Value.StartedAtMicroseconds = Some(ObservedStartedAt);
            }
            Value
        }
    };
    if Receipt.CompletedAtMicroseconds.is_none() {
        Receipt.CompletedAtMicroseconds = Some(ElapsedMicroseconds(BatchStarted));
    }
    Receipt.CancellationRequested = CancellationRequested;
    Receipt.OriginalRequest = Some(OriginalRequest);
    Receipt.SealProducerFacts();
    Receipt
}

pub(crate) fn GenerateRouteTreeBatchOutcomesNativeV1(
    Context: &RoutingContext,
    BatchIdentity: Arc<RetainedBatchIdentityV1>,
    Requests: Vec<AuthoritativeRouteRequestV1>,
    DeadlineAtMonotonicSeconds: f64,
    BoundaryMonotonicSampleSeconds: f64,
    NativeRemainingNanoseconds: u64,
    Deadline: RuntimeDeadline,
    BatchStarted: Instant,
) -> PendingBatchOutcomesV1 {
    let ContextGraphCanonicalJson = Context.AuthoritativeIdentityV1.CanonicalJson.clone();
    let ContextGraphSha256 = Context.AuthoritativeIdentityV1.Sha256.clone();
    let Receipts = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .enumerate()
            .map(|(Ordinal, Request)| {
                ExecuteRequestCaught(
                    Context,
                    BatchIdentity.clone(),
                    Ordinal,
                    Request,
                    BatchStarted,
                    &Deadline,
                )
            })
            .collect()
    });
    PendingBatchOutcomesV1 {
        BatchIdentity,
        DeadlineAtMonotonicSeconds,
        BoundaryMonotonicSampleSeconds,
        NativeRemainingNanoseconds,
        ContextGraphCanonicalJson,
        ContextGraphSha256,
        Receipts,
        Deadline,
    }
}

fn NativeSha256(Canonical: &str) -> String {
    format!("{:x}", Sha256::digest(Canonical.as_bytes()))
}

fn DowngradePendingReceipt(Receipt: &mut PendingReceiptV1, Reason: &str) {
    Receipt.SearchOutcome = "Incomplete".to_string();
    Receipt.TerminalReason = Reason.to_string();
    Receipt.Candidate = None;
    Receipt.NoPathProof = None;
    Receipt.ProofReachedNodes.clear();
}

fn ProducerReceiptStateIsCoherent(
    Facts: &ProducerReceiptFactsV1,
    OriginalCancellationRequested: bool,
) -> bool {
    let AccountedTotal = Facts
        .RouteExpansionCount
        .checked_add(Facts.ProofExpansionCount);
    let Common = AccountedTotal.is_some_and(|Total| Total <= Facts.MaximumExpansionCount)
        && Facts.CompletedAtMicroseconds.is_some()
        && Facts.Started == Facts.StartedAtMicroseconds.is_some()
        && (Facts.Started || AccountedTotal == Some(0))
        && Facts.CancellationRequested == OriginalCancellationRequested
        && Facts.RouteDomainIdentity.IsCoherent()
        && (Facts.CanonicalRequest.is_some()
            == (Facts.RouteDomainIdentity.Availability == VERIFIED))
        && matches!(
            Facts.OutcomePhase,
            "Entry"
                | "DomainCanonicalization"
                | "Search"
                | "Proof"
                | "CandidateValidation"
                | "ProofValidation"
                | "Completed"
        );
    if !Common {
        return false;
    }
    let UnstartedUnavailable = !Facts.Started
        && AccountedTotal == Some(0)
        && !Facts.CancellationAcknowledged
        && !Facts.SearchStopped
        && Facts.CleanupDisposition == "NotApplicableNoProcess"
        && Facts.CanonicalRequest.is_none();
    match (Facts.SearchOutcome.as_str(), Facts.TerminalReason.as_str()) {
        ("Found", "Found") | ("ProvenNoPath", "RelaxedGraphDisconnected") => {
            Facts.OutcomePhase == "Completed"
                && Facts.Started
                && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        ("Incomplete", "DeadlineExhaustedAtEntry") => {
            UnstartedUnavailable
                && Facts.OutcomePhase == "Entry"
                && Facts.RouteDomainIdentity.Availability == UNCOMPUTED_DEADLINE
        }
        ("Incomplete", "UnsupportedRequest") => {
            UnstartedUnavailable
                && Facts.OutcomePhase == "DomainCanonicalization"
                && Facts.RouteDomainIdentity.Availability == UNSUPPORTED_INPUT
        }
        ("Incomplete", "DeadlineExhaustedDuringValidation") => {
            UnstartedUnavailable
                && Facts.OutcomePhase == "DomainCanonicalization"
                && Facts.RouteDomainIdentity.Availability == UNCOMPUTED_DEADLINE
        }
        ("Incomplete", "WorkerFailure") if Facts.CanonicalRequest.is_none() => {
            UnstartedUnavailable
                && Facts.OutcomePhase == "DomainCanonicalization"
                && Facts.RouteDomainIdentity.Availability == PRODUCER_FAILURE
        }
        ("Incomplete", "Cancelled") => {
            Facts.OutcomePhase == "Completed"
                && !Facts.Started
                && AccountedTotal == Some(0)
                && Facts.CanonicalRequest.is_some()
                && Facts.CancellationRequested
                && Facts.CancellationAcknowledged
                && Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoDispatch"
        }
        ("Incomplete", "WorkCapExhausted") => {
            matches!(Facts.OutcomePhase, "Search" | "Proof")
                && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        ("Incomplete", "DeadlineExhausted") => {
            matches!(
                Facts.OutcomePhase,
                "Search" | "Proof" | "CandidateValidation" | "ProofValidation"
            ) && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        ("Incomplete", "DetailedSearchIncomplete") => {
            Facts.OutcomePhase == "Proof"
                && Facts.Started
                && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        ("Incomplete", "InvalidProducerResult") => {
            matches!(
                Facts.OutcomePhase,
                "CandidateValidation" | "ProofValidation"
            ) && Facts.Started
                && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        ("Incomplete", "WorkerFailure") => {
            Facts.OutcomePhase == "Search"
                && Facts.CanonicalRequest.is_some()
                && !Facts.CancellationRequested
                && !Facts.CancellationAcknowledged
                && !Facts.SearchStopped
                && Facts.CleanupDisposition == "NotApplicableNoProcess"
        }
        _ => false,
    }
}

pub(crate) fn FinalizeRouteTreeBatchOutcomesV1(
    Pending: PendingBatchOutcomesV1,
    Context: &RoutingContext,
) -> PyResult<RouteTreeBatchOutcomesV1> {
    if !Arc::ptr_eq(
        &Pending.ContextGraphCanonicalJson,
        &Context.AuthoritativeIdentityV1.CanonicalJson,
    ) || !Arc::ptr_eq(
        &Pending.ContextGraphSha256,
        &Context.AuthoritativeIdentityV1.Sha256,
    ) {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "authoritative route batch context identity mismatch",
        ));
    }
    let ContextGraphCanonicalJson = Pending.ContextGraphCanonicalJson.clone();
    let ContextGraphSha256 = Pending.ContextGraphSha256.clone();
    let mut Receipts = Vec::with_capacity(Pending.Receipts.len());
    for mut Value in Pending.Receipts {
        let ProducerFacts = Value.ProducerFacts.clone().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "authoritative route receipt has no sealed producer facts",
            )
        })?;
        if ProducerFacts.OriginalOrdinal != Value.OriginalOrdinal {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "authoritative route receipt ordinal mismatch",
            ));
        }
        let PendingMatchesProducer = ProducerFacts.Matches(&Value);
        let OriginalRequest = ProducerFacts.OriginalRequest.clone().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "authoritative route receipt lost its immutable request",
            )
        })?;
        let (_OriginalRequestId, OriginalRequestKind, OriginalMaximumExpansionCount) =
            MinimalRequestIdentity(&OriginalRequest);
        let OriginalRequestId = SealedRequestId(&OriginalRequest);
        let OriginalCancellationRequested = match &OriginalRequest {
            AuthoritativeRouteRequestV1::Coarse(Value) => Value.CancellationRequestedBeforeStart,
            AuthoritativeRouteRequestV1::Detailed(Value) => Value.CancellationRequestedBeforeStart,
        };
        let ExpectedImmutableInputIdentity = PendingIdentityV1::Verified(
            ImmutableInputCanonicalJson(&OriginalRequest),
            ImmutableInputSha256(&OriginalRequest),
        );
        let NativePayloadCanonicalJson = NativePayloadCanonicalJson(&OriginalRequest);
        let NativePayloadSha256 = NativePayloadSha256(&OriginalRequest);
        let ExpectedCallerEchoIdentity = ProducerFacts
            .CanonicalRequest
            .as_ref()
            .map(|Canonical| {
                PendingIdentityV1::Verified(
                    Canonical.CallerEchoScopeCanonicalJson.clone(),
                    Canonical.CallerEchoScopeSha256.clone(),
                )
            })
            .unwrap_or_else(|| SealedCallerEchoIdentity(&OriginalRequest));
        let ExpectedRouteDomainIdentity = ProducerFacts
            .CanonicalRequest
            .as_ref()
            .map(|Canonical| {
                PendingIdentityV1::Verified(
                    Canonical.RouteDomainScopeCanonicalJson.clone(),
                    Canonical.RouteDomainScopeSha256.clone(),
                )
            })
            .unwrap_or_else(|| ProducerFacts.RouteDomainIdentity.clone());
        let OriginalEnvelopeValid = PendingMatchesProducer
            && Arc::ptr_eq(&OriginalRequestId, &ProducerFacts.RequestId)
            && OriginalRequestKind == Value.RequestKind
            && OriginalMaximumExpansionCount == ProducerFacts.MaximumExpansionCount
            && OriginalCancellationRequested == ProducerFacts.CancellationRequested
            && Value
                .ImmutableInputIdentity
                .IsSameSeal(&ExpectedImmutableInputIdentity)
            && Value
                .CallerEchoIdentity
                .IsSameSeal(&ExpectedCallerEchoIdentity)
            && Value.ImmutableInputIdentity.IsCoherent()
            && Value.CallerEchoIdentity.IsCoherent()
            && Value.RouteDomainIdentity.IsCoherent()
            && Value
                .RouteDomainIdentity
                .IsSameSeal(&ExpectedRouteDomainIdentity)
            && ProducerReceiptStateIsCoherent(&ProducerFacts, OriginalCancellationRequested);
        ProducerFacts.Restore(&mut Value);
        Value.ImmutableInputIdentity = ExpectedImmutableInputIdentity;
        Value.CallerEchoIdentity = ExpectedCallerEchoIdentity;
        Value.RouteDomainIdentity = ExpectedRouteDomainIdentity;
        let AccountedTotal = Value
            .RouteExpansionCount
            .checked_add(Value.ProofExpansionCount)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("work accounting overflow"))?;
        let IncompleteShapeValid = Value.SearchOutcome != "Incomplete"
            || (Value.Candidate.is_none()
                && Value.NoPathProof.is_none()
                && ProducerReceiptStateIsCoherent(&ProducerFacts, OriginalCancellationRequested));
        let ValidationResult = if !OriginalEnvelopeValid {
            FinalValidationResultV1::Invalid
        } else if !IncompleteShapeValid || AccountedTotal > Value.MaximumExpansionCount {
            FinalValidationResultV1::Invalid
        } else {
            match Value.SearchOutcome.as_str() {
                "Found" => match (Value.CanonicalRequest.as_ref(), Value.Candidate.as_ref()) {
                    (Some(Request), Some(Candidate)) if Value.NoPathProof.is_none() => {
                        ValidateFoundCandidateWithDeadline(
                            Context,
                            Request,
                            Candidate,
                            Value.RouteExpansionCount,
                            &Pending.Deadline,
                        )
                    }
                    _ => FinalValidationResultV1::Invalid,
                },
                "ProvenNoPath" => {
                    match (Value.CanonicalRequest.as_ref(), Value.NoPathProof.as_ref()) {
                        (Some(Request), Some(Proof)) if Value.Candidate.is_none() => {
                            ValidateNoPathProofWithDeadline(
                                Context,
                                Request,
                                Proof,
                                &Value.ProofReachedNodes,
                                Value.ProofExpansionCount,
                                &Pending.Deadline,
                            )
                        }
                        _ => FinalValidationResultV1::Invalid,
                    }
                }
                "Incomplete" => FinalValidationResultV1::Valid,
                _ => FinalValidationResultV1::Invalid,
            }
        };
        match ValidationResult {
            FinalValidationResultV1::Valid => {}
            FinalValidationResultV1::Invalid => {
                DowngradePendingReceipt(&mut Value, "InvalidProducerResult");
                Value.OutcomePhase = "ReceiptFinalization";
            }
            FinalValidationResultV1::DeadlineExhausted => {
                DowngradePendingReceipt(&mut Value, "DeadlineExhaustedDuringFinalization");
                Value.OutcomePhase = "ReceiptFinalization";
            }
        }
        let MissingReceiptDependency = if Pending.BatchIdentity.SealedUtf8.is_none() {
            Some("BatchIdentity".to_string())
        } else if Value.RouteDomainIdentity.Availability != VERIFIED {
            Some("RouteDomainIdentity".to_string())
        } else if Value.ImmutableInputIdentity.Availability != VERIFIED {
            Some("ImmutableInputIdentity".to_string())
        } else if Value.CallerEchoIdentity.Availability != VERIFIED {
            Some("CallerEchoIdentity".to_string())
        } else {
            None
        };
        let (mut ReceiptIdentity, ReceiptSealDeadlineExpired) = if let Some(_Dependency) =
            &MissingReceiptDependency
        {
            (
                PendingIdentityV1::Unavailable(DEPENDENCY_UNAVAILABLE),
                false,
            )
        } else {
            let Document = (
                "native-route-receipt-scope-v1",
                CONTRACT_VERSION,
                Pending
                    .BatchIdentity
                    .SealedUtf8
                    .as_deref()
                    .expect("receipt identity requires sealed batch identity"),
                Value.OriginalOrdinal,
                Value.RequestId.as_ref(),
                Value.ImmutableInputIdentity.Sha256.as_deref(),
                Pending.DeadlineAtMonotonicSeconds,
                ContextGraphSha256.as_ref(),
                Value.RouteDomainIdentity.Sha256.as_deref(),
                Value.CallerEchoIdentity.Sha256.as_deref(),
            );
            match CanonicalJsonAndShaWithDeadline(&Document, &Pending.Deadline, true) {
                Ok((Canonical, Sha256)) => (PendingIdentityV1::Verified(Canonical, Sha256), false),
                Err(_) => (PendingIdentityV1::Unavailable(UNCOMPUTED_DEADLINE), true),
            }
        };
        if ReceiptSealDeadlineExpired {
            DowngradePendingReceipt(&mut Value, "DeadlineExhaustedDuringFinalization");
            Value.OutcomePhase = "ReceiptFinalization";
            ReceiptIdentity = PendingIdentityV1::Unavailable(UNCOMPUTED_DEADLINE);
        }
        let mut Proof = Value.NoPathProof;
        if let Some(ProofValue) = Proof.as_mut() {
            let Some(RouteSha256) = Value.RouteDomainIdentity.Sha256.as_ref() else {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "proof route identity is unavailable",
                ));
            };
            let Some(InputSha256) = Value.ImmutableInputIdentity.Sha256.as_ref() else {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "proof input identity is unavailable",
                ));
            };
            let Some(ReceiptSha256) = ReceiptIdentity.Sha256.as_ref() else {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "proof receipt identity is unavailable",
                ));
            };
            ProofValue.ContextGraphSha256 = ContextGraphSha256.to_string();
            ProofValue.RouteDomainScopeSha256 = RouteSha256.to_string();
            ProofValue.ImmutableInputSha256 = InputSha256.to_string();
            ProofValue.ReceiptScopeSha256 = ReceiptSha256.to_string();
        }
        let TotalExpansionCount = Value
            .RouteExpansionCount
            .checked_add(Value.ProofExpansionCount)
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("work accounting overflow"))?;
        if TotalExpansionCount > Value.MaximumExpansionCount {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "authoritative route receipt exceeded its work cap",
            ));
        }
        let RuntimeSearchOutcome = match Value.SearchOutcome.as_str() {
            "Found" => "Prepared",
            "ProvenNoPath" => "Infeasible",
            _ => "Unresolved",
        }
        .to_string();
        let RuntimeClaimStrength = match Value.SearchOutcome.as_str() {
            "ProvenNoPath" => "InfeasibilityProof",
            "Found" => "Candidate",
            _ => "Continuation",
        }
        .to_string();
        let Receipt = RouteTreeRequestReceiptV1 {
            ContractVersion: CONTRACT_VERSION.to_string(),
            BatchIdentity: Value.BatchIdentity,
            BatchIdentityRetentionStatus: Pending.BatchIdentity.RetentionStatus.to_string(),
            RequestKind: Value.RequestKind,
            RequestId: Value.RequestId,
            OriginalOrdinal: Value.OriginalOrdinal,
            SearchOutcome: Value.SearchOutcome,
            TerminalReason: Value.TerminalReason,
            RuntimeSearchOutcome,
            RuntimeClaimStrength,
            RuntimeCommitEligibility: "Ineligible".to_string(),
            Started: Value.Started,
            Settled: true,
            StartedAtMicroseconds: Value.StartedAtMicroseconds,
            CompletedAtMicroseconds: Value.CompletedAtMicroseconds,
            DeadlineAtMonotonicSeconds: Pending.DeadlineAtMonotonicSeconds,
            BoundaryMonotonicSampleSeconds: Pending.BoundaryMonotonicSampleSeconds,
            NativeRemainingNanoseconds: Pending.NativeRemainingNanoseconds,
            WorkUnit: "ExpandedSearchState".to_string(),
            MaximumExpansionCount: Value.MaximumExpansionCount,
            RouteExpansionCount: Value.RouteExpansionCount,
            ProofExpansionCount: Value.ProofExpansionCount,
            TotalExpansionCount,
            NativePayloadCanonicalJson,
            NativePayloadSha256,
            RawInputRetentionStatus: "SealedCanonicalBytes".to_string(),
            CancellationSnapshotStatus: "Captured".to_string(),
            OutcomePhase: Value.OutcomePhase.to_string(),
            ContextGraphIdentityAvailability: VERIFIED.to_string(),
            ContextGraphSha256: Some(ContextGraphSha256.clone()),
            RouteDomainIdentityAvailability: Value.RouteDomainIdentity.Availability.to_string(),
            RouteDomainScopeCanonicalJson: Value.RouteDomainIdentity.CanonicalJson.clone(),
            RouteDomainScopeSha256: Value.RouteDomainIdentity.Sha256.clone(),
            ImmutableInputIdentityAvailability: VERIFIED.to_string(),
            ImmutableInputCanonicalJson: Value.ImmutableInputIdentity.CanonicalJson.clone(),
            ImmutableInputSha256: Value.ImmutableInputIdentity.Sha256.clone(),
            ReceiptIdentityAvailability: ReceiptIdentity.Availability.to_string(),
            ReceiptIdentityDependency: MissingReceiptDependency,
            ReceiptScopeCanonicalJson: ReceiptIdentity.CanonicalJson.clone(),
            ReceiptScopeSha256: ReceiptIdentity.Sha256.clone(),
            CallerEchoIdentityAvailability: Value.CallerEchoIdentity.Availability.to_string(),
            CallerEchoScopeCanonicalJson: Value.CallerEchoIdentity.CanonicalJson.clone(),
            CallerEchoScopeSha256: Value.CallerEchoIdentity.Sha256.clone(),
            Candidate: Value.Candidate,
            NoPathProof: Proof,
            CancellationRequested: Value.CancellationRequested,
            CancellationAcknowledged: Value.CancellationAcknowledged,
            SearchStopped: Value.SearchStopped,
            CleanupDisposition: Value.CleanupDisposition,
        };
        Receipts.push(Receipt);
    }
    for (ExpectedOrdinal, Receipt) in Receipts.iter().enumerate() {
        let AccountedTotal = Receipt
            .RouteExpansionCount
            .checked_add(Receipt.ProofExpansionCount);
        let ProofValid = Receipt.NoPathProof.as_ref().is_none_or(|Proof| {
            Proof.ProofKind == "RequiredAttachmentDisconnectedInRelaxedGraphV1"
                && Proof.Complete
                && Proof.ClaimScope == "OneOriginalRouteRequest"
                && Receipt.ContextGraphSha256.as_deref() == Some(&Proof.ContextGraphSha256)
                && Receipt.RouteDomainScopeSha256.as_deref() == Some(&Proof.RouteDomainScopeSha256)
                && Receipt.ImmutableInputSha256.as_deref() == Some(&Proof.ImmutableInputSha256)
                && Receipt.ReceiptScopeSha256.as_deref() == Some(&Proof.ReceiptScopeSha256)
                && Proof.ExpansionCount == Receipt.ProofExpansionCount
                && Proof.UnreachableTargetBranchOrdinals.len()
                    == Proof.UnreachableAttachmentNodes.len()
                && !Proof.UnreachableTargetBranchOrdinals.is_empty()
        });
        let ClaimIdentitiesVerified =
            !matches!(Receipt.SearchOutcome.as_str(), "Found" | "ProvenNoPath")
                || (Receipt.ContextGraphIdentityAvailability == VERIFIED
                    && Receipt.RouteDomainIdentityAvailability == VERIFIED
                    && Receipt.ImmutableInputIdentityAvailability == VERIFIED
                    && Receipt.CallerEchoIdentityAvailability == VERIFIED
                    && Receipt.ReceiptIdentityAvailability == VERIFIED);
        let PairIsCoherent = |Availability: &str, CanonicalPresent: bool, ShaPresent: bool| {
            if Availability == VERIFIED {
                CanonicalPresent && ShaPresent
            } else {
                !CanonicalPresent && !ShaPresent
            }
        };
        let IdentityPairsCoherent = PairIsCoherent(
            &Receipt.RouteDomainIdentityAvailability,
            Receipt.RouteDomainScopeCanonicalJson.is_some(),
            Receipt.RouteDomainScopeSha256.is_some(),
        ) && PairIsCoherent(
            &Receipt.ImmutableInputIdentityAvailability,
            Receipt.ImmutableInputCanonicalJson.is_some(),
            Receipt.ImmutableInputSha256.is_some(),
        ) && PairIsCoherent(
            &Receipt.CallerEchoIdentityAvailability,
            Receipt.CallerEchoScopeCanonicalJson.is_some(),
            Receipt.CallerEchoScopeSha256.is_some(),
        ) && PairIsCoherent(
            &Receipt.ReceiptIdentityAvailability,
            Receipt.ReceiptScopeCanonicalJson.is_some(),
            Receipt.ReceiptScopeSha256.is_some(),
        );
        let ReceiptDependencyCoherent =
            if Receipt.ReceiptIdentityAvailability == DEPENDENCY_UNAVAILABLE {
                Receipt.ReceiptIdentityDependency.is_some()
            } else {
                Receipt.ReceiptIdentityDependency.is_none()
            };
        let StartWorkCoherent = Receipt.Started
            || (Receipt.TotalExpansionCount == 0 && Receipt.StartedAtMicroseconds.is_none());
        let CancellationCoherent = !Receipt.CancellationAcknowledged
            || (Receipt.CancellationRequested
                && Receipt.SearchStopped
                && !Receipt.Started
                && Receipt.TotalExpansionCount == 0);
        if Receipt.OriginalOrdinal != ExpectedOrdinal
            || !Arc::ptr_eq(&Receipt.BatchIdentity, &Pending.BatchIdentity)
            || AccountedTotal != Some(Receipt.TotalExpansionCount)
            || (Receipt.SearchOutcome == "Found") != Receipt.Candidate.is_some()
            || (Receipt.SearchOutcome == "ProvenNoPath") != Receipt.NoPathProof.is_some()
            || !ProofValid
            || !ClaimIdentitiesVerified
            || !IdentityPairsCoherent
            || !ReceiptDependencyCoherent
            || !StartWorkCoherent
            || !CancellationCoherent
            || Receipt.RuntimeCommitEligibility != "Ineligible"
            || (Receipt.SearchOutcome == "Found"
                && (Receipt.RuntimeSearchOutcome != "Prepared"
                    || Receipt.RuntimeClaimStrength != "Candidate"))
            || (Receipt.SearchOutcome == "ProvenNoPath"
                && (Receipt.RuntimeSearchOutcome != "Infeasible"
                    || Receipt.RuntimeClaimStrength != "InfeasibilityProof"))
            || (Receipt.SearchOutcome == "Incomplete"
                && (Receipt.RuntimeSearchOutcome != "Unresolved"
                    || Receipt.RuntimeClaimStrength != "Continuation"))
        {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(
                "malformed authoritative route receipt",
            ));
        }
    }
    let TotalRequestCount = Receipts.len();
    let StartedRequestCount = Receipts.iter().filter(|Value| Value.Started).count();
    let FoundCount = Receipts
        .iter()
        .filter(|Value| Value.SearchOutcome == "Found")
        .count();
    let ProvenNoPathCount = Receipts
        .iter()
        .filter(|Value| Value.SearchOutcome == "ProvenNoPath")
        .count();
    let IncompleteCount = Receipts
        .iter()
        .filter(|Value| Value.SearchOutcome == "Incomplete")
        .count();
    if FoundCount + ProvenNoPathCount + IncompleteCount != TotalRequestCount {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "authoritative route outcome counts do not reconcile",
        ));
    }
    let CheckedSum = |Selector: fn(&RouteTreeRequestReceiptV1) -> usize| {
        Receipts
            .iter()
            .try_fold(0usize, |Total, Value| Total.checked_add(Selector(Value)))
    };
    let AggregateRouteExpansionCount = CheckedSum(|Value| Value.RouteExpansionCount)
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("route work total overflow"))?;
    let AggregateProofExpansionCount = CheckedSum(|Value| Value.ProofExpansionCount)
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("proof work total overflow"))?;
    let AggregateExpansionCount = CheckedSum(|Value| Value.TotalExpansionCount)
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("work total overflow"))?;
    Ok(RouteTreeBatchOutcomesV1 {
        ContractVersion: CONTRACT_VERSION.to_string(),
        BatchIdentity: Pending.BatchIdentity.clone(),
        BatchIdentityRetentionStatus: Pending.BatchIdentity.RetentionStatus.to_string(),
        DeadlineAtMonotonicSeconds: Pending.DeadlineAtMonotonicSeconds,
        BoundaryMonotonicSampleSeconds: Pending.BoundaryMonotonicSampleSeconds,
        NativeRemainingNanoseconds: Pending.NativeRemainingNanoseconds,
        ContextGraphIdentityAvailability: VERIFIED.to_string(),
        ContextGraphCanonicalJson: Some(ContextGraphCanonicalJson),
        ContextGraphSha256: Some(ContextGraphSha256),
        Receipts,
        TotalRequestCount,
        StartedRequestCount,
        SettledReceiptCount: TotalRequestCount,
        FoundCount,
        ProvenNoPathCount,
        IncompleteCount,
        AggregateRouteExpansionCount,
        AggregateProofExpansionCount,
        AggregateExpansionCount,
        DeadlineExceeded: Pending.Deadline.WasExceeded(),
    })
}

fn NextDownPositive(Value: f64) -> f64 {
    debug_assert!(Value.is_finite() && Value > 0.0);
    f64::from_bits(Value.to_bits() - 1)
}

pub(crate) fn BuildDeadlineFromPythonMonotonicCutoff(
    PythonValue: Python<'_>,
    DeadlineAtMonotonicSeconds: f64,
) -> PyResult<(RuntimeDeadline, Instant, f64, u64)> {
    let NativeSample = Instant::now();
    if !DeadlineAtMonotonicSeconds.is_finite() || DeadlineAtMonotonicSeconds < 0.0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "deadline monotonic seconds must be finite and non-negative",
        ));
    }
    let BoundaryMonotonicSampleSeconds = PyModule::import_bound(PythonValue, "time")?
        .getattr("monotonic")?
        .call0()?
        .extract::<f64>()?;
    if !BoundaryMonotonicSampleSeconds.is_finite() || BoundaryMonotonicSampleSeconds < 0.0 {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "time.monotonic returned an invalid value",
        ));
    }
    let RemainingSeconds = if DeadlineAtMonotonicSeconds <= BoundaryMonotonicSampleSeconds {
        0.0
    } else {
        NextDownPositive(DeadlineAtMonotonicSeconds - BoundaryMonotonicSampleSeconds)
    };
    let WholeSeconds = RemainingSeconds.trunc();
    if WholeSeconds > u64::MAX as f64 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "deadline monotonic seconds are out of range",
        ));
    }
    let Seconds = WholeSeconds as u64;
    let Nanoseconds = ((RemainingSeconds - WholeSeconds) * 1_000_000_000.0).floor() as u32;
    let Remaining = Duration::new(Seconds, Nanoseconds);
    if Remaining.as_nanos() > u128::from(u64::MAX) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "deadline monotonic seconds are out of range",
        ));
    }
    let NativeRemainingNanoseconds = Remaining.as_nanos() as u64;
    let Deadline = RuntimeDeadline::FromEarlierSample(NativeSample, Remaining)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok((
        Deadline,
        NativeSample,
        BoundaryMonotonicSampleSeconds,
        NativeRemainingNanoseconds,
    ))
}

pub(crate) fn RegisterBatchOutcomeTypes(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Module.add_class::<RouteTreeCoarseRequestV1>()?;
    Module.add_class::<RouteTreeDetailedRequestV1>()?;
    Module.add_class::<RouteTreeNoPathProofV1>()?;
    Module.add_class::<RouteTreeRequestReceiptV1>()?;
    Module.add_class::<RouteTreeBatchOutcomesV1>()?;
    Ok(())
}

#[cfg(test)]
mod Tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    const A: Position = (0, 0, 0);
    const B: Position = (1, 0, 0);
    const C: Position = (2, 0, 0);

    fn LinearContext() -> RoutingContext {
        RoutingContext::FromMaps(
            HashMap::from([(A, vec![B]), (B, vec![A, C]), (C, vec![B])]),
            HashMap::from([((0, 0), vec![A]), ((1, 0), vec![B]), ((2, 0), vec![C])]),
        )
    }

    fn DisconnectedContext() -> RoutingContext {
        RoutingContext::FromMaps(
            HashMap::from([(A, Vec::new()), (C, Vec::new())]),
            HashMap::from([((0, 0), vec![A]), ((2, 0), vec![C])]),
        )
    }

    fn CanonicalRequest(AllowedNodes: Vec<Position>) -> CanonicalRouteRequestV1 {
        let RouteDomainScopeCanonicalJson: Arc<str> = Arc::from("[]");
        let CallerEchoScopeCanonicalJson: Arc<str> = Arc::from("[]");
        CanonicalRouteRequestV1 {
            RequestKind: DETAILED_REQUEST_KIND,
            RequestId: Arc::from("request"),
            CancellationRequestedBeforeStart: false,
            Starts: Arc::new(vec![A]),
            TargetBranches: Arc::new(vec![vec![C]]),
            AllowedNodes: Arc::new(AllowedNodes),
            BlockedNodes: Arc::new(Vec::new()),
            PreferredColumns: Arc::new(Vec::new()),
            NodeCosts: Arc::new(Vec::new()),
            PreferredRoutingY: 0,
            GuidePenalty: 0,
            BendPenalty: 0,
            ViaPenalty: 0,
            EnforceSignalStrength: false,
            MaximumExpansionCount: 32,
            RouteDomainScopeSha256: Arc::from(NativeSha256(&RouteDomainScopeCanonicalJson)),
            RouteDomainScopeCanonicalJson,
            CallerEchoScopeSha256: Arc::from(NativeSha256(&CallerEchoScopeCanonicalJson)),
            CallerEchoScopeCanonicalJson,
        }
    }

    fn ValidCandidate() -> RouteTreeSearchResult {
        RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: vec![A, B, C],
            TargetPaths: vec![(C, vec![A, B, C])],
            BoundaryFrontierNodes: Vec::new(),
            RepeaterReservations: Vec::new(),
            ExpansionCount: 3,
            RepeaterRejectedCount: 0,
            RepeaterConstraintFailureCount: 0,
            ConflictResources: Vec::new(),
            RejectedPathCount: 0,
            NoGoodCount: 0,
            ElapsedMilliseconds: 0,
            IsRouted: true,
            IsBudgetExpired: false,
        }
    }

    fn ValidProof() -> RouteTreeNoPathProofV1 {
        RouteTreeNoPathProofV1 {
            ProofKind: "RequiredAttachmentDisconnectedInRelaxedGraphV1".to_string(),
            UnreachableTargetBranchOrdinals: vec![0],
            UnreachableAttachmentNodes: vec![C],
            ReachedNodeCount: 1,
            ExpansionCount: 1,
            Complete: true,
            ClaimScope: "OneOriginalRouteRequest".to_string(),
            ContextGraphSha256: String::new(),
            RouteDomainScopeSha256: String::new(),
            ImmutableInputSha256: String::new(),
            ReceiptScopeSha256: String::new(),
        }
    }

    fn CallerBindings() -> Vec<(String, String)> {
        REQUIRED_CALLER_BINDINGS
            .iter()
            .map(|Name| ((*Name).to_string(), format!("{Name}-value")))
            .collect()
    }

    fn OriginalDetailed(MaximumExpansionCount: usize) -> AuthoritativeRouteRequestV1 {
        OriginalDetailedWith("request", MaximumExpansionCount, false)
    }

    fn OriginalDetailedWith(
        RequestId: &str,
        MaximumExpansionCount: usize,
        CancellationRequestedBeforeStart: bool,
    ) -> AuthoritativeRouteRequestV1 {
        RouteTreeDetailedRequestV1::New(
            RequestId.to_string(),
            CallerBindings(),
            (0, 0, 0, 2, 0, 0),
            (0, 0, 2, 0),
            CancellationRequestedBeforeStart,
            vec![A],
            vec![vec![C]],
            vec![A, B, C],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            MaximumExpansionCount,
        )
        .AuthoritativeRequest()
    }

    fn PendingFoundBatch(
        Context: &RoutingContext,
        Candidate: RouteTreeSearchResult,
    ) -> PendingBatchOutcomesV1 {
        let BatchIdentity = RetainedBatchIdentityV1::Owned("batch");
        let Original = OriginalDetailed(32);
        let ImmutableInput = ImmutableInputCanonicalJson(&Original);
        let ImmutableSha = ImmutableInputSha256(&Original);
        let Canonical = NormalizeRequest(Context, Original.clone(), &RuntimeDeadline::Unlimited())
            .expect("test request is supported");
        let Canonical = Arc::new(Canonical);
        let mut Receipt = EmptyReceipt(
            BatchIdentity.clone(),
            DETAILED_REQUEST_KIND,
            Arc::from("request"),
            0,
            32,
            "Found",
            "Completed",
            PendingIdentityV1::Verified(
                Canonical.RouteDomainScopeCanonicalJson.clone(),
                Canonical.RouteDomainScopeSha256.clone(),
            ),
            PendingIdentityV1::Verified(ImmutableInput, ImmutableSha),
            PendingIdentityV1::Verified(
                Canonical.CallerEchoScopeCanonicalJson.clone(),
                Canonical.CallerEchoScopeSha256.clone(),
            ),
        );
        Receipt.SearchOutcome = "Found".to_string();
        Receipt.Started = true;
        Receipt.StartedAtMicroseconds = Some(0);
        Receipt.CompletedAtMicroseconds = Some(0);
        Receipt.RouteExpansionCount = 3;
        Receipt.Candidate = Some(Candidate);
        Receipt.OriginalRequest = Some(Original);
        Receipt.CanonicalRequest = Some(Canonical);
        Receipt.SealProducerFacts();
        PendingBatchOutcomesV1 {
            BatchIdentity,
            DeadlineAtMonotonicSeconds: 100.0,
            BoundaryMonotonicSampleSeconds: 90.0,
            NativeRemainingNanoseconds: 10_000_000_000,
            ContextGraphCanonicalJson: ContextCanonicalJson(Context),
            ContextGraphSha256: Context.AuthoritativeIdentityV1.Sha256.clone(),
            Receipts: vec![Receipt],
            Deadline: RuntimeDeadline::Unlimited(),
        }
    }

    #[test]
    fn PositiveNextDownIsStrictlyConservative() {
        let Value = 0.000_000_5;
        let Rounded = NextDownPositive(Value);
        assert!(Rounded < Value);
        assert!(Rounded > 0.0);
    }

    #[test]
    fn NativeSha256MatchesStandardVectors() {
        assert_eq!(
            NativeSha256(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        );
        assert_eq!(
            NativeSha256("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        );
    }

    #[test]
    fn BatchRequestOwnershipSharesSealedLargeBuffers() {
        let Request = RouteTreeCoarseRequestV1::New(
            "shared-request".to_string(),
            CallerBindings(),
            (0, 0, 0, 2, 0, 0),
            (0, 0, 2, 0),
            false,
            vec![A],
            vec![vec![C]],
            vec![(0, 0), (1, 0), (2, 0)],
            Vec::new(),
            Vec::new(),
            (0..100_000).map(|Index| (Index, 0)).collect(),
            0,
            0,
            0,
            0,
            32,
        );
        let SealedPointer = Arc::as_ptr(&Request.Sealed);
        let PreferredBuffer = Request.Sealed.PreferredColumns.as_ptr();
        let Before = Arc::strong_count(&Request.Sealed);
        let Authoritative = Request.AuthoritativeRequest();
        let AuthoritativeRouteRequestV1::Coarse(Sealed) = Authoritative else {
            unreachable!();
        };

        assert_eq!(Arc::as_ptr(&Sealed), SealedPointer);
        assert_eq!(Sealed.PreferredColumns.as_ptr(), PreferredBuffer);
        assert_eq!(Arc::strong_count(&Sealed), Before + 1);
    }

    #[test]
    fn DeadlineCheckedCanonicalWriterPublishesNoPartialIdentity() {
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let Values = vec![(1, 2, 3); DEADLINE_CHECK_INTERVAL * 8];

        assert_eq!(
            CanonicalJsonAndShaWithDeadline(&Values, &Deadline, true),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );
        assert!(Deadline.WasExceeded());
    }

    #[test]
    fn CompleteCandidateValidatorRejectsEveryReturnedStructureCorruption() {
        let Context = LinearContext();
        let Request = CanonicalRequest(vec![A, B, C]);
        let Candidate = ValidCandidate();
        assert!(ValidateFoundCandidate(&Context, &Request, &Candidate));

        let mut DuplicateNode = Candidate.clone();
        DuplicateNode.Nodes.push(C);
        assert!(!ValidateFoundCandidate(&Context, &Request, &DuplicateNode));

        let mut MissingPathNode = Candidate.clone();
        MissingPathNode.Nodes.remove(1);
        assert!(!ValidateFoundCandidate(
            &Context,
            &Request,
            &MissingPathNode
        ));

        let mut ExtraPath = Candidate.clone();
        ExtraPath.TargetPaths.push((B, vec![A, B]));
        assert!(!ValidateFoundCandidate(&Context, &Request, &ExtraPath));

        let mut ForeignExtra = Candidate.clone();
        ForeignExtra.Nodes.push((99, 0, 0));
        assert!(!ValidateFoundCandidate(&Context, &Request, &ForeignExtra));

        let mut MissingTarget = Candidate.clone();
        MissingTarget.TargetPaths.clear();
        assert!(!ValidateFoundCandidate(&Context, &Request, &MissingTarget));
    }

    #[test]
    fn CompleteCandidateValidatorRejectsForestRootedAtMultipleAllowedStarts() {
        let D = (10, 0, 0);
        let mut Context = LinearContext();
        Context.Adjacency.insert(D, Vec::new());
        Context.NodesByColumn.insert((10, 0), vec![D]);
        let mut Request = CanonicalRequest(vec![A, B, C, D]);
        Request.Starts = Arc::new(vec![A, D]);
        Request.TargetBranches = Arc::new(vec![vec![C], vec![D]]);
        let mut Candidate = ValidCandidate();
        Candidate.Nodes.push(D);
        Candidate.TargetPaths.push((D, vec![D]));

        assert!(!ValidateFoundCandidate(&Context, &Request, &Candidate));
    }

    #[test]
    fn CompleteCandidateValidatorRejectsMalformedRepeater() {
        let Context = LinearContext();
        let mut Request = CanonicalRequest(vec![A, B, C]);
        Request.EnforceSignalStrength = true;
        let mut Candidate = ValidCandidate();
        Candidate.RepeaterReservations = vec![(B, "north".to_string())];
        assert!(!ValidateFoundCandidate(&Context, &Request, &Candidate));
    }

    #[test]
    fn ClosedReachableWitnessValidatorRejectsProofFieldAndClosureCorruption() {
        let Context = DisconnectedContext();
        let Request = CanonicalRequest(vec![A, C]);
        let Admission = RequestExpansionAdmissionV1::New(2);
        assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Proof));
        let Proof = ValidProof();
        assert_eq!(
            ValidateNoPathProofWithDeadline(
                &Context,
                &Request,
                &Proof,
                &[A],
                Admission.ProofCount(),
                &RuntimeDeadline::Unlimited(),
            ),
            FinalValidationResultV1::Valid,
        );

        let mut WrongPair = Proof.clone();
        WrongPair.UnreachableAttachmentNodes = vec![A];
        assert_eq!(
            ValidateNoPathProofWithDeadline(
                &Context,
                &Request,
                &WrongPair,
                &[A],
                Admission.ProofCount(),
                &RuntimeDeadline::Unlimited(),
            ),
            FinalValidationResultV1::Invalid,
        );

        let mut WrongCount = Proof.clone();
        WrongCount.ReachedNodeCount = 2;
        assert_eq!(
            ValidateNoPathProofWithDeadline(
                &Context,
                &Request,
                &WrongCount,
                &[A],
                Admission.ProofCount(),
                &RuntimeDeadline::Unlimited(),
            ),
            FinalValidationResultV1::Invalid,
        );

        for Mutated in [
            {
                let mut Value = Proof.clone();
                Value.ProofKind = "WrongProofKind".to_string();
                Value
            },
            {
                let mut Value = Proof.clone();
                Value.Complete = false;
                Value
            },
            {
                let mut Value = Proof.clone();
                Value.ClaimScope = "Global".to_string();
                Value
            },
            {
                let mut Value = Proof.clone();
                Value.UnreachableTargetBranchOrdinals = vec![1];
                Value
            },
            {
                let mut Value = Proof.clone();
                Value.ExpansionCount = 0;
                Value
            },
        ] {
            assert_eq!(
                ValidateNoPathProofWithDeadline(
                    &Context,
                    &Request,
                    &Mutated,
                    &[A],
                    Admission.ProofCount(),
                    &RuntimeDeadline::Unlimited(),
                ),
                FinalValidationResultV1::Invalid,
            );
        }

        assert_eq!(
            ValidateNoPathProofWithDeadline(
                &LinearContext(),
                &CanonicalRequest(vec![A, B, C]),
                &Proof,
                &[A],
                Admission.ProofCount(),
                &RuntimeDeadline::Unlimited(),
            ),
            FinalValidationResultV1::Invalid,
        );
    }

    #[test]
    fn RelaxedProofPhaseCannotOutliveSharedDeadline() {
        let Context = DisconnectedContext();
        let Request = CanonicalRequest(vec![A, C]);
        let Admission = RequestExpansionAdmissionV1::New(32);
        assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Route));
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        assert!(matches!(
            RelaxedConnectivityProof(&Context, &Request, &Admission, &Deadline),
            RelaxedConnectivityResultV1::DeadlineExhausted,
        ));
        assert_eq!(Admission.RouteCount(), 1);
        assert_eq!(Admission.ProofCount(), 0);
    }

    #[test]
    fn ActualFinalizerDowngradesCorruptedCandidateBeforeExposure() {
        let Context = LinearContext();
        let mut Candidate = ValidCandidate();
        Candidate.TargetPaths.push((B, vec![A, B]));
        let Result =
            FinalizeRouteTreeBatchOutcomesV1(PendingFoundBatch(&Context, Candidate), &Context)
                .expect("an attributable producer corruption becomes a receipt");
        assert_eq!(Result.Receipts[0].SearchOutcome, "Incomplete");
        assert_eq!(Result.Receipts[0].TerminalReason, "InvalidProducerResult");
        assert!(Result.Receipts[0].Candidate.is_none());
        assert!(Result.Receipts[0].NoPathProof.is_none());
    }

    #[test]
    fn ActualFinalizerDowngradesCorruptedImmutableEnvelopeBeforeExposure() {
        let Context = LinearContext();
        let mut Pending = PendingFoundBatch(&Context, ValidCandidate());
        Pending.Receipts[0].RequestId = Arc::from("corrupted-request-id");
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
            .expect("an attributable immutable-envelope corruption becomes a receipt");
        assert_eq!(Result.Receipts[0].SearchOutcome, "Incomplete");
        assert_eq!(Result.Receipts[0].TerminalReason, "InvalidProducerResult");
        assert_eq!(Result.Receipts[0].RequestId.as_ref(), "request");
        assert!(Result.Receipts[0].Candidate.is_none());
        assert!(Result.Receipts[0].NoPathProof.is_none());
    }

    #[test]
    fn ActualFinalizerRejectsCorruptedBatchContextIdentity() {
        let Context = LinearContext();
        let mut Pending = PendingFoundBatch(&Context, ValidCandidate());
        Pending.ContextGraphCanonicalJson = Arc::from("[]");
        assert!(FinalizeRouteTreeBatchOutcomesV1(Pending, &Context).is_err());
    }

    #[test]
    fn ActualFinalizerDowngradesCandidateWorkCountMismatchBeforeExposure() {
        let Context = LinearContext();
        let mut Candidate = ValidCandidate();
        Candidate.ExpansionCount = 2;
        let Result =
            FinalizeRouteTreeBatchOutcomesV1(PendingFoundBatch(&Context, Candidate), &Context)
                .expect("an attributable candidate count mismatch becomes a receipt");
        assert_eq!(Result.Receipts[0].SearchOutcome, "Incomplete");
        assert_eq!(Result.Receipts[0].TerminalReason, "InvalidProducerResult");
        assert!(Result.Receipts[0].Candidate.is_none());
    }

    #[test]
    fn ActualFinalizerDeadlineClearsValidatedCandidateBeforeExposure() {
        let Context = LinearContext();
        let mut Pending = PendingFoundBatch(&Context, ValidCandidate());
        Pending.Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
            .expect("deadline finalization becomes an unresolved receipt");
        assert_eq!(Result.Receipts[0].SearchOutcome, "Incomplete");
        assert_eq!(
            Result.Receipts[0].TerminalReason,
            "DeadlineExhaustedDuringFinalization"
        );
        assert!(Result.Receipts[0].Candidate.is_none());
    }

    #[test]
    fn ActualFinalizerDowngradesCorruptedProofBeforeExposure() {
        let Context = DisconnectedContext();
        let BatchIdentity = RetainedBatchIdentityV1::Owned("proof-batch");
        let Original = RouteTreeDetailedRequestV1::New(
            "request".to_string(),
            CallerBindings(),
            (0, 0, 0, 2, 0, 0),
            (0, 0, 2, 0),
            false,
            vec![A],
            vec![vec![C]],
            vec![A, C],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            2,
        )
        .AuthoritativeRequest();
        let ImmutableInput = ImmutableInputCanonicalJson(&Original);
        let ImmutableSha = ImmutableInputSha256(&Original);
        let Canonical = NormalizeRequest(&Context, Original.clone(), &RuntimeDeadline::Unlimited())
            .expect("test request is supported");
        let Canonical = Arc::new(Canonical);
        let mut Proof = ValidProof();
        Proof.UnreachableTargetBranchOrdinals = vec![1];
        let mut Receipt = EmptyReceipt(
            BatchIdentity.clone(),
            DETAILED_REQUEST_KIND,
            Arc::from("request"),
            0,
            2,
            "RelaxedGraphDisconnected",
            "Completed",
            PendingIdentityV1::Verified(
                Canonical.RouteDomainScopeCanonicalJson.clone(),
                Canonical.RouteDomainScopeSha256.clone(),
            ),
            PendingIdentityV1::Verified(ImmutableInput, ImmutableSha),
            PendingIdentityV1::Verified(
                Canonical.CallerEchoScopeCanonicalJson.clone(),
                Canonical.CallerEchoScopeSha256.clone(),
            ),
        );
        Receipt.SearchOutcome = "ProvenNoPath".to_string();
        Receipt.Started = true;
        Receipt.StartedAtMicroseconds = Some(0);
        Receipt.CompletedAtMicroseconds = Some(0);
        Receipt.RouteExpansionCount = 1;
        Receipt.ProofExpansionCount = 1;
        Receipt.NoPathProof = Some(Proof);
        Receipt.ProofReachedNodes = vec![A];
        Receipt.OriginalRequest = Some(Original);
        Receipt.CanonicalRequest = Some(Canonical);
        Receipt.SealProducerFacts();
        let Pending = PendingBatchOutcomesV1 {
            BatchIdentity,
            DeadlineAtMonotonicSeconds: 100.0,
            BoundaryMonotonicSampleSeconds: 90.0,
            NativeRemainingNanoseconds: 10_000_000_000,
            ContextGraphCanonicalJson: ContextCanonicalJson(&Context),
            ContextGraphSha256: Context.AuthoritativeIdentityV1.Sha256.clone(),
            Receipts: vec![Receipt],
            Deadline: RuntimeDeadline::Unlimited(),
        };
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
            .expect("an attributable producer corruption becomes a receipt");
        assert_eq!(Result.Receipts[0].SearchOutcome, "Incomplete");
        assert_eq!(Result.Receipts[0].TerminalReason, "InvalidProducerResult");
        assert!(Result.Receipts[0].NoPathProof.is_none());
    }

    #[test]
    fn ActualFinalizerRejectsOrdinalAndAggregateOverflow() {
        let Context = LinearContext();
        let mut WrongOrdinal = PendingFoundBatch(&Context, ValidCandidate());
        WrongOrdinal.Receipts[0].OriginalOrdinal = 1;
        assert!(FinalizeRouteTreeBatchOutcomesV1(WrongOrdinal, &Context).is_err());

        let Original = OriginalDetailed(usize::MAX);
        let BatchIdentity = RetainedBatchIdentityV1::Owned("overflow-batch");
        let ImmutableInput = ImmutableInputCanonicalJson(&Original);
        let ImmutableSha = ImmutableInputSha256(&Original);
        let Canonical = NormalizeRequest(&Context, Original.clone(), &RuntimeDeadline::Unlimited())
            .expect("test request is supported");
        let Canonical = Arc::new(Canonical);
        let BuildReceipt = |Ordinal| {
            let mut Receipt = EmptyReceipt(
                BatchIdentity.clone(),
                DETAILED_REQUEST_KIND,
                Arc::from("request"),
                Ordinal,
                usize::MAX,
                "WorkCapExhausted",
                "Search",
                PendingIdentityV1::Verified(
                    Canonical.RouteDomainScopeCanonicalJson.clone(),
                    Canonical.RouteDomainScopeSha256.clone(),
                ),
                PendingIdentityV1::Verified(ImmutableInput.clone(), ImmutableSha.clone()),
                PendingIdentityV1::Verified(
                    Canonical.CallerEchoScopeCanonicalJson.clone(),
                    Canonical.CallerEchoScopeSha256.clone(),
                ),
            );
            Receipt.Started = true;
            Receipt.StartedAtMicroseconds = Some(0);
            Receipt.CompletedAtMicroseconds = Some(0);
            Receipt.RouteExpansionCount = usize::MAX / 2 + 1;
            Receipt.OriginalRequest = Some(Original.clone());
            Receipt.CanonicalRequest = Some(Canonical.clone());
            Receipt.SealProducerFacts();
            Receipt
        };
        let Receipts = vec![BuildReceipt(0), BuildReceipt(1)];
        let Pending = PendingBatchOutcomesV1 {
            BatchIdentity,
            DeadlineAtMonotonicSeconds: 100.0,
            BoundaryMonotonicSampleSeconds: 90.0,
            NativeRemainingNanoseconds: 10_000_000_000,
            ContextGraphCanonicalJson: ContextCanonicalJson(&Context),
            ContextGraphSha256: Context.AuthoritativeIdentityV1.Sha256.clone(),
            Receipts,
            Deadline: RuntimeDeadline::Unlimited(),
        };
        assert!(FinalizeRouteTreeBatchOutcomesV1(Pending, &Context).is_err());
    }

    #[test]
    fn PanicPreservesChargedWorkOutsideUnwindBoundary() {
        let Receipt = ExecuteRequestWorkerBoundaryV1(
            "batch-panic",
            DETAILED_REQUEST_KIND,
            "panic-request",
            0,
            2,
            "[]".to_string(),
            "[]".to_string(),
            Instant::now(),
            |Admission, StartedAt| {
                StartedAt.store(0, Ordering::Relaxed);
                assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Route));
                panic!("deliberate worker failure");
            },
        );
        assert_eq!(Receipt.SearchOutcome, "Incomplete");
        assert_eq!(Receipt.TerminalReason, "WorkerFailure");
        assert!(Receipt.Started);
        assert_eq!(Receipt.RouteExpansionCount, 1);
        assert_eq!(Receipt.ProofExpansionCount, 0);
        assert!(Receipt.Candidate.is_none());
        assert!(Receipt.NoPathProof.is_none());
    }

    #[test]
    fn IndexedParallelCollectionPreservesSlotsAcrossPermutedCompletion() {
        let CompletionOrder = Arc::new(Mutex::new(Vec::new()));
        let Pool = rayon::ThreadPoolBuilder::new()
            .num_threads(2)
            .build()
            .expect("two-thread test pool builds");
        let Results: Vec<_> = Pool.install(|| {
            [0usize, 1usize]
                .into_par_iter()
                .map(|Ordinal| {
                    if Ordinal == 0 {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    CompletionOrder.lock().unwrap().push(Ordinal);
                    Ordinal
                })
                .collect()
        });
        assert_eq!(*CompletionOrder.lock().unwrap(), vec![1, 0]);
        assert_eq!(Results, vec![0, 1]);
    }

    #[test]
    fn CaughtWorkerFailureDoesNotEraseSiblingReceipt() {
        let Receipts: Vec<_> = [0usize, 1usize]
            .into_par_iter()
            .map(|Ordinal| {
                ExecuteRequestWorkerBoundaryV1(
                    "sibling-batch",
                    DETAILED_REQUEST_KIND,
                    if Ordinal == 0 { "failed" } else { "sibling" },
                    Ordinal,
                    1,
                    "[]".to_string(),
                    "[]".to_string(),
                    Instant::now(),
                    |Admission, StartedAt| {
                        if Ordinal == 0 {
                            StartedAt.store(0, Ordering::Relaxed);
                            assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Route));
                            panic!("deliberate worker failure");
                        }
                        EmptyReceipt(
                            RetainedBatchIdentityV1::Owned("sibling-batch"),
                            DETAILED_REQUEST_KIND,
                            Arc::from("sibling"),
                            1,
                            1,
                            "WorkCapExhausted",
                            "Search",
                            PendingIdentityV1::Verified(
                                Arc::from("[]"),
                                Arc::from(NativeSha256("[]")),
                            ),
                            PendingIdentityV1::Verified(
                                Arc::from("[]"),
                                Arc::from(NativeSha256("[]")),
                            ),
                            PendingIdentityV1::Verified(
                                Arc::from("[]"),
                                Arc::from(NativeSha256("[]")),
                            ),
                        )
                    },
                )
            })
            .collect();
        assert_eq!(Receipts[0].TerminalReason, "WorkerFailure");
        assert_eq!(Receipts[0].RouteExpansionCount, 1);
        assert_eq!(Receipts[1].RequestId.as_ref(), "sibling");
        assert_eq!(Receipts[1].TerminalReason, "WorkCapExhausted");
    }

    fn PendingForIntegrityTest(
        Context: &RoutingContext,
        BatchIdentity: &str,
        Request: AuthoritativeRouteRequestV1,
        Deadline: RuntimeDeadline,
    ) -> PendingBatchOutcomesV1 {
        GenerateRouteTreeBatchOutcomesNativeV1(
            Context,
            RetainedBatchIdentityV1::Owned(BatchIdentity),
            vec![Request],
            100.0,
            90.0,
            10_000_000_000,
            Deadline,
            Instant::now(),
        )
    }

    #[test]
    fn PostNormalizationPanicRetainsCanonicalScopeThroughActualFinalizer() {
        let Context = LinearContext();
        let Original = OriginalDetailed(32);
        let Expected = NormalizeRequest(&Context, Original.clone(), &RuntimeDeadline::Unlimited())
            .expect("integrity request is valid");
        *TEST_PANIC_AFTER_NORMALIZATION_BATCH
            .lock()
            .expect("test panic selector lock") = Some(("post-normalization-panic", 0));
        let Pending = GenerateRouteTreeBatchOutcomesNativeV1(
            &Context,
            RetainedBatchIdentityV1::Owned("post-normalization-panic"),
            vec![Original, OriginalDetailedWith("sibling", 0, false)],
            100.0,
            90.0,
            10_000_000_000,
            RuntimeDeadline::Unlimited(),
            Instant::now(),
        );
        *TEST_PANIC_AFTER_NORMALIZATION_BATCH
            .lock()
            .expect("test panic selector lock") = None;
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
            .expect("attributable worker failure finalizes");
        let Receipt = &Result.Receipts[0];

        assert_eq!(Receipt.TerminalReason, "WorkerFailure");
        assert_eq!(
            Receipt.RouteDomainScopeCanonicalJson.as_deref(),
            Some(Expected.RouteDomainScopeCanonicalJson.as_ref()),
        );
        assert_eq!(
            Receipt.CallerEchoScopeCanonicalJson.as_deref(),
            Some(Expected.CallerEchoScopeCanonicalJson.as_ref()),
        );
        assert!(Receipt.Candidate.is_none());
        assert!(Receipt.NoPathProof.is_none());
        assert!(Receipt.Started);
        assert_eq!(Receipt.RouteExpansionCount, 1);
        assert_eq!(Result.Receipts[1].RequestId.as_ref(), "sibling");
        assert_eq!(Result.Receipts[1].TerminalReason, "WorkCapExhausted");
    }

    #[test]
    fn NormalizationPanicRetainsRawIdentityWithUnavailableDomain() {
        let Context = LinearContext();
        *TEST_PANIC_DURING_NORMALIZATION_BATCH
            .lock()
            .expect("test normalization panic selector lock") = Some(("normalization-panic", 0));
        let Pending = GenerateRouteTreeBatchOutcomesNativeV1(
            &Context,
            RetainedBatchIdentityV1::Owned("normalization-panic"),
            vec![OriginalDetailed(32)],
            100.0,
            90.0,
            10_000_000_000,
            RuntimeDeadline::Unlimited(),
            Instant::now(),
        );
        *TEST_PANIC_DURING_NORMALIZATION_BATCH
            .lock()
            .expect("test normalization panic selector lock") = None;
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
            .expect("normalization failure remains attributable");
        let Receipt = &Result.Receipts[0];

        assert_eq!(Receipt.TerminalReason, "WorkerFailure");
        assert_eq!(Receipt.OutcomePhase, "DomainCanonicalization");
        assert_eq!(Receipt.RouteDomainIdentityAvailability, PRODUCER_FAILURE);
        assert!(Receipt.RouteDomainScopeCanonicalJson.is_none());
        assert!(Receipt.RouteDomainScopeSha256.is_none());
        assert_eq!(Receipt.ImmutableInputIdentityAvailability, VERIFIED);
        assert_eq!(Receipt.CallerEchoIdentityAvailability, VERIFIED);
        assert!(!Receipt.Started);
        assert_eq!(Receipt.TotalExpansionCount, 0);
        assert!(Receipt.Candidate.is_none());
        assert!(Receipt.NoPathProof.is_none());
    }

    #[test]
    fn ActualFinalizerRejectsCorruptedIncompleteScopes() {
        let Context = LinearContext();
        let Cancelled = OriginalDetailedWith("cancelled", 32, true);
        let mut Inputs = vec![
            PendingForIntegrityTest(
                &Context,
                "corrupt-cap",
                OriginalDetailed(0),
                RuntimeDeadline::Unlimited(),
            ),
            PendingForIntegrityTest(
                &Context,
                "corrupt-cancel",
                Cancelled,
                RuntimeDeadline::Unlimited(),
            ),
            PendingForIntegrityTest(
                &Context,
                "corrupt-deadline",
                OriginalDetailed(32),
                RuntimeDeadline::FromMilliseconds(Some(0)).unwrap(),
            ),
        ];
        *TEST_PANIC_AFTER_NORMALIZATION_BATCH
            .lock()
            .expect("test panic selector lock") = Some(("corrupt-worker", 0));
        Inputs.push(PendingForIntegrityTest(
            &Context,
            "corrupt-worker",
            OriginalDetailed(32),
            RuntimeDeadline::Unlimited(),
        ));
        *TEST_PANIC_AFTER_NORMALIZATION_BATCH
            .lock()
            .expect("test panic selector lock") = None;

        for mut Pending in Inputs {
            Pending.Receipts[0].RouteDomainIdentity.CanonicalJson =
                Some(Arc::from("[\"forged-domain\"]"));
            Pending.Receipts[0].CallerEchoIdentity.CanonicalJson =
                Some(Arc::from("[\"forged-echo\"]"));
            let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context)
                .expect("attributable incomplete receipt finalizes");
            let Receipt = &Result.Receipts[0];
            assert_ne!(
                Receipt.RouteDomainScopeCanonicalJson.as_deref(),
                Some("[\"forged-domain\"]"),
            );
            assert_ne!(
                Receipt.CallerEchoScopeCanonicalJson.as_deref(),
                Some("[\"forged-echo\"]"),
            );
            assert!(Receipt.Candidate.is_none());
            assert!(Receipt.NoPathProof.is_none());
        }
    }

    fn InvalidOriginalDetailed() -> AuthoritativeRouteRequestV1 {
        RouteTreeDetailedRequestV1::New(
            "invalid".to_string(),
            CallerBindings(),
            (0, 0, 0, 99, 0, 0),
            (0, 0, 99, 0),
            false,
            vec![(99, 0, 0)],
            vec![vec![C]],
            vec![A, B, C, (99, 0, 0)],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            32,
        )
        .AuthoritativeRequest()
    }

    #[test]
    fn UnavailableScopeCorruptionCannotBecomeVerified() {
        let Context = LinearContext();
        let mut PendingValues = vec![
            PendingForIntegrityTest(
                &Context,
                "red-entry-unavailable",
                OriginalDetailed(32),
                RuntimeDeadline::FromMilliseconds(Some(0)).unwrap(),
            ),
            PendingForIntegrityTest(
                &Context,
                "red-unsupported-unavailable",
                InvalidOriginalDetailed(),
                RuntimeDeadline::Unlimited(),
            ),
        ];
        *TEST_PANIC_DURING_NORMALIZATION_BATCH.lock().unwrap() =
            Some(("red-normalization-unavailable", 0));
        PendingValues.push(PendingForIntegrityTest(
            &Context,
            "red-normalization-unavailable",
            OriginalDetailed(32),
            RuntimeDeadline::Unlimited(),
        ));
        *TEST_PANIC_DURING_NORMALIZATION_BATCH.lock().unwrap() = None;

        let mut Exposed = Vec::new();
        for mut Pending in PendingValues {
            let OriginalReason = Pending.Receipts[0].TerminalReason.clone();
            let OriginalAvailability = Pending.Receipts[0].RouteDomainIdentity.Availability;
            let Forged = Arc::<str>::from(format!("[\"forged-{OriginalReason}\"]"));
            Pending.Receipts[0].RouteDomainIdentity =
                PendingIdentityV1::Verified(Forged.clone(), Arc::from(NativeSha256(&Forged)));
            let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context).unwrap();
            let Receipt = &Result.Receipts[0];
            println!(
                "unavailable original_reason={OriginalReason} original_availability={OriginalAvailability} final_reason={} availability={} domain={:?} receipt_availability={}",
                Receipt.TerminalReason,
                Receipt.RouteDomainIdentityAvailability,
                Receipt.RouteDomainScopeCanonicalJson,
                Receipt.ReceiptIdentityAvailability,
            );
            Exposed.push((
                OriginalReason,
                Receipt.RouteDomainIdentityAvailability.clone(),
                Receipt.RouteDomainScopeCanonicalJson.clone(),
            ));
        }
        assert!(Exposed
            .iter()
            .all(|(_, Availability, Canonical)| Availability != VERIFIED && Canonical.is_none()));
    }

    #[test]
    fn IncompleteReasonAndPhaseMustBeCoherent() {
        let Context = LinearContext();
        let mut FalseCancelled = PendingForIntegrityTest(
            &Context,
            "red-false-cancelled",
            OriginalDetailed(0),
            RuntimeDeadline::Unlimited(),
        );
        FalseCancelled.Receipts[0].TerminalReason = "Cancelled".to_string();
        FalseCancelled.Receipts[0].OutcomePhase = "BogusPhase";
        let FalseCancelled = FinalizeRouteTreeBatchOutcomesV1(FalseCancelled, &Context).unwrap();
        let FalseCancelled = &FalseCancelled.Receipts[0];

        let mut MissingAcknowledgement = PendingForIntegrityTest(
            &Context,
            "red-missing-ack",
            OriginalDetailedWith("cancelled", 32, true),
            RuntimeDeadline::Unlimited(),
        );
        MissingAcknowledgement.Receipts[0].CancellationAcknowledged = false;
        MissingAcknowledgement.Receipts[0].SearchStopped = false;
        let MissingAcknowledgement =
            FinalizeRouteTreeBatchOutcomesV1(MissingAcknowledgement, &Context).unwrap();
        let MissingAcknowledgement = &MissingAcknowledgement.Receipts[0];

        let mut StartedUnsupported = PendingForIntegrityTest(
            &Context,
            "red-started-unsupported",
            InvalidOriginalDetailed(),
            RuntimeDeadline::Unlimited(),
        );
        StartedUnsupported.Receipts[0].Started = true;
        StartedUnsupported.Receipts[0].StartedAtMicroseconds = Some(0);
        let StartedUnsupported =
            FinalizeRouteTreeBatchOutcomesV1(StartedUnsupported, &Context).unwrap();
        let StartedUnsupported = &StartedUnsupported.Receipts[0];

        assert!(
            FalseCancelled.TerminalReason != "Cancelled"
                || (FalseCancelled.CancellationRequested
                    && FalseCancelled.CancellationAcknowledged
                    && FalseCancelled.SearchStopped
                    && FalseCancelled.OutcomePhase == "Completed")
        );
        assert!(
            MissingAcknowledgement.TerminalReason != "Cancelled"
                || (MissingAcknowledgement.CancellationAcknowledged
                    && MissingAcknowledgement.SearchStopped)
        );
        assert!(
            StartedUnsupported.TerminalReason != "UnsupportedRequest"
                || (!StartedUnsupported.Started
                    && StartedUnsupported.TotalExpansionCount == 0
                    && StartedUnsupported.OutcomePhase == "DomainCanonicalization")
        );
    }

    #[test]
    fn IncompleteFinalizationDeadlineMustOwnTheReasonAndPhase() {
        let Context = LinearContext();
        let mut Pending = PendingForIntegrityTest(
            &Context,
            "red-incomplete-finalization",
            OriginalDetailed(0),
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Pending.Receipts[0].TerminalReason, "WorkCapExhausted");
        Pending.Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let Result = FinalizeRouteTreeBatchOutcomesV1(Pending, &Context).unwrap();
        let Receipt = &Result.Receipts[0];
        assert_eq!(
            Receipt.TerminalReason,
            "DeadlineExhaustedDuringFinalization"
        );
        assert_eq!(Receipt.OutcomePhase, "ReceiptFinalization");
    }

    #[test]
    fn BranchEdgeValidationExpiresBeforeADeepInvalidEdge() {
        let Nodes: Vec<_> = (0..=64).map(|Index| (Index, 0, 0)).collect();
        let mut Adjacency = HashMap::new();
        for Node in &Nodes {
            Adjacency.insert(*Node, Vec::new());
        }
        for Pair in Nodes[..64].windows(2) {
            Adjacency.get_mut(&Pair[0]).unwrap().push(Pair[1]);
            Adjacency.get_mut(&Pair[1]).unwrap().push(Pair[0]);
        }
        let Context = RoutingContext::FromMaps(Adjacency, HashMap::new());
        let Deadline = RuntimeDeadline::FromCheckBudget(32);
        let mut ValidationIndex = 0;

        assert_eq!(
            ValidateBranchEdgesWithDeadline(&Context, &[Nodes], &Deadline, &mut ValidationIndex,),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );
    }

    #[test]
    fn OneBranchPairChecksHighDegreeForwardAndReverseNeighbors() {
        let Irrelevant: Vec<_> = (100..196).map(|Index| (Index, 0, 0)).collect();
        let mut ForwardNeighbors = Irrelevant.clone();
        ForwardNeighbors.push(B);
        let ForwardContext = RoutingContext::FromMaps(
            HashMap::from([(A, ForwardNeighbors), (B, Vec::new())]),
            HashMap::new(),
        );
        let ForwardDeadline = RuntimeDeadline::FromCheckBudget(2);
        let mut ForwardSteps = 0;
        assert_eq!(
            ValidateBranchEdgesWithDeadline(
                &ForwardContext,
                &[vec![A, B]],
                &ForwardDeadline,
                &mut ForwardSteps,
            ),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );

        let mut ReverseNeighbors = Irrelevant.clone();
        ReverseNeighbors.push(A);
        let ReverseContext = RoutingContext::FromMaps(
            HashMap::from([(A, Irrelevant[..40].to_vec()), (B, ReverseNeighbors)]),
            HashMap::new(),
        );
        let ReverseDeadline = RuntimeDeadline::FromCheckBudget(3);
        let mut ReverseSteps = 0;
        assert_eq!(
            ValidateBranchEdgesWithDeadline(
                &ReverseContext,
                &[vec![A, B]],
                &ReverseDeadline,
                &mut ReverseSteps,
            ),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );

        let mut EntrySteps = 0;
        assert_eq!(
            ValidateBranchEdgesWithDeadline(
                &ForwardContext,
                &[vec![A, B]],
                &RuntimeDeadline::FromCheckBudget(0),
                &mut EntrySteps,
            ),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );
        for Context in [&ForwardContext, &ReverseContext] {
            let mut Steps = 0;
            assert_eq!(
                ValidateBranchEdgesWithDeadline(
                    Context,
                    &[vec![A, B]],
                    &RuntimeDeadline::Unlimited(),
                    &mut Steps,
                ),
                Ok(()),
            );
        }
    }

    #[test]
    fn ManySingletonBranchesCheckpointOuterTraversal() {
        let Context = LinearContext();
        let Branches = vec![vec![C]; DEADLINE_CHECK_INTERVAL * 4];
        let Deadline = RuntimeDeadline::FromCheckBudget(1);
        let mut InterruptedProgress = 0;
        assert_eq!(
            ValidateBranchEdgesWithDeadline(
                &Context,
                &Branches,
                &Deadline,
                &mut InterruptedProgress,
            ),
            Err(RequestNormalizationErrorV1::DeadlineExhausted),
        );
        assert_eq!(InterruptedProgress, DEADLINE_CHECK_INTERVAL);

        let mut CompletedProgress = 0;
        assert_eq!(
            ValidateBranchEdgesWithDeadline(
                &Context,
                &Branches,
                &RuntimeDeadline::Unlimited(),
                &mut CompletedProgress,
            ),
            Ok(()),
        );
        assert_eq!(CompletedProgress, Branches.len());
    }

    #[test]
    fn HighDegreeCandidateEdgeAndProofClosureObserveCheckpointExpiry() {
        let Neighbors: Vec<_> = (1..=96).map(|Index| (Index, 0, 0)).collect();
        let Context =
            RoutingContext::FromMaps(HashMap::from([(A, Neighbors.clone())]), HashMap::new());
        let EdgeDeadline = RuntimeDeadline::FromCheckBudget(1);
        let mut EdgeSteps = 0;
        assert_eq!(
            CandidateEdgeIsValidWithDeadline(
                &Context,
                A,
                (97, 0, 0),
                &EdgeDeadline,
                &mut EdgeSteps,
            ),
            Err(FinalValidationResultV1::DeadlineExhausted),
        );

        let mut Request = CanonicalRequest(vec![A]);
        Request.TargetBranches = Arc::new(vec![vec![C]]);
        let Proof = RouteTreeNoPathProofV1 {
            ProofKind: "RequiredAttachmentDisconnectedInRelaxedGraphV1".to_string(),
            UnreachableTargetBranchOrdinals: vec![0],
            UnreachableAttachmentNodes: vec![C],
            ReachedNodeCount: 1,
            ExpansionCount: 1,
            Complete: true,
            ClaimScope: "OneOriginalRouteRequest".to_string(),
            ContextGraphSha256: String::new(),
            RouteDomainScopeSha256: String::new(),
            ImmutableInputSha256: String::new(),
            ReceiptScopeSha256: String::new(),
        };
        let ProofDeadline = RuntimeDeadline::FromCheckBudget(1);
        assert_eq!(
            ValidateNoPathProofWithDeadline(&Context, &Request, &Proof, &[A], 1, &ProofDeadline,),
            FinalValidationResultV1::DeadlineExhausted,
        );
    }

    #[test]
    fn DeadlineAwareElectricalHelpersMatchLegacyAndInterrupt() {
        let Context = LinearContext();
        let Nodes = HashSet::from([A, B, C]);
        let Repeaters = HashMap::new();
        let LegacyPowers = PropagateCanonicalRoutePower(A, &Nodes, &Repeaters, &Context.Adjacency);
        let DeadlinePowers = PropagateCanonicalRoutePowerWithDeadline(
            A,
            &Nodes,
            &Repeaters,
            &Context.Adjacency,
            &RuntimeDeadline::Unlimited(),
        );
        assert!(matches!(
            DeadlinePowers,
            DeadlineAwareElectricalResult::Complete(ref Powers) if Powers == &LegacyPowers
        ));
        let RepeaterValues = vec![(B, "west".to_string())];
        let LegacyCycles = FindSelfExcitingRepeaterCycles(&Nodes, &RepeaterValues);
        let DeadlineCycles = FindSelfExcitingRepeaterCyclesWithDeadline(
            &Nodes,
            &RepeaterValues,
            &RuntimeDeadline::Unlimited(),
        );
        assert!(matches!(
            DeadlineCycles,
            DeadlineAwareElectricalResult::Complete(ref Cycles) if Cycles == &LegacyCycles
        ));

        let LargeNeighbors: Vec<_> = (1..=96).map(|Index| (Index, 0, 0)).collect();
        let LargeNodes: HashSet<_> = std::iter::once(A)
            .chain(LargeNeighbors.iter().copied())
            .collect();
        let LargeAdjacency = HashMap::from([(A, LargeNeighbors)]);
        assert!(matches!(
            PropagateCanonicalRoutePowerWithDeadline(
                A,
                &LargeNodes,
                &HashMap::new(),
                &LargeAdjacency,
                &RuntimeDeadline::FromCheckBudget(1),
            ),
            DeadlineAwareElectricalResult::DeadlineExhausted,
        ));
        let ManyRepeaters: Vec<_> = (0..96)
            .map(|Index| ((Index, 0, 0), "west".to_string()))
            .collect();
        assert!(matches!(
            FindSelfExcitingRepeaterCyclesWithDeadline(
                &LargeNodes,
                &ManyRepeaters,
                &RuntimeDeadline::FromCheckBudget(1),
            ),
            DeadlineAwareElectricalResult::DeadlineExhausted,
        ));
    }

    #[test]
    fn ActualCandidateValidationTraversesElectricalChecks() {
        let Context = LinearContext();
        let mut Request = CanonicalRequest(vec![A, B, C]);
        Request.EnforceSignalStrength = true;
        assert_eq!(
            ValidateFoundCandidateWithDeadline(
                &Context,
                &Request,
                &ValidCandidate(),
                3,
                &RuntimeDeadline::FromCheckBudget(usize::MAX),
            ),
            FinalValidationResultV1::Valid,
        );
    }

    #[test]
    fn ExpiredCandidateValidationEntersNoProportionalPrelude() {
        let NodeCount = 750_000i32;
        let Nodes: Vec<_> = (0..NodeCount).map(|Index| (Index, 0, 0)).collect();
        let mut Request = CanonicalRequest(Nodes.clone());
        Request.TargetBranches = Arc::new(vec![vec![(NodeCount - 1, 0, 0)]]);
        let Candidate = RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: Nodes.clone(),
            TargetPaths: vec![((NodeCount - 1, 0, 0), Nodes)],
            BoundaryFrontierNodes: Vec::new(),
            RepeaterReservations: Vec::new(),
            ExpansionCount: 0,
            RepeaterRejectedCount: 0,
            RepeaterConstraintFailureCount: 0,
            ConflictResources: Vec::new(),
            RejectedPathCount: 0,
            NoGoodCount: 0,
            ElapsedMilliseconds: 0,
            IsRouted: true,
            IsBudgetExpired: false,
        };
        let Deadline = RuntimeDeadline::FromCheckBudget(1);
        let Result = ValidateFoundCandidateWithDeadline(
            &LinearContext(),
            &Request,
            &Candidate,
            0,
            &Deadline,
        );
        assert_eq!(Result, FinalValidationResultV1::DeadlineExhausted);
        assert!(Deadline.WasExceeded());
    }

    #[test]
    fn ExpiredProofValidationEntersNoProportionalPrelude() {
        let NodeCount = 750_000i32;
        let Nodes: Vec<_> = (0..NodeCount).map(|Index| (Index, 0, 0)).collect();
        let mut Request = CanonicalRequest(Nodes.clone());
        Request.TargetBranches = Arc::new(vec![vec![(NodeCount, 0, 0)]]);
        let Proof = RouteTreeNoPathProofV1 {
            ProofKind: "RequiredAttachmentDisconnectedInRelaxedGraphV1".to_string(),
            UnreachableTargetBranchOrdinals: vec![0],
            UnreachableAttachmentNodes: vec![(NodeCount, 0, 0)],
            ReachedNodeCount: Nodes.len(),
            ExpansionCount: Nodes.len(),
            Complete: true,
            ClaimScope: "OneOriginalRouteRequest".to_string(),
            ContextGraphSha256: String::new(),
            RouteDomainScopeSha256: String::new(),
            ImmutableInputSha256: String::new(),
            ReceiptScopeSha256: String::new(),
        };
        let Deadline = RuntimeDeadline::FromCheckBudget(1);
        let Result = ValidateNoPathProofWithDeadline(
            &LinearContext(),
            &Request,
            &Proof,
            &Nodes,
            Nodes.len(),
            &Deadline,
        );
        assert_eq!(Result, FinalValidationResultV1::DeadlineExhausted);
        assert!(Deadline.WasExceeded());
    }
}
