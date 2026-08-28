//! Exact bounded lease-domain planning.

use crate::Core::Deadline::RuntimeDeadline;
use crate::Core::Runtime::RoutingThreadPool;
use rayon::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

#[derive(Clone)]
pub(crate) struct LeaseCandidate {
    pub(crate) Id: String,
    pub(crate) Order: usize,
    pub(crate) ContractKeys: Vec<String>,
    pub(crate) Claims: Vec<usize>,
}

#[derive(Clone)]
pub(crate) struct LeaseDomain {
    pub(crate) Signal: String,
    pub(crate) Candidates: Vec<LeaseCandidate>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LeaseSolveStatus {
    Feasible,
    Unsatisfiable,
    Incomplete,
}

pub(crate) struct LeaseSolveResult {
    pub(crate) Status: LeaseSolveStatus,
    pub(crate) Selected: Vec<(String, String)>,
    pub(crate) ExpansionCount: usize,
    pub(crate) DeadlineExceeded: bool,
    pub(crate) BudgetExhausted: bool,
}

struct SharedWork {
    Budget: AtomicUsize,
    BudgetExhausted: AtomicBool,
    Maximum: usize,
    Deadline: RuntimeDeadline,
}

impl SharedWork {
    fn Advance(&self) -> bool {
        if self.Deadline.Check() {
            return false;
        }
        loop {
            let Current = self.Budget.load(Ordering::Relaxed);
            if Current >= self.Maximum {
                self.BudgetExhausted.store(true, Ordering::Relaxed);
                return false;
            }
            if self
                .Budget
                .compare_exchange_weak(Current, Current + 1, Ordering::Relaxed, Ordering::Relaxed)
                .is_ok()
            {
                return true;
            }
        }
    }
}

type SelectedLease = (String, String, Vec<String>);

fn ViolatesNogood(Selected: &[SelectedLease], Nogoods: &[Vec<(String, String)>]) -> bool {
    Nogoods.iter().any(|Clause| {
        Clause.iter().all(|(Signal, Key)| {
            Selected.iter().any(|(SelectedSignal, _Id, Keys)| {
                SelectedSignal == Signal && Keys.binary_search(Key).is_ok()
            })
        })
    })
}

fn Search(
    Domains: &[LeaseDomain],
    Capacities: &[usize],
    Nogoods: &[Vec<(String, String)>],
    Index: usize,
    Usage: &mut [usize],
    Selected: &mut Vec<SelectedLease>,
    Work: &SharedWork,
) -> LeaseSolveStatus {
    if !Work.Advance() {
        return LeaseSolveStatus::Incomplete;
    }
    if Index == Domains.len() {
        return LeaseSolveStatus::Feasible;
    }
    let Domain = &Domains[Index];
    for Candidate in &Domain.Candidates {
        if Work.Deadline.Check() {
            return LeaseSolveStatus::Incomplete;
        }
        if Candidate.Claims.iter().any(|Resource| {
            *Resource >= Capacities.len() || Usage[*Resource] >= Capacities[*Resource]
        }) {
            continue;
        }
        for Resource in &Candidate.Claims {
            Usage[*Resource] += 1;
        }
        Selected.push((
            Domain.Signal.clone(),
            Candidate.Id.clone(),
            Candidate.ContractKeys.clone(),
        ));
        let Status = if ViolatesNogood(Selected, Nogoods) {
            LeaseSolveStatus::Unsatisfiable
        } else {
            Search(
                Domains,
                Capacities,
                Nogoods,
                Index + 1,
                Usage,
                Selected,
                Work,
            )
        };
        if Status == LeaseSolveStatus::Feasible {
            return Status;
        }
        Selected.pop();
        for Resource in &Candidate.Claims {
            Usage[*Resource] -= 1;
        }
        if Status == LeaseSolveStatus::Incomplete {
            return Status;
        }
    }
    LeaseSolveStatus::Unsatisfiable
}

pub(crate) fn SolveLeaseDomainsWithDeadline(
    mut Domains: Vec<LeaseDomain>,
    Capacities: Vec<usize>,
    mut Nogoods: Vec<Vec<(String, String)>>,
    MaximumExpansions: usize,
    Deadline: RuntimeDeadline,
) -> LeaseSolveResult {
    Domains.sort_by(|First, Second| First.Signal.cmp(&Second.Signal));
    for Domain in &mut Domains {
        Domain
            .Candidates
            .sort_by(|First, Second| (First.Order, &First.Id).cmp(&(Second.Order, &Second.Id)));
        Domain
            .Candidates
            .dedup_by(|First, Second| First.Id == Second.Id);
        for Candidate in &mut Domain.Candidates {
            Candidate.ContractKeys.sort();
            Candidate.ContractKeys.dedup();
            Candidate.Claims.sort_unstable();
            Candidate.Claims.dedup();
        }
    }
    Nogoods.sort();
    Nogoods.dedup();
    let Work = SharedWork {
        Budget: AtomicUsize::new(0),
        BudgetExhausted: AtomicBool::new(false),
        Maximum: MaximumExpansions,
        Deadline,
    };
    if Domains.iter().any(|Domain| Domain.Candidates.is_empty()) {
        return LeaseSolveResult {
            Status: LeaseSolveStatus::Unsatisfiable,
            Selected: Vec::new(),
            ExpansionCount: 0,
            DeadlineExceeded: Work.Deadline.Check(),
            BudgetExhausted: false,
        };
    }
    if Domains.is_empty() {
        return LeaseSolveResult {
            Status: LeaseSolveStatus::Feasible,
            Selected: Vec::new(),
            ExpansionCount: 0,
            DeadlineExceeded: Work.Deadline.Check(),
            BudgetExhausted: false,
        };
    }
    let First = &Domains[0];
    let Outcomes = RoutingThreadPool().install(|| {
        First
            .Candidates
            .par_iter()
            .map(|Candidate| {
                let mut Usage = vec![0usize; Capacities.len()];
                let mut Selected = vec![(
                    First.Signal.clone(),
                    Candidate.Id.clone(),
                    Candidate.ContractKeys.clone(),
                )];
                if !Work.Advance() {
                    return (LeaseSolveStatus::Incomplete, Vec::new());
                }
                if Candidate
                    .Claims
                    .iter()
                    .any(|Resource| *Resource >= Capacities.len() || Capacities[*Resource] == 0)
                {
                    return (LeaseSolveStatus::Unsatisfiable, Vec::new());
                }
                for Resource in &Candidate.Claims {
                    Usage[*Resource] += 1;
                }
                let Status = if ViolatesNogood(&Selected, &Nogoods) {
                    LeaseSolveStatus::Unsatisfiable
                } else {
                    Search(
                        &Domains,
                        &Capacities,
                        &Nogoods,
                        1,
                        &mut Usage,
                        &mut Selected,
                        &Work,
                    )
                };
                (Status, Selected)
            })
            .collect::<Vec<_>>()
    });
    // Outcomes retain sorted first-level order.  A feasible later branch is
    // only authoritative after every lexically earlier branch is proven UNSAT.
    let mut Incomplete = false;
    for (Status, Selected) in Outcomes {
        match Status {
            LeaseSolveStatus::Feasible if !Incomplete => {
                return LeaseSolveResult {
                    Status,
                    Selected: Selected
                        .into_iter()
                        .map(|(Signal, Id, _Keys)| (Signal, Id))
                        .collect(),
                    ExpansionCount: Work.Budget.load(Ordering::Relaxed),
                    DeadlineExceeded: Work.Deadline.WasExceeded(),
                    BudgetExhausted: Work.BudgetExhausted.load(Ordering::Relaxed),
                }
            }
            LeaseSolveStatus::Incomplete => Incomplete = true,
            _ => {}
        }
    }
    LeaseSolveResult {
        Status: if Incomplete
            || Work.Deadline.WasExceeded()
            || Work.BudgetExhausted.load(Ordering::Relaxed)
        {
            LeaseSolveStatus::Incomplete
        } else {
            LeaseSolveStatus::Unsatisfiable
        },
        Selected: Vec::new(),
        ExpansionCount: Work.Budget.load(Ordering::Relaxed),
        DeadlineExceeded: Work.Deadline.WasExceeded(),
        BudgetExhausted: Work.BudgetExhausted.load(Ordering::Relaxed),
    }
}

#[cfg(test)]
mod Tests {
    use super::*;

    fn Candidate(Id: &str, Order: usize, Claims: &[usize]) -> LeaseCandidate {
        LeaseCandidate {
            Id: Id.to_string(),
            Order,
            ContractKeys: vec![Id.to_string()],
            Claims: Claims.to_vec(),
        }
    }

    #[test]
    fn ParallelFirstLevelReturnsTheFirstFeasibleLeaseAssignment() {
        let Result = SolveLeaseDomainsWithDeadline(
            vec![
                LeaseDomain {
                    Signal: "A".to_string(),
                    Candidates: vec![Candidate("a0", 0, &[0]), Candidate("a1", 1, &[1])],
                },
                LeaseDomain {
                    Signal: "B".to_string(),
                    Candidates: vec![Candidate("b0", 0, &[0])],
                },
            ],
            vec![1, 1],
            Vec::new(),
            100,
            RuntimeDeadline::FromSeconds(Some(1.0)).unwrap(),
        );
        assert_eq!(Result.Status, LeaseSolveStatus::Feasible);
        assert_eq!(
            Result.Selected,
            vec![
                ("A".to_string(), "a1".to_string()),
                ("B".to_string(), "b0".to_string()),
            ]
        );
    }

    #[test]
    fn SharedExpansionBudgetReportsIncomplete() {
        let Result = SolveLeaseDomainsWithDeadline(
            vec![LeaseDomain {
                Signal: "A".to_string(),
                Candidates: vec![Candidate("a0", 0, &[])],
            }],
            Vec::new(),
            Vec::new(),
            0,
            RuntimeDeadline::FromSeconds(Some(1.0)).unwrap(),
        );
        assert_eq!(Result.Status, LeaseSolveStatus::Incomplete);
        assert!(Result.BudgetExhausted);
    }

    #[test]
    fn SharedDeadlineReportsIncompleteInsteadOfUnsatisfiable() {
        let Result = SolveLeaseDomainsWithDeadline(
            vec![LeaseDomain {
                Signal: "A".to_string(),
                Candidates: vec![Candidate("a0", 0, &[])],
            }],
            Vec::new(),
            Vec::new(),
            100,
            RuntimeDeadline::FromSeconds(Some(0.0)).unwrap(),
        );
        assert_eq!(Result.Status, LeaseSolveStatus::Incomplete);
        assert!(Result.DeadlineExceeded);
        assert!(!Result.BudgetExhausted);
    }
}
