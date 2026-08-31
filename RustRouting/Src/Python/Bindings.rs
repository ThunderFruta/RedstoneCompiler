//! Stable Python surface for the native routing domains.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::*;
use crate::Core::Runtime::{GetRoutingThreadCount, RoutingThreadPool};
use crate::Escape::{
    AccessRegionGraphRecipeValue, AccessRegionGraphResultValue,
    BuildAccessRegionGraphCatalogWithDeadline, BuildDerivedEscapeStatePathsWithDeadline,
    BuildLayeredAccessEscapeViewCatalogWithDeadline,
    BuildLayeredEscapeStatePathCatalogWithDeadline, EscapeRequest, EscapeRequestResult,
    LayeredAccessEscapeGraphValue, LayeredAccessEscapeMemberValue,
    LayeredAccessEscapeSelectionResult, LayeredAccessGuideMemberValue,
    LayeredAccessGuideSelectionResult, LayeredEscapeMemberRequest, LayeredEscapeMemberResult,
    SolveLayeredAccessEscapeFactorCatalogWithDeadline,
    SolveLayeredAccessGuideFactorCatalogWithDeadline,
};
use crate::Generation::{
    GenerateAndAssignRouteTreesFactorizedNative, GeneratePortalCandidateBatchesNative,
    GenerateRouteTreeClaimAwareDetailedBatchNative, GenerateRouteTreeDetailedBatchNative,
    GenerateRouteTreesFactorizedNative, GenerateRouteTreesNative,
};
use crate::Geometry::ExteriorConnectors::{
    BuildFabricSubtreesBatchWithTelemetry, SearchExteriorConnectorsBatchWithTelemetry,
};
use crate::Geometry::RouteClaims::{
    BuildDeferredRouteClaimsBatchWithTelemetry, BuildRouteClaimsBatch,
    BuildRouteClaimsBatchWithTelemetry, GenerateRectilinearTopology,
};
use crate::Path::PathRouting::FindPath;
use crate::Planning::AssignmentPlanning::{
    AssignmentCandidateValue, BaseAssignmentValue, CompactClaimPrimitiveValue,
    CompactFactorMemberValue, DeadlineExceededAssignmentResult,
    PlanAuthoritativeRoutesWithBaseAndDeadline, PlanAuthoritativeRoutesWithDeadline,
    SolveCompactTemplateFactorCatalogWithDeadline as SolveCompactTemplateFactorCatalogNative,
    SolveTemplateAssignmentDomainsWithDeadline as SolveTemplateAssignmentDomainsNative,
    TemplateAssignmentDomainValue,
};
use crate::Planning::LeasePlanning::{
    LeaseCandidate, LeaseDomain, LeaseSolveStatus, SolveLeaseDomainsWithDeadline,
};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

fn ValidateAssignmentResourceCount(ResourceCount: usize) -> PyResult<()> {
    if ResourceCount == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "resource count must be positive",
        ));
    }
    Ok(())
}

fn ExtractIndexValuesWithDeadline(
    Values: &Bound<'_, PyAny>,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<Vec<usize>>> {
    if Deadline.Check() {
        return Ok(None);
    }
    let Count = Values.len()?;
    if Deadline.Check() {
        return Ok(None);
    }
    let mut Result = Vec::with_capacity(Count.min(4_096));
    for Index in 0..Count {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        Result.push(Values.get_item(Index)?.extract::<usize>()?);
    }
    if Deadline.Check() {
        return Ok(None);
    }
    Ok(Some(Result))
}

fn ExtractAssignmentCandidateValuesWithDeadline(
    Values: &Bound<'_, PyAny>,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<Vec<AssignmentCandidateValue>>> {
    if Deadline.Check() {
        return Ok(None);
    }
    let Count = Values.len()?;
    if Deadline.Check() {
        return Ok(None);
    }
    let mut Result = Vec::with_capacity(Count.min(1_024));
    for CandidateIndex in 0..Count {
        if CandidateIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let Candidate = Values.get_item(CandidateIndex)?;
        let CandidateLength = Candidate.len()?;
        if CandidateLength != 11 && CandidateLength != 12 && CandidateLength != 13 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "assignment candidate must contain 11, 12, or 13 values",
            ));
        }
        let Some(Wire) = ExtractIndexValuesWithDeadline(&Candidate.get_item(2)?, Deadline)? else {
            return Ok(None);
        };
        let Some(Support) = ExtractIndexValuesWithDeadline(&Candidate.get_item(3)?, Deadline)?
        else {
            return Ok(None);
        };
        let Some(Air) = ExtractIndexValuesWithDeadline(&Candidate.get_item(4)?, Deadline)? else {
            return Ok(None);
        };
        let Some(Electrical) = ExtractIndexValuesWithDeadline(&Candidate.get_item(5)?, Deadline)?
        else {
            return Ok(None);
        };
        if Deadline.Check() {
            return Ok(None);
        }
        Result.push((
            Candidate.get_item(0)?.extract::<String>()?,
            Candidate.get_item(1)?.extract::<String>()?,
            Wire,
            Support,
            Air,
            Electrical,
            Candidate.get_item(6)?.extract::<i32>()?,
            Candidate.get_item(7)?.extract::<i32>()?,
            Candidate.get_item(8)?.extract::<i32>()?,
            Candidate.get_item(9)?.extract::<i32>()?,
            Candidate.get_item(10)?.extract::<i32>()?,
            if CandidateLength == 12 {
                Candidate.get_item(11)?.extract::<String>()?
            } else if CandidateLength == 13 {
                Candidate.get_item(11)?.extract::<String>()?
            } else {
                String::new()
            },
            if CandidateLength == 13 {
                Candidate.get_item(12)?.extract::<String>()?
            } else {
                Candidate.get_item(0)?.extract::<String>()?
            },
        ));
    }
    if Deadline.Check() {
        return Ok(None);
    }
    Ok(Some(Result))
}

fn ExtractBaseAssignmentValuesWithDeadline(
    Values: &Bound<'_, PyAny>,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<Vec<BaseAssignmentValue>>> {
    if Deadline.Check() {
        return Ok(None);
    }
    let Count = Values.len()?;
    if Deadline.Check() {
        return Ok(None);
    }
    let mut Result = Vec::with_capacity(Count.min(1_024));
    for BaseIndex in 0..Count {
        if BaseIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let Base = Values.get_item(BaseIndex)?;
        if Base.len()? != 5 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "base assignment must contain exactly 5 values",
            ));
        }
        let Some(Wire) = ExtractIndexValuesWithDeadline(&Base.get_item(1)?, Deadline)? else {
            return Ok(None);
        };
        let Some(Support) = ExtractIndexValuesWithDeadline(&Base.get_item(2)?, Deadline)? else {
            return Ok(None);
        };
        let Some(Air) = ExtractIndexValuesWithDeadline(&Base.get_item(3)?, Deadline)? else {
            return Ok(None);
        };
        let Some(Electrical) = ExtractIndexValuesWithDeadline(&Base.get_item(4)?, Deadline)? else {
            return Ok(None);
        };
        if Deadline.Check() {
            return Ok(None);
        }
        Result.push((
            Base.get_item(0)?.extract::<String>()?,
            Wire,
            Support,
            Air,
            Electrical,
        ));
    }
    if Deadline.Check() {
        return Ok(None);
    }
    Ok(Some(Result))
}

fn ExtractTemplateAssignmentDomainsWithDeadline(
    Values: &Bound<'_, PyAny>,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<Vec<TemplateAssignmentDomainValue>>> {
    if Deadline.Check() {
        return Ok(None);
    }
    let Count = Values.len()?;
    let mut Result = Vec::with_capacity(Count.min(128));
    for Index in 0..Count {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let Domain = Values.get_item(Index)?;
        if Domain.len()? != 6 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "template assignment domain must contain exactly 6 values",
            ));
        }
        let ObjectiveValues = &Domain.get_item(1)?;
        let ObjectiveCount = ObjectiveValues.len()?;
        let mut Objective = Vec::with_capacity(ObjectiveCount);
        for ObjectiveIndex in 0..ObjectiveCount {
            if ObjectiveIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(None);
            }
            Objective.push(ObjectiveValues.get_item(ObjectiveIndex)?.extract::<i64>()?);
        }
        let RequiredSignalValues = &Domain.get_item(3)?;
        let RequiredSignalCount = RequiredSignalValues.len()?;
        let mut RequiredSignals = Vec::with_capacity(RequiredSignalCount);
        for SignalIndex in 0..RequiredSignalCount {
            if SignalIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(None);
            }
            RequiredSignals.push(
                RequiredSignalValues
                    .get_item(SignalIndex)?
                    .extract::<String>()?,
            );
        }
        let Some(Candidates) =
            ExtractAssignmentCandidateValuesWithDeadline(&Domain.get_item(4)?, Deadline)?
        else {
            return Ok(None);
        };
        let Some(BaseValues) =
            ExtractBaseAssignmentValuesWithDeadline(&Domain.get_item(5)?, Deadline)?
        else {
            return Ok(None);
        };
        Result.push((
            Domain.get_item(0)?.extract::<String>()?,
            Objective,
            Domain.get_item(2)?.extract::<usize>()?,
            RequiredSignals,
            Candidates,
            BaseValues,
        ));
    }
    if Deadline.Check() {
        return Ok(None);
    }
    Ok(Some(Result))
}

pub(crate) fn Register(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Module.add_class::<RoutingContext>()?;
    Module.add_class::<PortalCandidate>()?;
    Module.add_class::<PortalCandidateBatchResult>()?;
    Module.add_class::<RouteTreeBatchResult>()?;
    Module.add_class::<FactorizedRouteTreeSelectionResult>()?;
    Module.add_class::<RouteTreeSearchResult>()?;
    Module.add_class::<RouteTreeDetailedBatchResult>()?;
    Module.add_class::<RoutingAssignmentResult>()?;
    Module.add_class::<TemplateRoutingAssignmentResult>()?;
    Module.add_function(wrap_pyfunction!(GetRoutingThreadCount, Module)?)?;
    Module.add_function(wrap_pyfunction!(BuildRouteClaimsBatch, Module)?)?;
    Module.add_function(wrap_pyfunction!(
        BuildRouteClaimsBatchWithTelemetry,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        BuildDeferredRouteClaimsBatchWithTelemetry,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        BuildFabricSubtreesBatchWithTelemetry,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        SearchExteriorConnectorsBatchWithTelemetry,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(GenerateRectilinearTopology, Module)?)?;
    Module.add_function(wrap_pyfunction!(SolveLeaseDomainsBounded, Module)?)?;
    Module.add_function(wrap_pyfunction!(
        BuildAccessRegionGraphCatalogBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        BuildDerivedEscapeStatePathsBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        BuildLayeredEscapeStatePathCatalogBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        BuildLayeredAccessEscapeViewCatalogBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        SolveLayeredAccessEscapeFactorCatalogBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        SolveLayeredAccessGuideFactorCatalogBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        SolveTemplateAssignmentDomainsBounded,
        Module
    )?)?;
    Module.add_function(wrap_pyfunction!(
        SolveCompactTemplateFactorCatalogBounded,
        Module
    )?)?;
    Ok(())
}

/// Build every exact immutable access-region graph in one native batch.
/// Geometry construction is finite and bounded only by the shared absolute
/// deadline; an incomplete graph is never interpreted as infeasible.
#[pyfunction]
#[pyo3(signature=(Recipes, MaximumRuntimeMilliseconds))]
fn BuildAccessRegionGraphCatalogBounded(
    PythonValue: Python<'_>,
    Recipes: Vec<AccessRegionGraphRecipeValue>,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<(Vec<AccessRegionGraphResultValue>, bool)> {
    if Recipes.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "access-region graph catalog requires recipes",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok(PythonValue
        .allow_threads(move || BuildAccessRegionGraphCatalogWithDeadline(Recipes, Deadline)))
}

/// Enumerate bounded directional escape candidates; Python validates exact
/// redstone claims and turns cap/deadline exhaustion into typed incomplete.
#[pyfunction]
#[pyo3(signature=(AdjacencyValues, Requests, BendPenalty, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
fn BuildDerivedEscapeStatePathsBounded(
    PythonValue: Python<'_>,
    AdjacencyValues: Vec<(Position, Vec<Position>)>,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<(String, Vec<EscapeRequestResult>, usize, bool, bool)> {
    if MaximumExpansionCount < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum escape expansions must be positive",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok(PythonValue.allow_threads(move || {
        BuildDerivedEscapeStatePathsWithDeadline(
            AdjacencyValues,
            Requests,
            BendPenalty,
            MaximumExpansionCount,
            Deadline,
        )
    }))
}

/// Build all exact layer/member escape domains under one native call and one
/// absolute deadline.  Each member supplies its own graph and finite cap;
/// the shared cap must cover their sum so parallel scheduling cannot change
/// completeness or steal work between members.
#[pyfunction]
#[pyo3(signature=(Members, BendPenalty, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
fn BuildLayeredEscapeStatePathCatalogBounded(
    PythonValue: Python<'_>,
    Members: Vec<LayeredEscapeMemberRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<(String, Vec<LayeredEscapeMemberResult>, usize, bool, bool)> {
    if Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered escape catalog requires at least one member",
        ));
    }
    if MaximumExpansionCount < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum layered escape expansions must be positive",
        ));
    }
    if Members
        .iter()
        .any(|Member| Member.0.is_empty() || Member.3 < 1)
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered escape catalog members require identities and positive caps",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    Ok(PythonValue.allow_threads(move || {
        BuildLayeredEscapeStatePathCatalogWithDeadline(
            Members,
            BendPenalty,
            MaximumExpansionCount,
            Deadline,
        )
    }))
}

/// Build every exact layer view over shared source graphs in one bounded
/// native call.  The operation returns path catalogs only; it performs no
/// feasibility inference and cannot report UNSAT.
#[pyfunction]
#[pyo3(signature=(Graphs, Members, BendPenalty, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
fn BuildLayeredAccessEscapeViewCatalogBounded(
    PythonValue: Python<'_>,
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<(String, Vec<LayeredEscapeMemberResult>, usize, bool, bool)> {
    if MaximumExpansionCount < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum layered access view expansions must be positive",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PythonValue.allow_threads(move || {
        BuildLayeredAccessEscapeViewCatalogWithDeadline(
            Graphs,
            Members,
            BendPenalty,
            MaximumExpansionCount,
            Deadline,
        )
    })
}

/// Traverse exact layer-member escape domains and solve their access capacity
/// inside one bounded native operation.  Python declares geometry and member
/// contracts; Rust returns the selected member's exact path catalog so only
/// that continuation needs physical object materialization.
#[pyfunction]
#[pyo3(signature=(Graphs, Members, BendPenalty, MaximumAssignmentExpansionCount, MaximumRuntimeMilliseconds))]
fn SolveLayeredAccessEscapeFactorCatalogBounded(
    PythonValue: Python<'_>,
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<LayeredAccessEscapeSelectionResult> {
    if MaximumAssignmentExpansionCount < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum layered access assignment expansions must be positive",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PythonValue.allow_threads(move || {
        SolveLayeredAccessEscapeFactorCatalogWithDeadline(
            Graphs,
            Members,
            BendPenalty,
            MaximumAssignmentExpansionCount,
            Deadline,
        )
    })
}

/// Select exact access stubs and canonical guide spines together.  The
/// complete layered portfolio is traversed and tested inside this one call;
/// only the winning member's path catalog and guide recipes cross back into
/// Python for physical materialization.
#[pyfunction]
#[pyo3(signature=(Graphs, Members, BendPenalty, MaximumAssignmentExpansionCount, MaximumRuntimeMilliseconds))]
fn SolveLayeredAccessGuideFactorCatalogBounded(
    PythonValue: Python<'_>,
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    Members: Vec<LayeredAccessGuideMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<LayeredAccessGuideSelectionResult> {
    if MaximumAssignmentExpansionCount < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum layered access-guide assignment expansions must be positive",
        ));
    }
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PythonValue.allow_threads(move || {
        SolveLayeredAccessGuideFactorCatalogWithDeadline(
            Graphs,
            Members,
            BendPenalty,
            MaximumAssignmentExpansionCount,
            Deadline,
        )
    })
}

/// Select one fixed assignment template portfolio in Rust.  Each domain has
/// a separate local resource index, but all members share one deadline and
/// expansion cap.  This is a pre-route capacity choice, never a relaunch.
#[pyfunction]
#[pyo3(signature=(TemplateDomains, MaximumExpansionCount, MaximumRuntimeMilliseconds, NonExhaustiveTemplateDomain=true))]
fn SolveTemplateAssignmentDomainsBounded<'py>(
    PythonValue: Python<'py>,
    TemplateDomains: &Bound<'py, PyAny>,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
    NonExhaustiveTemplateDomain: bool,
) -> PyResult<TemplateRoutingAssignmentResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let Some(Domains) = ExtractTemplateAssignmentDomainsWithDeadline(TemplateDomains, &Deadline)?
    else {
        return Ok(TemplateRoutingAssignmentResult {
            Status: "Incomplete".to_string(),
            Success: false,
            Complete: false,
            Unsatisfiable: false,
            IncompleteReason: "assignment-deadline".to_string(),
            SelectedTemplateId: None,
            SelectedTemplateObjective: Vec::new(),
            SelectedCandidateIds: Vec::new(),
            ExpansionCount: 0,
            BudgetExhausted: false,
            DeadlineExceeded: true,
            CompletedWork: 0,
            FailureNet: None,
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: false,
            AttemptedTemplateIds: Vec::new(),
            AttemptPairwiseIncompatibleSignals: Vec::new(),
            AttemptFailureNets: Vec::new(),
            AttemptExpansionCounts: Vec::new(),
            AttemptPartialCandidateIds: Vec::new(),
            NonExhaustiveTemplateDomain,
            CompactMaskTelemetry: Vec::new(),
        });
    };
    PythonValue.allow_threads(move || {
        SolveTemplateAssignmentDomainsNative(
            Domains,
            MaximumExpansionCount,
            Deadline,
            NonExhaustiveTemplateDomain,
        )
    })
}

/// Select one exact interned compact-factor catalog.  Claim primitives are
/// decoded once; candidate masks are composed lazily for attempted members.
#[pyfunction]
#[pyo3(signature=(ResourcePositions, PrimitiveValues, FactorValues, MemberValues, MaximumExpansionCount, MaximumRuntimeMilliseconds, NonExhaustiveTemplateDomain=true))]
fn SolveCompactTemplateFactorCatalogBounded(
    PythonValue: Python<'_>,
    ResourcePositions: Vec<Position>,
    PrimitiveValues: Vec<CompactClaimPrimitiveValue>,
    FactorValues: Vec<crate::Planning::AssignmentPlanning::CompactFactorValue>,
    MemberValues: Vec<CompactFactorMemberValue>,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
    NonExhaustiveTemplateDomain: bool,
) -> PyResult<TemplateRoutingAssignmentResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PythonValue.allow_threads(move || {
        SolveCompactTemplateFactorCatalogNative(
            ResourcePositions,
            PrimitiveValues,
            FactorValues,
            MemberValues,
            MaximumExpansionCount,
            Deadline,
            NonExhaustiveTemplateDomain,
        )
    })
}

/// Solves sorted component-boundary lease domains.  The first branching level
/// runs in Rayon, while one atomic budget and one absolute deadline cover the
/// entire batch.  Results are reduced in lexical branch order.
#[pyfunction]
#[pyo3(signature=(LeaseDomains, ClaimSetCapacities, RejectedClaimSets, MaximumExpansions, MaximumRuntimeSeconds=None))]
fn SolveLeaseDomainsBounded(
    PythonValue: Python<'_>,
    LeaseDomains: Vec<(String, Vec<(String, usize, Vec<String>, Vec<usize>)>)>,
    ClaimSetCapacities: Vec<usize>,
    RejectedClaimSets: Vec<Vec<(String, String)>>,
    MaximumExpansions: usize,
    MaximumRuntimeSeconds: Option<f64>,
) -> PyResult<(String, Vec<(String, String)>, usize, bool, bool)> {
    let Deadline = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let Domains = LeaseDomains
        .into_iter()
        .map(|(Signal, Candidates)| LeaseDomain {
            Signal,
            Candidates: Candidates
                .into_iter()
                .map(|(Id, Order, ContractKeys, Claims)| LeaseCandidate {
                    Id,
                    Order,
                    ContractKeys,
                    Claims,
                })
                .collect(),
        })
        .collect();
    let Result = PythonValue.allow_threads(|| {
        SolveLeaseDomainsWithDeadline(
            Domains,
            ClaimSetCapacities,
            RejectedClaimSets,
            MaximumExpansions,
            Deadline,
        )
    });
    Ok((
        match Result.Status {
            LeaseSolveStatus::Feasible => "Feasible",
            LeaseSolveStatus::Unsatisfiable => "Unsatisfiable",
            LeaseSolveStatus::Incomplete => "Incomplete",
        }
        .to_string(),
        Result.Selected,
        Result.ExpansionCount,
        Result.DeadlineExceeded,
        Result.BudgetExhausted,
    ))
}

#[pymethods]
impl RoutingContext {
    #[new]
    fn New(
        _Bounds: (i32, i32, i32, i32, i32, i32),
        _PlacementBounds: (i32, i32, i32, i32),
        NodeValues: Vec<Position>,
        EdgeValues: Vec<Edge>,
    ) -> PyResult<Self> {
        let Nodes: HashSet<Position> = NodeValues.into_iter().collect();
        let mut Adjacency: HashMap<Position, Vec<Position>> = Nodes
            .iter()
            .copied()
            .map(|Value| (Value, Vec::new()))
            .collect();
        for (First, Second) in EdgeValues {
            if !Nodes.contains(&First) || !Nodes.contains(&Second) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "resource graph edge references a missing node",
                ));
            }
            Adjacency.get_mut(&First).unwrap().push(Second);
            Adjacency.get_mut(&Second).unwrap().push(First);
        }
        for Values in Adjacency.values_mut() {
            Values.sort();
            Values.dedup();
        }
        let mut NodesByColumn: HashMap<(i32, i32), Vec<Position>> = HashMap::new();
        for PositionValue in &Nodes {
            NodesByColumn
                .entry((PositionValue.0, PositionValue.2))
                .or_default()
                .push(*PositionValue);
        }
        for Values in NodesByColumn.values_mut() {
            Values.sort_unstable();
        }
        Ok(Self {
            Adjacency,
            NodesByColumn,
        })
    }

    fn AddRegion(
        &mut self,
        NodeValues: Vec<Position>,
        EdgeValues: Vec<Edge>,
    ) -> PyResult<(usize, usize)> {
        for PositionValue in NodeValues {
            if self.Adjacency.contains_key(&PositionValue) {
                continue;
            }
            self.Adjacency.insert(PositionValue, Vec::new());
            self.NodesByColumn
                .entry((PositionValue.0, PositionValue.2))
                .or_default()
                .push(PositionValue);
        }
        for Values in self.NodesByColumn.values_mut() {
            Values.sort_unstable();
            Values.dedup();
        }
        for (First, Second) in EdgeValues {
            if !self.Adjacency.contains_key(&First) || !self.Adjacency.contains_key(&Second) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "resource graph edge references a missing node",
                ));
            }
            self.Adjacency.get_mut(&First).unwrap().push(Second);
            self.Adjacency.get_mut(&Second).unwrap().push(First);
        }
        for Values in self.Adjacency.values_mut() {
            Values.sort_unstable();
            Values.dedup();
        }
        Ok((self.NodeCount(), self.EdgeCount()))
    }

    #[pyo3(signature=(AllowedColumns, RequiredAllowedNodeValues, BlockedNodeValues, ConnectivityRequiredNodeValues, Start, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
    fn CertifyRouteFactorConnectivityBounded(
        &self,
        PythonValue: Python<'_>,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredAllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        ConnectivityRequiredNodeValues: Vec<Position>,
        Start: Position,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> (bool, bool, usize) {
        PythonValue.allow_threads(|| {
            self.CertifyRouteFactorConnectivityNative(
                AllowedColumns,
                RequiredAllowedNodeValues,
                BlockedNodeValues,
                ConnectivityRequiredNodeValues,
                Start,
                MaximumExpansionCount,
                MaximumRuntimeMilliseconds,
            )
        })
    }

    #[pyo3(signature=(Requests, MaximumRuntimeMilliseconds))]
    fn CertifyRouteFactorConnectivityBatchBounded(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<(i32, i32)>,
            i32,
            Vec<Position>,
            Vec<Position>,
            Vec<Position>,
            Position,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> Vec<(bool, bool, usize)> {
        PythonValue.allow_threads(|| {
            self.CertifyRouteFactorConnectivityBatchNative(Requests, MaximumRuntimeMilliseconds)
        })
    }

    #[pyo3(signature=(Starts, PortalTargets, AllowedNodeValues, PreferredRoutingY, MaximumPortalCount, MaximumExpansionCount, MaximumRuntimeSeconds=None))]
    fn GeneratePortalCandidates(
        &self,
        Starts: Vec<Position>,
        PortalTargets: Vec<Position>,
        AllowedNodeValues: Vec<Position>,
        PreferredRoutingY: i32,
        MaximumPortalCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Vec<PortalCandidate> {
        self.GeneratePortalCandidatesNative(
            Starts,
            PortalTargets,
            AllowedNodeValues,
            PreferredRoutingY,
            MaximumPortalCount,
            MaximumExpansionCount,
            MaximumRuntimeSeconds,
        )
    }
    #[allow(clippy::type_complexity)]
    fn GeneratePortalCandidateBatches(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Position>,
            Vec<Position>,
            i32,
            usize,
            usize,
        )>,
    ) -> Vec<Vec<PortalCandidate>> {
        PythonValue.allow_threads(|| {
            GeneratePortalCandidateBatchesNative(self, Requests, None)
                .expect("unbounded portal generation cannot reject its deadline")
                .Candidates
        })
    }

    #[allow(clippy::type_complexity)]
    fn GeneratePortalCandidateBatchesBounded(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Position>,
            Vec<Position>,
            i32,
            usize,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<PortalCandidateBatchResult> {
        PythonValue.allow_threads(|| {
            GeneratePortalCandidateBatchesNative(self, Requests, Some(MaximumRuntimeMilliseconds))
        })
    }
    #[pyo3(signature=(Starts, TargetBranches, AllowedNodeValues, PreferredColumns, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, MaximumExpansionCount, MaximumRuntimeSeconds=None))]
    fn GenerateRouteTree(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Option<Vec<Position>> {
        self.GenerateRouteTreeNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            Vec::new(),
            PreferredColumns,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
            MaximumRuntimeSeconds,
        )
    }

    #[pyo3(signature=(Starts, TargetBranches, AllowedNodeValues, BlockedNodeValues, PreferredColumns, NodeCostValues, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
    fn GenerateRouteTreeWithCostsBounded(
        &self,
        PythonValue: Python<'_>,
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
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> Option<Vec<Position>> {
        PythonValue.allow_threads(|| {
            self.GenerateRouteTreeWithCostsNative(
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
                false,
                MaximumExpansionCount,
                Some(MaximumRuntimeMilliseconds as f64 / 1000.0),
            )
        })
    }

    #[pyo3(signature=(Starts, TargetBranches, AllowedNodeValues, BlockedNodeValues, PreferredColumns, NodeCostValues, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, EnforceSignalStrength, MaximumExpansionCount, MaximumRuntimeMilliseconds))]
    fn GenerateRouteTreeDetailedBounded(
        &self,
        PythonValue: Python<'_>,
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
        MaximumRuntimeMilliseconds: u64,
    ) -> RouteTreeSearchResult {
        PythonValue.allow_threads(|| {
            self.GenerateRouteTreeDetailedNative(
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
                MaximumRuntimeMilliseconds,
            )
        })
    }

    #[pyo3(signature=(Starts, TargetBranches, AllowedNodeValues, BlockedNodeValues, PreferredColumns, NodeCostValues, PreferredRoutingY, GuidePenalty, BendPenalty, ViaPenalty, EnforceSignalStrength, MaximumExpansionCount, MaximumRuntimeMilliseconds, MandatoryWireValues, MandatorySupportValues, MandatoryAirValues, MandatoryElectricalValues))]
    fn GenerateRouteTreeClaimAwareDetailedBounded(
        &self,
        PythonValue: Python<'_>,
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
        MaximumRuntimeMilliseconds: u64,
        MandatoryWireValues: Vec<Position>,
        MandatorySupportValues: Vec<Position>,
        MandatoryAirValues: Vec<Position>,
        MandatoryElectricalValues: Vec<Position>,
    ) -> RouteTreeSearchResult {
        PythonValue.allow_threads(|| {
            self.GenerateRouteTreeClaimAwareDetailedNative(
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
                MaximumRuntimeMilliseconds,
                MandatoryWireValues,
                MandatorySupportValues,
                MandatoryAirValues,
                MandatoryElectricalValues,
            )
        })
    }

    /// Runs independent detailed route-tree searches against a frozen routing
    /// context.  `MaximumRuntimeMilliseconds` is one absolute deadline for
    /// the whole batch, not a new budget per request.
    #[allow(clippy::type_complexity)]
    #[pyo3(signature=(Requests, MaximumRuntimeMilliseconds))]
    fn GenerateRouteTreeDetailedBatchBounded(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Vec<Position>>,
            Vec<Position>,
            Vec<Position>,
            Vec<(i32, i32)>,
            Vec<(Position, i32)>,
            i32,
            i32,
            i32,
            i32,
            bool,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> RouteTreeDetailedBatchResult {
        PythonValue.allow_threads(|| {
            GenerateRouteTreeDetailedBatchNative(self, Requests, MaximumRuntimeMilliseconds)
        })
    }

    #[allow(clippy::type_complexity)]
    #[pyo3(signature=(Requests, MaximumRuntimeMilliseconds))]
    fn GenerateRouteTreeClaimAwareDetailedBatchBounded(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<ClaimAwareDetailedRouteTreeRequest>,
        MaximumRuntimeMilliseconds: u64,
    ) -> RouteTreeDetailedBatchResult {
        PythonValue.allow_threads(|| {
            GenerateRouteTreeClaimAwareDetailedBatchNative(
                self,
                Requests,
                MaximumRuntimeMilliseconds,
            )
        })
    }

    #[allow(clippy::type_complexity)]
    fn GenerateRouteTrees(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Vec<Position>>,
            Vec<(i32, i32)>,
            Vec<Position>,
            Vec<Position>,
            Vec<(i32, i32)>,
            i32,
            i32,
            i32,
            i32,
            usize,
        )>,
    ) -> Vec<Option<Vec<Position>>> {
        PythonValue.allow_threads(|| {
            GenerateRouteTreesNative(self, Requests, None)
                .expect("unbounded route-tree generation cannot reject its deadline")
                .RouteTrees
        })
    }

    #[allow(clippy::type_complexity)]
    fn GenerateRouteTreesBounded(
        &self,
        PythonValue: Python<'_>,
        Requests: Vec<(
            Vec<Position>,
            Vec<Vec<Position>>,
            Vec<(i32, i32)>,
            Vec<Position>,
            Vec<Position>,
            Vec<(i32, i32)>,
            i32,
            i32,
            i32,
            i32,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<RouteTreeBatchResult> {
        PythonValue.allow_threads(|| {
            GenerateRouteTreesNative(self, Requests, Some(MaximumRuntimeMilliseconds))
        })
    }

    #[pyo3(signature=(AccessPayloads, GuidePayloads, Requests, MaximumRuntimeMilliseconds))]
    fn GenerateRouteTreesFactorizedBounded(
        &self,
        PythonValue: Python<'_>,
        AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
        GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
        Requests: Vec<FactorizedRouteTreeRequest>,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<RouteTreeBatchResult> {
        PythonValue.allow_threads(|| {
            GenerateRouteTreesFactorizedNative(
                self,
                AccessPayloads,
                GuidePayloads,
                Requests,
                MaximumRuntimeMilliseconds,
            )
        })
    }

    #[pyo3(signature=(AccessPayloads, GuidePayloads, Requests, SignalRequestIndices, MaximumAssignmentExpansionCount, MaximumRuntimeMilliseconds))]
    fn GenerateAndAssignRouteTreesFactorizedBounded(
        &self,
        PythonValue: Python<'_>,
        AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
        GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
        Requests: Vec<FactorizedRouteTreeRequest>,
        SignalRequestIndices: Vec<(String, Vec<usize>)>,
        MaximumAssignmentExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<FactorizedRouteTreeSelectionResult> {
        PythonValue.allow_threads(|| {
            GenerateAndAssignRouteTreesFactorizedNative(
                self,
                AccessPayloads,
                GuidePayloads,
                Requests,
                SignalRequestIndices,
                MaximumAssignmentExpansionCount,
                MaximumRuntimeMilliseconds,
            )
        })
    }
    #[pyo3(signature=(CandidateValues, ResourceCount, MaximumExpansionCount, MaximumRuntimeSeconds=None))]
    fn PlanAuthoritativeRoutes<'py>(
        &self,
        PythonValue: Python<'py>,
        CandidateValues: &Bound<'py, PyAny>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> PyResult<RoutingAssignmentResult> {
        ValidateAssignmentResourceCount(ResourceCount)?;
        let Deadline = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        let Some(CandidateValues) =
            ExtractAssignmentCandidateValuesWithDeadline(CandidateValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        PythonValue.allow_threads(move || {
            PlanAuthoritativeRoutesWithDeadline(
                CandidateValues,
                ResourceCount,
                MaximumExpansionCount,
                Deadline,
            )
        })
    }

    fn PlanAuthoritativeRoutesBounded<'py>(
        &self,
        PythonValue: Python<'py>,
        CandidateValues: &Bound<'py, PyAny>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<RoutingAssignmentResult> {
        ValidateAssignmentResourceCount(ResourceCount)?;
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        let Some(CandidateValues) =
            ExtractAssignmentCandidateValuesWithDeadline(CandidateValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        PythonValue.allow_threads(move || {
            PlanAuthoritativeRoutesWithDeadline(
                CandidateValues,
                ResourceCount,
                MaximumExpansionCount,
                Deadline,
            )
        })
    }

    #[pyo3(signature=(CandidateValues, BaseValues, ResourceCount, MaximumExpansionCount, MaximumRuntimeSeconds=None))]
    fn PlanAuthoritativeRoutesWithBase<'py>(
        &self,
        PythonValue: Python<'py>,
        CandidateValues: &Bound<'py, PyAny>,
        BaseValues: &Bound<'py, PyAny>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> PyResult<RoutingAssignmentResult> {
        ValidateAssignmentResourceCount(ResourceCount)?;
        let Deadline = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        let Some(CandidateValues) =
            ExtractAssignmentCandidateValuesWithDeadline(CandidateValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        let Some(BaseValues) = ExtractBaseAssignmentValuesWithDeadline(BaseValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        PythonValue.allow_threads(move || {
            PlanAuthoritativeRoutesWithBaseAndDeadline(
                CandidateValues,
                BaseValues,
                ResourceCount,
                MaximumExpansionCount,
                Deadline,
            )
        })
    }

    fn PlanAuthoritativeRoutesWithBaseBounded<'py>(
        &self,
        PythonValue: Python<'py>,
        CandidateValues: &Bound<'py, PyAny>,
        BaseValues: &Bound<'py, PyAny>,
        ResourceCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> PyResult<RoutingAssignmentResult> {
        ValidateAssignmentResourceCount(ResourceCount)?;
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        let Some(CandidateValues) =
            ExtractAssignmentCandidateValuesWithDeadline(CandidateValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        let Some(BaseValues) = ExtractBaseAssignmentValuesWithDeadline(BaseValues, &Deadline)?
        else {
            return Ok(DeadlineExceededAssignmentResult(0));
        };
        PythonValue.allow_threads(move || {
            PlanAuthoritativeRoutesWithBaseAndDeadline(
                CandidateValues,
                BaseValues,
                ResourceCount,
                MaximumExpansionCount,
                Deadline,
            )
        })
    }

    fn FindPathOnResourceGraph(
        &self,
        Starts: Vec<Position>,
        Target: Position,
        PreferredRoutingY: i32,
        BlockedNodeValues: Vec<Position>,
        NodeCostValues: Vec<(Position, i32)>,
        EdgeCostValues: Vec<(Edge, i32)>,
        BendPenalty: i32,
        ViaPenalty: i32,
        ProgressPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Option<Vec<Position>> {
        FindPath(
            &self.Adjacency,
            &Starts,
            Target,
            PreferredRoutingY,
            &BlockedNodeValues.into_iter().collect(),
            &NodeCostValues.into_iter().collect(),
            &EdgeCostValues.into_iter().collect(),
            BendPenalty,
            ViaPenalty,
            ProgressPenalty,
            MaximumExpansionCount,
        )
    }

    fn FindPathsOnResourceGraph(
        &self,
        PythonValue: Python<'_>,
        Starts: Vec<Position>,
        Targets: Vec<Position>,
        PreferredRoutingY: i32,
        BlockedNodeValues: Vec<Position>,
        NodeCostValues: Vec<(Position, i32)>,
        EdgeCostValues: Vec<(Edge, i32)>,
        BendPenalty: i32,
        ViaPenalty: i32,
        ProgressPenalty: i32,
        MaximumExpansionCount: usize,
    ) -> Vec<Option<Vec<Position>>> {
        let BlockedNodes: HashSet<Position> = BlockedNodeValues.into_iter().collect();
        let NodeCosts: HashMap<Position, i32> = NodeCostValues.into_iter().collect();
        let EdgeCosts: HashMap<Edge, i32> = EdgeCostValues.into_iter().collect();
        PythonValue.allow_threads(|| {
            RoutingThreadPool().install(|| {
                Targets
                    .par_iter()
                    .map(|Target| {
                        FindPath(
                            &self.Adjacency,
                            &Starts,
                            *Target,
                            PreferredRoutingY,
                            &BlockedNodes,
                            &NodeCosts,
                            &EdgeCosts,
                            BendPenalty,
                            ViaPenalty,
                            ProgressPenalty,
                            MaximumExpansionCount,
                        )
                    })
                    .collect()
            })
        })
    }

    fn NodeCount(&self) -> usize {
        self.Adjacency.len()
    }

    fn EdgeCount(&self) -> usize {
        self.Adjacency.values().map(Vec::len).sum::<usize>() / 2
    }
}
