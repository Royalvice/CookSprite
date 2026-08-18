"""Global silhouette compilation and independent internal structural strokes."""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush

import cv2
import numpy as np
from scipy import ndimage, sparse  # type: ignore[import-untyped]
from scipy.sparse.csgraph import breadth_first_order, maximum_flow  # type: ignore[import-untyped]
from skimage.morphology import skeletonize

from .evidence import CellEvidence


@dataclass(frozen=True)
class SilhouetteContour:
    mask: np.ndarray
    contour_points: tuple[tuple[int, int], ...]
    source_energy: float
    component_count: int
    hole_count: int
    dangling_cells_removed: int
    thin_cells_restored: int
    irregularity: float


@dataclass(frozen=True)
class InternalStructureStroke:
    mask: np.ndarray
    component_count: int
    cell_count: int


def _add_edge(rows: list[int], cols: list[int], data: list[int], source: int, target: int, capacity: int) -> None:
    if capacity <= 0:
        return
    rows.append(source)
    cols.append(target)
    data.append(int(capacity))


def _global_min_cut(evidence: CellEvidence) -> np.ndarray:
    coverage = evidence.coverage
    height, width = coverage.shape
    node_count = height * width
    source = node_count
    sink = node_count + 1
    rows: list[int] = []
    cols: list[int] = []
    data: list[int] = []
    unary_scale = 240.0
    for y in range(height):
        for x in range(width):
            node = y * width + x
            value = float(np.clip(coverage[y, x], 0.0, 1.0))
            cost_foreground = round(((1.0 - value) ** 2) * unary_scale) + 1
            cost_background = round((value**2) * unary_scale) + 1
            if evidence.signed_distance[y, x] > 0.20:
                cost_background += round(min(evidence.signed_distance[y, x], 2.0) * 36.0)
            elif evidence.signed_distance[y, x] < -0.20:
                cost_foreground += round(min(-evidence.signed_distance[y, x], 2.0) * 36.0)
            if evidence.contour_protect[y, x] and value >= 0.04:
                cost_background += 6000
            if x in (0, width - 1) or y in (0, height - 1):
                cost_foreground += 12000
            _add_edge(rows, cols, data, source, node, cost_background)
            _add_edge(rows, cols, data, node, sink, cost_foreground)
            if x + 1 < width:
                neighbor = node + 1
                boundary = max(float(evidence.edge[y, x]), float(evidence.edge[y, x + 1]))
                pair = round(10.0 + 34.0 * (1.0 - boundary * 0.86))
                _add_edge(rows, cols, data, node, neighbor, pair)
                _add_edge(rows, cols, data, neighbor, node, pair)
            if y + 1 < height:
                neighbor = node + width
                boundary = max(float(evidence.edge[y, x]), float(evidence.edge[y + 1, x]))
                pair = round(10.0 + 34.0 * (1.0 - boundary * 0.86))
                _add_edge(rows, cols, data, node, neighbor, pair)
                _add_edge(rows, cols, data, neighbor, node, pair)
    graph = sparse.csr_matrix((np.asarray(data, np.int64), (rows, cols)), shape=(node_count + 2, node_count + 2))
    result = maximum_flow(graph, source, sink)
    residual = (graph - result.flow).tocsr()
    residual.data[residual.data <= 0] = 0
    residual.eliminate_zeros()
    reachable = breadth_first_order(residual, source, directed=True, return_predecessors=False)
    foreground_nodes = reachable[reachable < node_count]
    mask = np.zeros(node_count, dtype=bool)
    mask[foreground_nodes] = True
    return mask.reshape(height, width)


def _retain_primary_component(
    mask: np.ndarray,
    coverage: np.ndarray,
    protect: np.ndarray,
    thin_support: np.ndarray,
) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 2:
        return mask
    seeds = (coverage >= 0.58) | protect
    scores: list[tuple[float, int]] = []
    for index in range(1, count):
        component = labels == index
        overlap = int(np.count_nonzero(component & seeds))
        area = int(stats[index, cv2.CC_STAT_AREA])
        scores.append((overlap * 100000.0 + area, index))
    selected = max(scores)[1]
    output = labels == selected
    for index in range(1, count):
        if index == selected:
            continue
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        mean_coverage = float(np.mean(coverage[component]))
        strong_cells = int(np.count_nonzero(coverage[component] >= 0.58))
        protected_cells = int(np.count_nonzero(protect[component]))
        thin_cells = int(np.count_nonzero(thin_support[component]))
        # Running limbs, separated boots, cape tips and antennae can be
        # legitimate disconnected Alpha components at a 64-cell grid. Keep a
        # satellite when the source coverage strongly supports it; reject
        # low-coverage islands and unprotected single-cell tails.
        evidenced = area >= 2 and mean_coverage >= 0.42 and strong_cells >= max(1, int(np.ceil(area * 0.24)))
        protected_detail = protected_cells > 0 and mean_coverage >= 0.18 and area >= 2
        supported_branch = area >= 2 and mean_coverage >= 0.08 and thin_cells >= max(1, int(np.ceil(area * 0.30)))
        if evidenced or protected_detail or supported_branch:
            output |= component
    # Preserve protected edge evidence only when it touches an already kept
    # component. This reconnects antialiasing gaps without admitting distant
    # one-cell noise.
    near = cv2.dilate(output.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    output |= protect & near & (coverage >= 0.04)
    return output


def _restore_supported_thin_paths(mask: np.ndarray, evidence: CellEvidence) -> tuple[np.ndarray, int]:
    """Restore connected source centerlines without admitting isolated dots."""

    support = evidence.thin_support & (evidence.coverage >= 0.035)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(support.astype(np.uint8), connectivity=8)
    if count <= 1:
        return mask, 0
    output = mask.copy()
    near_foreground = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    restored = 0
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < 2 or not np.any(component & near_foreground):
            continue
        values = evidence.coverage[component]
        if float(np.mean(values)) < 0.08 and float(np.max(values)) < 0.24:
            continue
        novel = component & ~output
        restored += int(np.count_nonzero(novel))
        output |= component
    return output, restored


def _topology_cleanup(mask: np.ndarray, evidence: CellEvidence) -> tuple[np.ndarray, int, int]:
    output, restored = _restore_supported_thin_paths(mask, evidence)
    removed = 0
    kernel = np.ones((3, 3), np.uint8)
    for _ in range(3):
        neighbors = cv2.filter2D(output.astype(np.uint8), cv2.CV_16S, kernel) - output.astype(np.uint8)
        spike = output & (neighbors <= 1) & ~evidence.contour_protect & ~evidence.thin_support & (evidence.coverage < 0.68)
        removed += int(np.count_nonzero(spike))
        output[spike] = False
        neighbors = cv2.filter2D(output.astype(np.uint8), cv2.CV_16S, kernel) - output.astype(np.uint8)
        notch = ~output & (neighbors >= 7) & (evidence.coverage >= 0.12)
        output[notch] = True
    filled = ndimage.binary_fill_holes(output)
    holes = filled & ~output
    count, labels, stats, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=8)
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        source_hole = float(np.mean(evidence.coverage[component])) if np.any(component) else 0.0
        if area <= 3 or source_hole >= 0.12:
            output[component] = True
    output |= evidence.contour_protect & (evidence.coverage >= 0.05)
    output, restored_second = _restore_supported_thin_paths(output, evidence)
    return (
        _retain_primary_component(output, evidence.coverage, evidence.contour_protect, evidence.thin_support),
        removed,
        restored + restored_second,
    )


def _digital_path(start: tuple[int, int], end: tuple[int, int], sdf: np.ndarray) -> list[tuple[int, int]]:
    """Find a monotone digital straight path with explicit run-length energy."""

    sx, sy = start
    ex, ey = end
    dx = abs(ex - sx)
    dy = abs(ey - sy)
    sign_x = 0 if ex == sx else (1 if ex > sx else -1)
    sign_y = 0 if ey == sy else (1 if ey > sy else -1)
    if dx + dy == 0:
        return [start]
    # State is progress-x, progress-y, last direction, capped run length.
    queue: list[tuple[float, int, int, int, int]] = [(0.0, 0, 0, -1, 0)]
    distance: dict[tuple[int, int, int, int], float] = {(0, 0, -1, 0): 0.0}
    previous: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
    final_state: tuple[int, int, int, int] | None = None
    while queue:
        cost, px, py, last, run = heappop(queue)
        state = (px, py, last, run)
        if cost != distance.get(state):
            continue
        if px == dx and py == dy:
            final_state = state
            break
        for direction in (0, 1):
            if direction == 0 and (sign_x == 0 or px >= dx):
                continue
            if direction == 1 and (sign_y == 0 or py >= dy):
                continue
            nx = px + (direction == 0)
            ny = py + (direction == 1)
            next_run = min(5, run + 1) if direction == last else 1
            x = sx + nx * sign_x
            y = sy + ny * sign_y
            ideal = abs((ny / max(dy, 1)) - (nx / max(dx, 1))) if dx and dy else 0.0
            source_cost = min(abs(float(sdf[np.clip(y, 0, sdf.shape[0] - 1), np.clip(x, 0, sdf.shape[1] - 1)])), 2.0) * 0.16
            rhythm = 0.0
            if direction == last and next_run > 3:
                rhythm += (next_run - 3) * 0.55
            if last >= 0 and direction != last and run > 3:
                rhythm += (run - 3) * 0.22
            next_cost = cost + ideal * 0.32 + source_cost + rhythm
            next_state = (nx, ny, direction, next_run)
            if next_cost < distance.get(next_state, float("inf")):
                distance[next_state] = next_cost
                previous[next_state] = state
                heappush(queue, (next_cost, *next_state))
    if final_state is None:
        return [start, end]
    states = [final_state]
    while states[-1] in previous:
        states.append(previous[states[-1]])
    states.reverse()
    return [(sx + state[0] * sign_x, sy + state[1] * sign_y) for state in states]


def _regularize_polygon(mask: np.ndarray, evidence: CellEvidence) -> np.ndarray:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return mask
    output = np.zeros_like(mask, dtype=bool)
    for contour in contours:
        component_u8 = np.zeros_like(mask, dtype=np.uint8)
        cv2.drawContours(component_u8, [contour], -1, 1, thickness=cv2.FILLED)
        component = component_u8.astype(bool) & mask
        perimeter = cv2.arcLength(contour, closed=True)
        anchors = cv2.approxPolyDP(contour, epsilon=max(0.55, perimeter * 0.0025), closed=True).reshape(-1, 2)
        if len(anchors) < 3 or np.count_nonzero(component) < 4:
            output |= component
            continue
        path: list[tuple[int, int]] = []
        for index in range(len(anchors)):
            start = (int(anchors[index, 0]), int(anchors[index, 1]))
            next_index = (index + 1) % len(anchors)
            end = (int(anchors[next_index, 0]), int(anchors[next_index, 1]))
            segment = _digital_path(start, end, evidence.signed_distance)
            path.extend(segment[:-1])
        candidate_u8 = np.zeros_like(mask, dtype=np.uint8)
        cv2.fillPoly(candidate_u8, [np.asarray(path, dtype=np.int32).reshape(-1, 1, 2)], 1)
        candidate = candidate_u8.astype(bool)
        union = int(np.count_nonzero(candidate | component))
        intersection = int(np.count_nonzero(candidate & component))
        iou = intersection / max(union, 1)
        protected_loss = np.any((evidence.contour_protect | evidence.thin_support) & component & ~candidate)
        old_energy = float(np.mean(np.abs(component.astype(np.float32) - evidence.coverage)))
        new_energy = float(np.mean(np.abs(candidate.astype(np.float32) - evidence.coverage)))
        output |= candidate if iou >= 0.965 and not protected_loss and new_energy <= old_energy + 0.012 else component
    return output


def _contour_irregularity(mask: np.ndarray) -> tuple[tuple[tuple[int, int], ...], float]:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return (), 1.0
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2)
    if len(contour) < 3:
        return tuple((int(x), int(y)) for x, y in contour), 1.0
    delta = np.diff(np.vstack((contour, contour[:1])), axis=0)
    directions = [tuple(int(value) for value in item) for item in delta]
    runs: list[int] = []
    current = directions[0]
    length = 1
    for direction in directions[1:]:
        if direction == current:
            length += 1
        else:
            runs.append(length)
            current = direction
            length = 1
    runs.append(length)
    bad = sum(1 for value in runs if value > 3)
    return tuple((int(x), int(y)) for x, y in contour), bad / max(len(runs), 1)


def _compact_source_ink(candidate: np.ndarray, evidence: CellEvidence) -> np.ndarray:
    """Keep eye-, mouth- and fastener-sized ink without outlining all shading."""

    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate.astype(np.uint8), connectivity=8)
    output = np.zeros_like(candidate)
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        width = int(stats[index, cv2.CC_STAT_WIDTH])
        height = int(stats[index, cv2.CC_STAT_HEIGHT])
        compact = area <= 4 or (area <= 7 and width <= 3 and height <= 3)
        if compact and np.any(component & evidence.protect):
            output |= component
    return output


def compile_silhouette(evidence: CellEvidence) -> SilhouetteContour:
    mask = _global_min_cut(evidence)
    mask, removed, restored = _topology_cleanup(mask, evidence)
    mask = _regularize_polygon(mask, evidence)
    mask, removed_second, restored_second = _topology_cleanup(mask, evidence)
    contour, irregularity = _contour_irregularity(mask)
    components = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)[0] - 1
    filled = ndimage.binary_fill_holes(mask)
    holes = cv2.connectedComponents((filled & ~mask).astype(np.uint8), connectivity=8)[0] - 1
    energy = float(np.mean(np.abs(mask.astype(np.float32) - evidence.coverage)))
    return SilhouetteContour(
        mask,
        contour,
        energy,
        int(components),
        int(holes),
        removed + removed_second,
        restored + restored_second,
        irregularity,
    )


def compile_internal_strokes(evidence: CellEvidence, silhouette: np.ndarray) -> InternalStructureStroke:
    height, width = silhouette.shape
    lab = evidence.lab
    contrast = np.zeros((height, width), dtype=np.float32)
    region_change = np.zeros((height, width), dtype=bool)
    for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        shifted_lab = np.roll(lab, (dy, dx), axis=(0, 1))
        shifted_region = np.roll(evidence.region, (dy, dx), axis=(0, 1))
        contrast = np.maximum(contrast, np.linalg.norm(lab - shifted_lab, axis=2))
        region_change |= (evidence.region >= 0) & (shifted_region >= 0) & (evidence.region != shifted_region)
    interior = cv2.erode(silhouette.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0
    regional_candidate = (
        interior
        & region_change
        & (contrast >= 0.075)
        & (evidence.edge >= 0.48)
        & ((evidence.source_dark >= 0.14) | (contrast >= 0.16))
    )
    # Source-ink evidence is independent from SLIC region ownership. This is
    # important for eyes and mouths that occupy one logical cell and would
    # otherwise disappear when both sides map to the same region.
    soft_interior = silhouette & (evidence.signed_distance >= 0.35)
    ink_candidate = soft_interior & (evidence.ink_coverage >= 0.10) & (evidence.protect | (evidence.feature >= 0.54))
    candidate = regional_candidate | _compact_source_ink(ink_candidate, evidence)
    stroke = skeletonize(candidate)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(stroke.astype(np.uint8), connectivity=8)
    output = np.zeros_like(stroke)
    components = 0
    for index in range(1, count):
        component = labels == index
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area >= 2 or np.any(component & evidence.protect) or float(np.max(evidence.ink_coverage[component])) >= 0.10:
            output |= component
            components += 1
    return InternalStructureStroke(output, components, int(np.count_nonzero(output)))
