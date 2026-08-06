use crate::AssignmentPlanning::{
    AssignmentCandidateValue, BaseAssignmentValue, DeadlineExceededAssignmentResult,
    PlanAuthoritativeRoutesWithBaseAndDeadline, PlanAuthoritativeRoutesWithDeadline,
};
use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Generation::{
    GeneratePortalCandidateBatchesNative, GenerateRouteTreeDetailedBatchNative,
    GenerateRouteTreesNative,
};
use crate::LeasePlanning::{
    LeaseCandidate, LeaseDomain, LeaseSolveStatus, SolveLeaseDomainsWithDeadline,
};
use crate::Models::*;
use crate::PathRouting::FindPath;
use crate::{
    BuildFabricSubtreesBatchWithTelemetry, BuildRouteClaimsBatch,
    BuildRouteClaimsBatchWithTelemetry, EvaluateLogicPrograms, GenerateRectilinearTopology,
    GetRoutingThreadCount, RoutingThreadPool, SearchExteriorConnectorsBatchWithTelemetry,
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
        if Candidate.len()? != 11 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "assignment candidate must contain exactly 11 values",
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

pub(crate) fn Register(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Module.add_class::<RoutingContext>()?;
    Module.add_class::<PortalCandidate>()?;
    Module.add_class::<PortalCandidateBatchResult>()?;
    Module.add_class::<RouteTreeBatchResult>()?;
    Module.add_class::<RouteTreeSearchResult>()?;
    Module.add_class::<RouteTreeDetailedBatchResult>()?;
    Module.add_class::<RoutingAssignmentResult>()?;
    Module.add_function(wrap_pyfunction!(GetRoutingThreadCount, Module)?)?;
    Module.add_function(wrap_pyfunction!(BuildRouteClaimsBatch, Module)?)?;
    Module.add_function(wrap_pyfunction!(
        BuildRouteClaimsBatchWithTelemetry,
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
    Module.add_function(wrap_pyfunction!(EvaluateLogicPrograms, Module)?)?;
    Module.add_function(wrap_pyfunction!(SolveLeaseDomainsBounded, Module)?)?;
    Ok(())
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
