"""Process-global placement caches with stable object identity."""

from __future__ import annotations




_JointPlacementSearchCache: dict[
    tuple[object, ...],
    dict[str, object],
] = {}

_JointPlacementExactScreenCache: dict[
    tuple[object, ...],
    "ExactJointPlacementScreen",
] = {}

_ExactStatePlacementGeometryCache: dict[
    tuple[object, ...],
    tuple["ExactStatePlacedGateGeometry", ...],
] = {}

_PackedClusterBaseLayoutCache: dict[
    tuple[object, ...],
    tuple[
        str,
        int | None,
        dict[str, str],
        dict[str, tuple[int, int]],
        dict[str, int],
        dict[str, bool],
        int,
        int,
    ],
] = {}

_PinAlignedPackedClusterPortfolioCache: dict[
    tuple[object, ...],
    tuple["PinAlignedPackedClusterState", ...],
] = {}

_PlacementTopologyCache: dict[
    tuple[object, ...],
    tuple[
        tuple[tuple[str, int], ...],
        tuple[tuple[str, ...], ...],
    ],
] = {}

_ClusterLocalRouteTemplateCache: dict[
    tuple[object, ...],
    "ClusterLocalRouteTemplateCacheEntry",
] = {}
