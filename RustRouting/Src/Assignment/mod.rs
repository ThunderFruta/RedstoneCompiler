//! Capacity-one route-candidate assignment domain.

mod Api;
mod Domains;
mod Search;
mod Witness;

pub(crate) use Api::{AssignCandidates, SortCandidatesWithDeadline};
pub(crate) use Witness::{ParseContractRequirements, SelectionHasPoweredAccessWitnessExact};
