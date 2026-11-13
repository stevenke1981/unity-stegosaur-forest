"""Construct anatomically located quad edge rings on a continuous retopology cage."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil
import bpy
import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

@dataclass(frozen=True)
class Band:
    name: str
    center: tuple
    axis: tuple
    offsets: tuple
    radius: float
    radial: bool = False


def joint_bands():
    result = []
    for side, sign in (("L", 1), ("R", -1)):
        result += [
            Band(f"Shoulder.{side}", (sign*.355,-.782,.430), (.24,.292,.36), (.94,1.0,1.06), .32, True),
            Band(f"Elbow.{side}", (sign*.305,-.770,.286), (0,0,1), (-.034,0,.034), .22),
            Band(f"FrontAnkle.{side}", (sign*.309,-.836,.081), (0,0,1), (-.024,0,.024), .23),
            Band(f"Hip.{side}", (sign*.45,-.105,.57), (.315,.34,.5), (.94,1.0,1.06), .38, True),
            Band(f"Knee.{side}", (sign*.385,-.016,.294), (0,0,1), (-.020,-.002,.016), .23),
            Band(f"Hock.{side}", (sign*.389,-.025,.327), (0,0,1), (-.008,0,.008), .24),
            Band(f"HindAnkle.{side}", (sign*.406,-.025,.065), (0,0,1), (-.020,0,.020), .25),
        ]
    result += [
        Band("Neck", (.006,-1.165,.778), (0,1,0), (-.028,0,.028), .32),
        Band("TailRoot", (.006,.396,.675), (0,1,0), (-.033,0,.033), .38),
        Band("TailJoint2", (.005,.731,.575), (0,1,0), (-.021,0,.021), .45),
        Band("TailJoint3", (.005,1.128,.535), (0,1,0), (-.034,0,.034), .22),
    ]
    return result


def closed_components(segments):
    adj = defaultdict(list)
    for a,b in segments:
        adj[a].append(b)
        adj[b].append(a)
    seen = set()
    for point in adj:
        if point in seen:
            continue
        seen.add(point)
        pending, component = [point], []
        while pending:
            current = pending.pop()
            component.append(current)
            for neighbor in adj[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        if len(component) >= 6 and all(len(adj[node]) == 2 for node in component):
            yield component


def cut_ring(bm, band, level, tag, marks, *, silent=False):
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    points = [v.co.copy() for v in bm.verts]
    center = Vector(band.center)
    normal = Vector(band.axis).normalized()
    threshold = level + .000193
    if band.radial:
        scale = Vector(band.axis)
        values = [Vector(((v.x-center.x)/scale.x, (v.y-center.y)/scale.y, (v.z-center.z)/scale.z)).length - threshold
                  for v in points]
    else:
        values = [(v-center).dot(normal) - threshold for v in points]
    segments, cross = {}, {}
    for f in bm.faces:
        if min(values[v.index] for v in f.verts) >= 0 or max(values[v.index] for v in f.verts) <= 0:
            continue
        crossed = [e for e in f.edges if values[e.verts[0].index] * values[e.verts[1].index] < 0]
        if len(crossed) != 2:
            continue
        for edge in crossed:
            a,b = edge.verts
            frac = values[a.index] / (values[a.index] - values[b.index])
            cross[edge] = (a, frac, a.co.lerp(b.co, frac))
        segments[frozenset(crossed)] = f
    groups = []
    for component in closed_components(segments):
        coords = [cross[e][2] for e in component]
        mean = sum(coords, Vector()) / len(coords)
        distance = (mean-center).length
        spread = max((p-center).length for p in coords)
        if band.radial:
            if mean.z < center.z + .05 or min(p.z for p in coords) < .27 or abs(mean.x-center.x) > .22:
                continue
            distance = (mean-Vector((center.x*.77,center.y,center.z+.15))).length
            # The longer axis follows the upper limb patch and straddles its skeletal attachment.
            if max(abs(p.x - center.x) for p in coords) > band.radius:
                continue
        elif spread > band.radius:
            continue
        groups.append((distance, component, tuple(mean), spread))
    if not groups:
        if not silent:
            print("missing", band.name, level, flush=True)
        return None
    _, selected, mean, radius = min(groups, key=lambda row: row[0])
    selected = set(selected)
    connections = [(tuple(pair), f) for pair, f in segments.items() if pair.issubset(selected)]
    # No crossing with another feature ring is allowed. That would split its edges
    # and make local deformation coupling harder to audit.
    if any(e[marks] for e in selected):
        if not silent:
            print("crossed previous", band.name, level, flush=True)
        return None
    new_vertices = {}
    for edge in sorted(selected, key=lambda e:e.index):
        a, frac, point = cross[edge]
        new_edge, vertex = bmesh.utils.edge_split(edge, a, frac)
        vertex.co = point
        new_vertices[edge] = vertex
    for (a,b), face in connections:
        _, loop = bmesh.utils.face_split(face, new_vertices[a], new_vertices[b])
        if loop is None:
            raise RuntimeError(f"Failed to connect ring {band.name} / {level}.")
        loop.edge[marks] = tag
    if not silent:
        print("closed ring", band.name, level, len(connections), [round(x,3) for x in mean], round(radius,3), flush=True)
    return {"edgesBeforeQuadRefine":len(connections), "center":list(mean), "radiusMax":radius}


def split_to_quads(bm):
    # Catmull-Clark topology refinement without moving old points. Every n-gon
    # becomes n quads, including the 3/5-sided faces produced by local ring cuts.
    bm.verts.index_update()
    bm.faces.index_update()
    bm.edges.index_update()
    layer = bm.edges.layers.int.get("joint_ring")
    count = len(bm.verts)
    base = [tuple(v.co) for v in bm.verts]
    base.extend(tuple((e.verts[0].co + e.verts[1].co) * .5) for e in bm.edges)
    base.extend(tuple(f.calc_center_median()) for f in bm.faces)
    midpoint = {e: count+e.index for e in bm.edges}
    face_start = count+len(bm.edges)
    marks, faces = {}, []
    for e in bm.edges:
        if e[layer]:
            for v in e.verts:
                marks[tuple(sorted((v.index,midpoint[e])))] = e[layer]
    for f in bm.faces:
        for loop in f.loops:
            faces.append((loop.vert.index,midpoint[loop.edge],face_start+f.index,midpoint[loop.link_loop_prev.edge]))
    return base, faces, marks


def refine_quad_cross_sections(points, faces, ring_marks, target):
    """Refine existing quad strips until the exact target, preserving conformity."""
    if len(faces) == target:
        return points, faces, ring_marks, []
    missing = target-len(faces)
    if missing < 0:
        raise ValueError(f"Use a lower-resolution seed; coarse cage refined to {len(faces)} quads.")
    adj = defaultdict(list)
    for fid, face in enumerate(faces):
        for i in range(4):
            adj[tuple(sorted((face[i],face[(i+1)%4])))].append((fid,i))
    xyz = np.asarray(points)
    found = {}
    visited = set()
    for first in sorted(adj):
        if first in visited or first in ring_marks or len(adj[first]) != 2:
            continue
        route, used = [], set()
        start = adj[first][0]
        face, axis = start
        while face not in used:
            used.add(face)
            quad = faces[face]
            e1 = tuple(sorted((quad[axis],quad[(axis+1)%4])))
            e2 = tuple(sorted((quad[(axis+2)%4],quad[(axis+3)%4])))
            visited.add(e1)
            if e1 in ring_marks or e2 in ring_marks or len(adj[e2]) != 2:
                break
            route.append((face,axis,e1,e2))
            next_face = next(pair for pair in adj[e2] if pair[0] != face)
            face,axis = next_face
            if (face,axis) == start:
                size = len(route)
                if size <= missing:
                    used_faces = frozenset(used)
                    if used_faces not in found:
                        areas = [np.linalg.norm(np.cross(xyz[faces[k][1]]-xyz[faces[k][0]],
                                                         xyz[faces[k][3]]-xyz[faces[k][0]])) for k in used]
                        found[used_faces] = (size, float(np.mean(areas)), route)
                break
    ranked = sorted(found.values(), key=lambda row:(-row[1],len(row[2])))
    # At most one selected strip touches each face. Try short combinations of
    # disjoint closed strips; repeat a strip only to solve a dense residual.
    exact = []
    for size, _, route in ranked:
        if size == missing:
            exact = [(1,route)]
            break
    if not exact:
        by_length = defaultdict(list)
        for size, quality, route in ranked:
            by_length[size].append((quality,route))
        best = None
        for size, quality, route in ranked:
            set_a = {row[0] for row in route}
            for a_count in range(1, min(9,missing//size)+1):
                remainder = missing-a_count*size
                if remainder == 0:
                    key = (a_count, -quality)
                    if best is None or key < best:
                        best, exact = key, [(a_count,route)]
                    continue
                for b_size, options in by_length.items():
                    b_count, rest = divmod(remainder,b_size)
                    if rest or not 1 <= b_count <= 8 or (best and max(a_count,b_count) > best[0]):
                        continue
                    option = next(((q,r) for q,r in options if not any(row[0] in set_a for row in r)),None)
                    if option:
                        q,r = option
                        key = (max(a_count,b_count),-(quality+q))
                        if best is None or key < best:
                            best, exact = key, [(a_count,route),(b_count,r)]
    if not exact:
        raise RuntimeError(f"Could not add {missing} faces with disjoint conforming strips. Strip lengths: {sorted(set(v[0] for v in ranked))}")
    new_points = list(points)
    replacement = {}
    for inserts, route in exact:
        ids = {}
        for _,_,e1,e2 in route:
            for edge in (e1,e2):
                if edge not in ids:
                    a,b = map(lambda i:np.asarray(points[i]),edge)
                    ids[edge] = []
                    for index in range(1,inserts+1):
                        p = tuple(a+(b-a)*index/(inserts+1))
                        ids[edge].append(len(new_points))
                        new_points.append(p)
        for fid,axis,e1,e2 in route:
            q = tuple(faces[fid][(axis+j)%4] for j in range(4))
            first = ids[e1] if q[0]==e1[0] else ids[e1][::-1]
            second = ids[e2] if q[3]==e2[0] else ids[e2][::-1]
            bottom = [q[0],*first,q[1]]
            top = [q[3],*second,q[2]]
            replacement[fid] = [(bottom[j],bottom[j+1],top[j+1],top[j]) for j in range(inserts+1)]
    refined = [new for i,face in enumerate(faces) for new in replacement.get(i,[face])]
    if len(refined) != target:
        raise RuntimeError("Refined strip count diverged from target.")
    return new_points, refined, ring_marks, [dict(quadsPerStrip=len(route),extraLoops=n) for n,route in exact]


def make_cage(seed, target):
    bm=bmesh.new()
    bm.from_mesh(seed)
    marks = bm.edges.layers.int.new("joint_ring")
    evidence = []
    for band in joint_bands():
        for idx, offset in enumerate(band.offsets,1):
            tag = len(evidence)+1
            result = cut_ring(bm,band,offset,tag,marks)
            if result is None:
                raise RuntimeError(f"No closed non-intersecting ring for {band.name} / {idx}")
            evidence.append({"tag":tag,"joint":band.name,"ring":idx,"offset":offset,**result})
    topology = Counter(len(f.verts) for f in bm.faces)
    print("coarse cage after joint cuts", dict(topology), "refinement produces", sum(k*v for k,v in topology.items()), flush=True)
    points, faces, ring_marks = split_to_quads(bm)
    bm.free()
    points, faces, ring_marks, budget = refine_quad_cross_sections(points,faces,ring_marks,target)
    return points, faces, ring_marks, evidence, budget
