//! Native route generation, batching, and factorized assignment.

mod Api;
mod Batches;
mod DetailedTrees;
mod Factorized;
mod SelectedWorldClaims;

pub(crate) use Api::GenerateRouteTreesNative;
pub(crate) use Batches::{
    GeneratePortalCandidateBatchesNative, GenerateRouteTreeClaimAwareDetailedBatchNative,
    GenerateRouteTreeDetailedBatchNative,
};
pub(crate) use Factorized::{
    GenerateAndAssignRouteTreesFactorizedNative, GenerateRouteTreesFactorizedNative,
};
