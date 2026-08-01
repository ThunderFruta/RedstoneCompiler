from types import SimpleNamespace

from Compiler.Routing.ComponentPipeline import (
    BuildPhysicalComponentLocalFactorProjection,
    BuildPhysicalComponentLocalFactorUnsatCertificate,
    ComparePhysicalComponentLocalFactorProjection,
)
from Compiler.Routing.ResourceGraph import RoutingResourceClaims


def _Move(Position, Delta):
    return tuple(Position[Index] + Delta[Index] for Index in range(3))


def _Claims(Cells, Delta):
    return RoutingResourceClaims(
        WireCells=frozenset(_Move(Value, Delta) for Value in Cells),
        SupportCells=frozenset(),
        RequiredAirCells=frozenset(),
        ElectricalCells=frozenset(),
    )


def _Fixture(
    *,
    Delta=(0, 0, 0),
    Names=("left", "right"),
    Reverse=False,
    Resource="resource-v1",
    Technology="technology-v1",
    InterfaceDirection="input",
    ChangeLocalContract=False,
    ChangeTopology=False,
    DomainComplete=True,
    SourceProofFingerprint="proof-v1",
):
    First, Second = Names
    Nodes = tuple(_Move(Value, Delta) for Value in (
        (0, 1, 0),
        (1, 1, 0),
        (2, 1, 0),
        (3, 1, 0),
    ))
    Edges = (
        (Nodes[0], Nodes[1]),
        (Nodes[1], Nodes[2]),
        ((Nodes[0], Nodes[2]) if ChangeTopology else (Nodes[2], Nodes[3])),
    )

    def Domain(Signal, Terminal, Attachment):
        CandidatePath = (Terminal, Attachment)
        return SimpleNamespace(
            Signal=Signal,
            Terminal=Terminal,
            TerminalRole="owned-input",
            Complete=DomainComplete,
            Candidates=(SimpleNamespace(
                Attachment=Attachment,
                Path=CandidatePath,
                Claims=_Claims(CandidatePath, (0, 0, 0)),
                Layer=1,
            ),),
        )

    Domains = (
        Domain(First, Nodes[0], Nodes[1]),
        Domain(Second, Nodes[3], Nodes[2]),
    )

    def LogicalPort(Signal, Terminal, Direction):
        return SimpleNamespace(
            Signal=Signal,
            Direction=Direction,
            OwnedTerminals=(Terminal,),
            ExternalTerminalCount=1,
            Capacity=1,
        )

    Interface = SimpleNamespace(
        Complete=True,
        Ports=(
            LogicalPort(First, Nodes[0], InterfaceDirection),
            LogicalPort(Second, Nodes[3], "output"),
        ),
        PhysicalPortReservations=(),
        Feedthroughs=(),
    )
    Problem = SimpleNamespace(
        Fabric=SimpleNamespace(
            Nodes=Nodes,
            Edges=Edges,
            IngressNodes=(Nodes[0], Nodes[3]),
            TopologyKind="generic-reconvergent",
            Complete=True,
        ),
        MaximumPowerDistance=15,
        Interface=Interface,
        PhysicalAssemblyPlan=None,
        OwnedTerminalDomains=Domains,
        DomainComplete=True,
    )

    def Factor(Signal, Terminal, Attachment, Direction):
        LocalPath = (Terminal, Attachment)
        if ChangeLocalContract and Signal == First:
            LocalPath = (Terminal, Nodes[2], Attachment)
        Candidate = SimpleNamespace(
            Attachment=Attachment,
            Path=(Terminal, Attachment),
            Claims=_Claims((Terminal, Attachment), (0, 0, 0)),
            Layer=1,
        )
        return SimpleNamespace(
            Signal=Signal,
            Direction=Direction,
            Capacity=1,
            OwnedTerminals=(Terminal,),
            FabricAttachment=Attachment,
            LocalPath=LocalPath,
            LocalClaims=_Claims(LocalPath, (0, 0, 0)),
            OwnedAccessCandidates=(Candidate,),
        )

    Factors = (
        (First, (Factor(First, Nodes[0], Nodes[1], "input"),)),
        (Second, (Factor(Second, Nodes[3], Nodes[2], "output"),)),
    )
    if Reverse:
        Problem.OwnedTerminalDomains = tuple(reversed(Domains))
        Problem.Interface.Ports = tuple(reversed(Interface.Ports))
        Factors = tuple(reversed(Factors))
    Projection = BuildPhysicalComponentLocalFactorProjection(
        Problem,
        Factors,
        ResourceGraphFingerprint=Resource,
        TechnologyFingerprint=Technology,
        Complete=DomainComplete,
    )
    Proof = {
        "GlobalRelaxedLocalProofComplete": True,
        "GlobalRelaxedLocalCoreComplete": True,
        "GlobalRelaxedLocalProofStatus": "architectural-unsatisfiable",
        "GlobalRelaxedLocalProofFingerprint": SourceProofFingerprint,
        "GlobalRelaxedLocalUnsatCoreSignals": [Second, First],
        "GlobalRelaxedLocalUnsatCoreKind": "opposing-local-access-pair",
    }
    Certificate = BuildPhysicalComponentLocalFactorUnsatCertificate(
        Problem,
        Factors,
        Proof,
        ResourceGraphFingerprint=Resource,
        TechnologyFingerprint=Technology,
    )
    return Problem, Factors, Projection, Certificate, Proof


def test_projection_is_translation_rename_and_order_invariant():
    _, _, FirstProjection, FirstCertificate, _ = _Fixture()
    _, _, SecondProjection, SecondCertificate, _ = _Fixture(
        Delta=(41, 3, -17),
        Names=("renamed_b", "renamed_a"),
        Reverse=True,
        SourceProofFingerprint="renamed-source-proof",
    )

    assert FirstProjection == SecondProjection
    assert FirstCertificate == SecondCertificate
    Comparison = ComparePhysicalComponentLocalFactorProjection(
        FirstCertificate,
        SecondProjection,
    )
    assert Comparison.CanPrune
    assert Comparison.ExactDomainMatch
    assert Comparison.CoreFactorMatchCount == 2


def test_projection_rejects_resource_technology_and_topology_mismatch():
    _, _, Projection, Certificate, _ = _Fixture()
    Cases = (
        (_Fixture(Resource="resource-v2")[2], "resource-graph-mismatch"),
        (_Fixture(Technology="technology-v2")[2], "technology-mismatch"),
        (_Fixture(ChangeTopology=True)[2], "component-topology-mismatch"),
    )
    for Candidate, Reason in Cases:
        Comparison = ComparePhysicalComponentLocalFactorProjection(
            Certificate,
            Candidate,
        )
        assert not Comparison.CanPrune
        assert Comparison.RejectionReason == Reason
        assert Comparison != ComparePhysicalComponentLocalFactorProjection(
            Certificate,
            Projection,
        )


def test_projection_rejects_interface_and_local_contract_mismatch():
    _, _, _, Certificate, _ = _Fixture()
    InterfaceCandidate = _Fixture(InterfaceDirection="output")[2]
    InterfaceComparison = ComparePhysicalComponentLocalFactorProjection(
        Certificate,
        InterfaceCandidate,
    )
    assert not InterfaceComparison.CanPrune
    assert (
        InterfaceComparison.RejectionReason
        == "interface-contract-mismatch"
    )

    LocalCandidate = _Fixture(ChangeLocalContract=True)[2]
    LocalComparison = ComparePhysicalComponentLocalFactorProjection(
        Certificate,
        LocalCandidate,
    )
    assert not LocalComparison.CanPrune
    assert LocalComparison.IdentityCompatible
    assert LocalComparison.RejectionReason == "local-contract-domain-mismatch"


def test_incomplete_proof_or_projection_cannot_prune():
    Problem, Factors, Projection, _, Proof = _Fixture()
    IncompleteProof = dict(Proof)
    IncompleteProof["GlobalRelaxedLocalCoreComplete"] = False
    Certificate = BuildPhysicalComponentLocalFactorUnsatCertificate(
        Problem,
        Factors,
        IncompleteProof,
        ResourceGraphFingerprint="resource-v1",
        TechnologyFingerprint="technology-v1",
    )
    Comparison = ComparePhysicalComponentLocalFactorProjection(
        Certificate,
        Projection,
    )
    assert not Certificate.Complete
    assert not Comparison.CanPrune
    assert Comparison.RejectionReason == "incomplete-certificate"

    IncompleteProjection = _Fixture(DomainComplete=False)[2]
    CompleteCertificate = _Fixture()[3]
    Comparison = ComparePhysicalComponentLocalFactorProjection(
        CompleteCertificate,
        IncompleteProjection,
    )
    assert not IncompleteProjection.Complete
    assert not Comparison.CanPrune
    assert Comparison.RejectionReason == "incomplete-projection"
