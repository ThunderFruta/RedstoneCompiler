//! Access escape construction and exact layered catalog planning.

mod Api;
mod Candidates;
mod Catalog;
mod State;
mod Traversal;

pub(crate) use Api::{
    BuildLayeredAccessEscapeViewCatalogWithDeadline,
    BuildLayeredEscapeStatePathCatalogWithDeadline,
    SolveLayeredAccessEscapeFactorCatalogWithDeadline,
    SolveLayeredAccessGuideFactorCatalogWithDeadline,
};
pub(crate) use State::{
    AccessRegionGraphRecipeValue, AccessRegionGraphResultValue, EscapeRequest, EscapeRequestResult,
    LayeredAccessEscapeGraphValue, LayeredAccessEscapeMemberValue, LayeredEscapeMemberRequest,
    LayeredEscapeMemberResult,
};
pub(crate) use Traversal::{
    BuildAccessRegionGraphCatalogWithDeadline, BuildDerivedEscapeStatePathsWithDeadline,
    LayeredAccessEscapeSelectionResult, LayeredAccessGuideMemberValue,
    LayeredAccessGuideSelectionResult,
};
