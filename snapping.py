# ------------------------------------------------------------
#  Snapping: on-screen scene geometry and snap searches
# ------------------------------------------------------------
#
#  Everything is measured the way it appears in the viewport: points are
#  projected through the 3D view's perspective matrix with numpy, so "bounds"
#  means the 2D box a mesh actually occupies on screen (not its 3D bounding
#  box), and vertex / edge picking ignores geometry hidden behind other
#  objects (checked with a ray cast).

import math

import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils import geometry as mu_geometry

from .common import get_region_3d, get_view_region, screen_to_plane, snap_kind_from_event


GEOM_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}


def evaluated_local_coords(obj, depsgraph, limit=None):
    """(N,3) float32 local vertex coordinates of an object's evaluated
    geometry, or None (no geometry / more than `limit` vertices)."""
    if obj.type not in GEOM_TYPES:
        return None
    ev = obj.evaluated_get(depsgraph)
    try:
        mesh = ev.to_mesh()
    except RuntimeError:
        return None
    if mesh is None:
        return None
    try:
        n = len(mesh.vertices)
        if not n or (limit is not None and n > limit):
            return None
        co = np.empty(n * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        return co.reshape(n, 3)
    finally:
        ev.to_mesh_clear()


class GeoCache:
    MAX_VERTS = 2_500_000

    def __init__(self, context, want_edges=False, depsgraph=None):
        self.scene = context.scene
        self.depsgraph = depsgraph or context.evaluated_depsgraph_get()
        self.view_layer = context.view_layer
        self.want_edges = want_edges
        self.total_verts = 0
        self.skipped = 0
        self._objs = None
        self._geo = {}
        self._screen = {}
        self._view_key = None
        self.persp = None
        self.size = (1, 1)

    # -- scene data ---------------------------------------------

    def objects(self):
        if self._objs is None:
            self._objs = [o for o in self.view_layer.objects if o.visible_get()]
        return self._objs

    def _load(self, obj):
        key = obj.as_pointer()
        if key in self._geo:
            return self._geo[key]
        data = None
        if obj.type in GEOM_TYPES:
            ev = obj.evaluated_get(self.depsgraph)
            try:
                mesh = ev.to_mesh()
            except RuntimeError:
                mesh = None
            if mesh is not None:
                try:
                    n = len(mesh.vertices)
                    if n and self.total_verts + n <= self.MAX_VERTS:
                        co = np.empty(n * 3, dtype=np.float32)
                        mesh.vertices.foreach_get("co", co)
                        mw = np.array(ev.matrix_world, dtype=np.float32)
                        world = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
                        edges = None
                        if self.want_edges and len(mesh.edges):
                            e = np.empty(len(mesh.edges) * 2, dtype=np.int32)
                            mesh.edges.foreach_get("vertices", e)
                            edges = e.reshape(-1, 2)
                        data = {"world": world, "edges": edges}
                        self.total_verts += n
                    elif n:
                        self.skipped += 1
                finally:
                    ev.to_mesh_clear()
        self._geo[key] = data
        return data

    # -- projection ---------------------------------------------

    def sync(self, context):
        rv3d = get_region_3d(context)
        region = get_view_region(context)
        key = (tuple(c for row in rv3d.perspective_matrix for c in row),
               region.width, region.height)
        if key != self._view_key:
            self._view_key = key
            self._screen.clear()
            self.persp = np.array(rv3d.perspective_matrix, dtype=np.float32)
            self.size = (region.width, region.height)

    def project(self, world):
        """(N,3) world points -> ((N,2) screen px, (N,) in-front-of-camera mask)."""
        M = self.persp
        w = world @ M[3, :3] + M[3, 3]
        x = world @ M[0, :3] + M[0, 3]
        y = world @ M[1, :3] + M[1, 3]
        valid = w > 1e-6
        with np.errstate(all='ignore'):
            sx = (x / w * 0.5 + 0.5) * self.size[0]
            sy = (y / w * 0.5 + 0.5) * self.size[1]
        return np.stack([sx, sy], axis=1).astype(np.float32), valid

    def screen(self, context, obj):
        self.sync(context)
        key = obj.as_pointer()
        if key in self._screen:
            return self._screen[key]
        geo = self._load(obj)
        out = None
        if geo is not None:
            xy, valid = self.project(geo["world"])
            if valid.any():
                vx = xy[valid]
                rect = (float(vx[:, 0].min()), float(vx[:, 1].min()),
                        float(vx[:, 0].max()), float(vx[:, 1].max()))
                out = {"xy": xy, "valid": valid, "rect": rect, "geo": geo, "ab": None}
        self._screen[key] = out
        return out

    # -- queries --------------------------------------------------

    def _origin_xy(self, obj):
        loc = np.array([tuple(obj.matrix_world.translation)], dtype=np.float32)
        xy, valid = self.project(loc)
        return (float(xy[0, 0]), float(xy[0, 1])) if valid[0] else None

    def origins(self, context):
        self.sync(context)
        out = []
        for o in self.objects():
            p = self._origin_xy(o)
            if p is not None:
                out.append((o, p))
        return out

    def bounds(self, context, objs=None):
        """[(object, (x0, y0, x1, y1))] - on-screen 2D bounds of each object.
        Objects without geometry (empties, lights...) give a single point."""
        self.sync(context)
        out = []
        for o in (objs if objs is not None else self.objects()):
            scr = self.screen(context, o)
            if scr is not None:
                out.append((o, scr["rect"]))
            else:
                p = self._origin_xy(o)
                if p is not None:
                    out.append((o, (p[0], p[1], p[0], p[1])))
        return out

    def _visible(self, context, world, q, tol_scale=0.002):
        region, rv3d = get_view_region(context), get_region_3d(context)
        ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, q)
        rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, q)
        hit = self.scene.ray_cast(self.depsgraph, ro, rd)
        if not hit[0]:
            return True
        d_pt = (world - ro).dot(rd)
        d_hit = (hit[1] - ro).dot(rd)
        return d_hit >= d_pt - (tol_scale * abs(d_pt) + 1e-4)

    def nearest_vertex(self, context, mx, my, radius):
        """Visible vertex nearest the cursor within `radius` px, or None."""
        self.sync(context)
        r2 = radius * radius
        cands = []
        for obj in self.objects():
            scr = self.screen(context, obj)
            if scr is None:
                continue
            x0, y0, x1, y1 = scr["rect"]
            if mx < x0 - radius or mx > x1 + radius or my < y0 - radius or my > y1 + radius:
                continue
            xy = scr["xy"]
            with np.errstate(all='ignore'):
                d2 = (xy[:, 0] - mx) ** 2 + (xy[:, 1] - my) ** 2
            d2 = np.where(scr["valid"], d2, np.inf)
            idx = np.nonzero(d2 <= r2)[0]
            if idx.size == 0:
                continue
            if idx.size > 8:
                idx = idx[np.argpartition(d2[idx], 8)[:8]]
            for i in idx:
                cands.append((float(d2[i]), obj, int(i)))
        cands.sort(key=lambda c: c[0])
        for _d2, obj, i in cands[:12]:
            scr = self._screen[obj.as_pointer()]
            world = Vector([float(c) for c in scr["geo"]["world"][i]])
            q = (float(scr["xy"][i, 0]), float(scr["xy"][i, 1]))
            if self._visible(context, world, q):
                return {"screen": q, "obj": obj, "world": world}
        return None

    def nearest_edge(self, context, mx, my, radius):
        """Visible mesh edge nearest the cursor within `radius` px, or None.
        Needs GeoCache(..., want_edges=True)."""
        self.sync(context)
        r2 = radius * radius
        M = np.array([mx, my], dtype=np.float32)
        cands = []
        for obj in self.objects():
            scr = self.screen(context, obj)
            if scr is None or scr["geo"]["edges"] is None:
                continue
            x0, y0, x1, y1 = scr["rect"]
            if mx < x0 - radius or mx > x1 + radius or my < y0 - radius or my > y1 + radius:
                continue
            if scr["ab"] is None:
                e = scr["geo"]["edges"]
                xy, valid = scr["xy"], scr["valid"]
                A, B = xy[e[:, 0]], xy[e[:, 1]]
                scr["ab"] = (A, B, valid[e[:, 0]] & valid[e[:, 1]],
                             np.minimum(A[:, 0], B[:, 0]), np.maximum(A[:, 0], B[:, 0]),
                             np.minimum(A[:, 1], B[:, 1]), np.maximum(A[:, 1], B[:, 1]))
            A, B, ok, xlo, xhi, ylo, yhi = scr["ab"]
            # Cheap bounding-box prefilter, so the real maths only runs on the
            # few edges near the cursor (matters on dense meshes).
            with np.errstate(all='ignore'):
                sel = np.nonzero(ok & (xlo <= mx + radius) & (xhi >= mx - radius)
                                 & (ylo <= my + radius) & (yhi >= my - radius))[0]
            if sel.size == 0:
                continue
            As, Bs = A[sel], B[sel]
            with np.errstate(all='ignore'):
                AB = Bs - As
                L2 = (AB * AB).sum(axis=1)
                t = np.clip(np.nan_to_num(((M - As) * AB).sum(axis=1) / L2), 0.0, 1.0)
                Q = As + AB * t[:, None]
                d2 = ((Q - M) ** 2).sum(axis=1)
                mask = (L2 > 1.0) & (d2 <= r2)
            idx = np.nonzero(mask)[0]
            if idx.size == 0:
                continue
            if idx.size > 8:
                idx = idx[np.argpartition(d2[idx], 8)[:8]]
            for i in idx:
                cands.append((float(d2[i]), obj, int(sel[i]), float(t[i])))
        cands.sort(key=lambda c: c[0])
        region, rv3d = get_view_region(context), get_region_3d(context)
        for _d2, obj, i, t in cands[:12]:
            scr = self._screen[obj.as_pointer()]
            A, B = scr["ab"][0], scr["ab"][1]
            e = scr["geo"]["edges"][i]
            WA = Vector([float(c) for c in scr["geo"]["world"][e[0]]])
            WB = Vector([float(c) for c in scr["geo"]["world"][e[1]]])
            q = (float(A[i, 0] + t * (B[i, 0] - A[i, 0])),
                 float(A[i, 1] + t * (B[i, 1] - A[i, 1])))
            # True 3D point of the edge under the cursor, for the occlusion test.
            ro = view3d_utils.region_2d_to_origin_3d(region, rv3d, q)
            rd = view3d_utils.region_2d_to_vector_3d(region, rv3d, q)
            near = mu_geometry.intersect_line_line(ro, ro + rd, WA, WB)
            pe = WB if near is None else near[1]
            ab = WB - WA
            if ab.length_squared > 0:
                t3 = max(0.0, min(1.0, (pe - WA).dot(ab) / ab.length_squared))
                pe = WA + ab * t3
            if self._visible(context, pe, q):
                return {
                    "a": (float(A[i, 0]), float(A[i, 1])),
                    "b": (float(B[i, 0]), float(B[i, 1])),
                    "q": q, "obj": obj,
                    "wa": WA, "wb": WB, "pe": pe,
                }
        return None


def bounds_key_points(rect):
    """Corners, edge midpoints and centre of a screen rectangle."""
    x0, y0, x1, y1 = rect
    xm, ym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return [(x0, y0), (xm, y0), (x1, y0),
            (x0, ym), (xm, ym), (x1, ym),
            (x0, y1), (xm, y1), (x1, y1)]


POINT_KINDS = ('ORIGIN', 'BOUNDS', 'VERTEX')


def active_snap_kinds(settings, event):
    """Snap targets in force right now for dragging a guide: the toggles that
    are switched on, plus whatever the held shortcut asks for."""
    kinds = set()
    if settings.snap_grid:
        kinds.add('GRID')
    if settings.snap_origin:
        kinds.add('ORIGIN')
    if settings.snap_bounds:
        kinds.add('BOUNDS')
    if settings.snap_vertex:
        kinds.add('VERTEX')
    held = snap_kind_from_event(event)
    if held:
        kinds.add(held)
    return kinds


def snap_targets(cache, context, kinds):
    """Point targets for ORIGIN / BOUNDS (any mix):
    ((K,2) array, [{label, rect, obj, kind}])."""
    if isinstance(kinds, str):
        kinds = {kinds}
    pts, meta = [], []
    if 'ORIGIN' in kinds:
        for obj, (x, y) in cache.origins(context):
            pts.append((x, y))
            meta.append({"label": f"{obj.name} origin", "rect": None, "obj": obj, "kind": 'ORIGIN'})
    if 'BOUNDS' in kinds:
        for obj, rect in cache.bounds(context):
            kp = bounds_key_points(rect)
            pts.extend(kp)
            meta.extend([{"label": f"{obj.name} bounds", "rect": rect, "obj": obj, "kind": 'BOUNDS'}] * len(kp))
    return np.array(pts, dtype=np.float64).reshape(-1, 2), meta


def best_point_snap(cache, context, kinds, mx, my, radius):
    """Nearest ORIGIN / BOUNDS / VERTEX target to the cursor, among the kinds
    that are on. -> {kind, label, pt, rect, obj, world?, score} or None"""
    cands = []
    if kinds & {'ORIGIN', 'BOUNDS'}:
        xy, meta = snap_targets(cache, context, kinds)
        i = snap_point_to_targets((mx, my), xy, radius)
        if i is not None:
            m_ = meta[i]
            cands.append({"kind": m_["kind"], "label": m_["label"], "rect": m_["rect"],
                          "obj": m_["obj"], "pt": (float(xy[i, 0]), float(xy[i, 1])),
                          "score": math.hypot(xy[i, 0] - mx, xy[i, 1] - my)})
    if 'VERTEX' in kinds:
        found = cache.nearest_vertex(context, mx, my, radius)
        if found is not None:
            q = found["screen"]
            cands.append({"kind": 'VERTEX', "label": f"{found['obj'].name} vertex", "rect": None,
                          "obj": found["obj"], "pt": q, "world": found["world"],
                          "score": math.hypot(q[0] - mx, q[1] - my)})
    return min(cands, key=lambda c: c["score"]) if cands else None


def best_line_snap(cache, context, kinds, P, n, mx, my, radius):
    """Target closest to the line (point P, normal n), among the kinds that are
    on. -> best_point_snap's dict plus "dist" (signed distance to the line)."""
    cands = []
    if kinds & {'ORIGIN', 'BOUNDS'}:
        xy, meta = snap_targets(cache, context, kinds)
        hit = snap_line_to_targets(P, n, xy, radius)
        if hit is not None:
            i, dist = hit
            m_ = meta[i]
            cands.append({"kind": m_["kind"], "label": m_["label"], "rect": m_["rect"],
                          "obj": m_["obj"], "pt": (float(xy[i, 0]), float(xy[i, 1])),
                          "dist": dist, "score": abs(dist)})
    if 'VERTEX' in kinds:
        found = cache.nearest_vertex(context, mx, my, radius)
        if found is not None:
            q = found["screen"]
            dist = n[0] * (q[0] - P[0]) + n[1] * (q[1] - P[1])
            cands.append({"kind": 'VERTEX', "label": f"{found['obj'].name} vertex", "rect": None,
                          "obj": found["obj"], "pt": q, "world": found["world"],
                          "dist": dist, "score": abs(dist)})
    return min(cands, key=lambda c: c["score"]) if cands else None


def snap_line_to_targets(P, n, xy, radius):
    """Target whose distance to the line (point P, normal n) is smallest.
    Returns (index, signed distance) or None if none within `radius`."""
    if len(xy) == 0:
        return None
    dist = (xy[:, 0] - P[0]) * n[0] + (xy[:, 1] - P[1]) * n[1]
    i = int(np.argmin(np.abs(dist)))
    if abs(dist[i]) <= radius:
        return i, float(dist[i])
    return None


def snap_point_to_targets(M, xy, radius, min_dist=0.0):
    """Target nearest the cursor within `radius` px (and farther than min_dist)."""
    if len(xy) == 0:
        return None
    d2 = (xy[:, 0] - M[0]) ** 2 + (xy[:, 1] - M[1]) ** 2
    d2 = np.where(d2 > min_dist * min_dist, d2, np.inf)
    i = int(np.argmin(d2))
    return i if d2[i] <= radius * radius else None


def snap_move_to_guides(refs, delta, lines, radius):
    """Snap a moving set of reference points to guide lines.

    refs   - start positions [(x, y)] of the points that may snap
    delta  - raw screen movement (dx, dy)
    lines  - [(key, P, n)] a point on and the normal of each line
    Returns (delta, hits) with hits = [(key, ref_index, residual)]. Snaps to
    one line, or to two non-parallel lines at once (landing on their crossing).
    `residual` is how far that ref was from its line before snapping.
    """
    if not len(refs) or not lines:
        return delta, []
    R = np.asarray(refs, dtype=np.float64).reshape(-1, 2)

    def best(d, excl_n=None):
        found = None
        for gi, (_key, P, n) in enumerate(lines):
            if excl_n is not None and abs(n[0] * excl_n[0] + n[1] * excl_n[1]) > 0.85:
                continue
            dist = (R[:, 0] + d[0] - P[0]) * n[0] + (R[:, 1] + d[1] - P[1]) * n[1]
            k = int(np.argmin(np.abs(dist)))
            if abs(dist[k]) <= radius and (found is None or abs(dist[k]) < found[0]):
                found = (abs(dist[k]), gi, k, float(dist[k]))
        return found

    b1 = best(delta)
    if b1 is None:
        return delta, []
    _a, g1, k1, dist1 = b1
    key1, P1, n1 = lines[g1]
    d1 = (delta[0] - n1[0] * dist1, delta[1] - n1[1] * dist1)
    hits = [(key1, k1, dist1)]
    b2 = best(d1, excl_n=n1)
    if b2 is None:
        return d1, hits
    _a, g2, k2, dist2 = b2
    key2, P2, n2 = lines[g2]
    c1 = n1[0] * (P1[0] - R[k1, 0]) + n1[1] * (P1[1] - R[k1, 1])
    c2 = n2[0] * (P2[0] - R[k2, 0]) + n2[1] * (P2[1] - R[k2, 1])
    det = n1[0] * n2[1] - n1[1] * n2[0]
    if abs(det) < 1e-6:
        return d1, hits
    hits.append((key2, k2, dist2))
    return ((c1 * n2[1] - c2 * n1[1]) / det, (n1[0] * c2 - n2[0] * c1) / det), hits


def snap_world_point(context, hit):
    """World-space position of a snap target found in screen space.

    VERTEX: the vertex itself. ORIGIN: the object's origin. BOUNDS: the point
    on the plane through the object's origin (facing the view) that shows at
    that spot of the on-screen box, so the target is where it LOOKS to be."""
    if hit["kind"] == 'VERTEX':
        return Vector(hit["world"])
    origin = hit["obj"].matrix_world.translation.copy()
    if hit["kind"] == 'ORIGIN':
        return origin
    W = screen_to_plane(get_view_region(context), get_region_3d(context), hit["pt"], origin)
    return origin if W is None else W
