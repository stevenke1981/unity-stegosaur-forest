"""Build the editable 30,000-quad Stegosaur from the repaired detail seed."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import bpy
import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from joint_quad_cage import make_cage
from detail_quad_seed import lip_guide

FEATURES = ("Breath", "Blink", "NostrilFlare", "RoarTension", "Throat")


class Surface:
    """Original source triangles with the material charts and deformation channels."""

    def __init__(self, objects, bones):
        positions, triangles, uv, owner, charts, weights, shapes = [], [], [], [], [], [], []
        chart_keys, parents = {}, []

        def root(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i

        self.materials = [obj.data.materials[0] for obj in objects]
        for owner_id, obj in enumerate(objects):
            mesh = obj.data
            mesh.calc_loop_triangles()
            base = len(positions)
            group_names = [bones.get(vg.name) for vg in obj.vertex_groups]
            part_weights = np.zeros((len(mesh.vertices), len(bones)))
            for vertex in mesh.vertices:
                for group in vertex.groups:
                    bone = group_names[group.group]
                    if bone is not None:
                        part_weights[vertex.index, bone] = group.weight
            if obj.name == "Tongue":
                positions.extend(tuple(v.co) for v in mesh.vertices)
            else:
                positions.extend(lip_guide(v.co,w) for v,w in zip(mesh.vertices,part_weights[:,bones["LowerJaw"]],strict=True))
            weights.extend(part_weights)
            part_shapes = np.zeros((len(mesh.vertices), len(FEATURES), 3))
            if mesh.shape_keys:
                for index, name in enumerate(FEATURES):
                    key = mesh.shape_keys.key_blocks.get(name)
                    if key:
                        for i, point in enumerate(key.data):
                            part_shapes[i, index] = tuple(point.co - mesh.vertices[i].co)
            shapes.extend(part_shapes)
            layer = mesh.uv_layers.active.data
            material_name = self.materials[owner_id].name
            for tri in mesh.loop_triangles:
                verts = tuple(base + i for i in tri.vertices)
                tex = tuple(tuple(layer[i].uv) for i in tri.loops)
                nodes = []
                for index, texel in zip(verts, tex, strict=True):
                    key = (material_name, *[round(x, 6) for x in positions[index]], *[round(x, 5) for x in texel])
                    node = chart_keys.get(key)
                    if node is None:
                        node = len(parents)
                        parents.append(node)
                        chart_keys[key] = node
                    nodes.append(node)
                node = root(nodes[0])
                for other in nodes[1:]:
                    parents[root(other)] = node
                charts.append(node)
                owner.append(owner_id)
                triangles.append(verts)
                uv.append(tex)
        self.owner = np.asarray(owner)
        self.chart = np.asarray([root(i) for i in charts])
        self.positions = np.asarray(positions)
        self.triangles = np.asarray(triangles)
        self.uv = np.asarray(uv)
        self.weights = np.asarray(weights)
        self.shapes = np.asarray(shapes)
        self.bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
        self.chart_members = defaultdict(list)
        for i, c in enumerate(self.chart):
            self.chart_members[c].append(i)
        self.chart_bvh = {}

    def closest(self, point, normal, limit=.035):
        nearest, direction, tri, distance = self.bvh.find_nearest(point)
        normal = Vector(normal)
        if normal.dot(direction) > .65:
            return nearest,direction,tri,distance
        candidates = self.bvh.find_nearest_range(point,min(limit,distance+.018))
        if candidates:
            return min(candidates,key=lambda hit:hit[3]+.011*(1-normal.dot(hit[1]))**2)
        return nearest,direction,tri,distance

    def barycentric(self, point, tri):
        a, b, c = self.positions[self.triangles[tri]]
        e1, e2, q = b-a, c-a, np.asarray(point)-a
        aa, ab, bb = e1 @ e1, e1 @ e2, e2 @ e2
        d = aa*bb-ab*ab
        if d <= 1e-26:
            return np.array((1., 0., 0.))
        p, r = q @ e1, q @ e2
        v, w = (bb*p-ab*r)/d, (aa*r-ab*p)/d
        values = np.maximum((1-v-w, v, w), 0.)
        return values / values.sum()

    def texture_at(self, point, chart):
        members = self.chart_members[chart]
        if chart not in self.chart_bvh:
            original = self.triangles[members]
            vertices, inverse = np.unique(original, return_inverse=True)
            self.chart_bvh[chart] = BVHTree.FromPolygons(
                self.positions[vertices].tolist(), inverse.reshape(-1, 3).tolist(), all_triangles=True)
        location, _, index, distance = self.chart_bvh[chart].find_nearest(point)
        tri = members[index]
        return self.barycentric(location, tri) @ self.uv[tri], distance


def tongue_cage(surface):
    """Six sides of a 6 × 8 × 6 cube mapped by radial rays to the closed tongue."""
    counts = (6, 8, 6)
    center = Vector((surface.positions.min(axis=0) + surface.positions.max(axis=0)) / 2)
    extent = Vector(np.ptp(surface.positions, axis=0) / 2)
    index, positions, faces = {}, [], []
    for side in range(3):
        a, b = (side+1) % 3, (side+2) % 3
        for upper in (False, True):
            for i in range(counts[a]):
                for j in range(counts[b]):
                    face = []
                    for u, v in ((i,j),(i+1,j),(i+1,j+1),(i,j+1)):
                        grid = [0,0,0]
                        grid[side], grid[a], grid[b] = counts[side] if upper else 0, u, v
                        key = tuple(grid)
                        if key not in index:
                            ray = Vector(((2*grid[k]/counts[k]-1)*extent[k] for k in range(3))).normalized()
                            point, _, _, _ = surface.bvh.ray_cast(center, ray)
                            if point is None:
                                raise RuntimeError(f"Tongue ray missed the original surface: {key}")
                            index[key] = len(positions)
                            positions.append(tuple(point))
                        face.append(index[key])
                    faces.append(tuple(face if upper else reversed(face)))
    return positions, faces


def adjacency(faces, size):
    quad = np.asarray(faces)
    a = quad.reshape(-1)
    b = np.roll(quad, -1, axis=1).reshape(-1)
    edges = np.unique(np.sort(np.column_stack((a, b)), axis=1), axis=0)
    first, second = edges.T
    receive = np.concatenate((first, second))
    send = np.concatenate((second, first))
    count = np.bincount(receive, minlength=size)
    return quad, receive, send, count


def vertex_normals(points, quad):
    vertices = points[quad]
    area_normals = np.cross(vertices[:, 1]-vertices[:, 0], vertices[:, 2]-vertices[:, 0])
    area_normals += np.cross(vertices[:, 2]-vertices[:, 0], vertices[:, 3]-vertices[:, 0])
    normals = np.zeros_like(points)
    for column in range(4):
        np.add.at(normals, quad[:, column], area_normals)
    return normals / np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-13)


def relax(positions, faces, ring_marks, seed, source, steps):
    points = np.asarray(positions, dtype=float)
    quad, receive, send, count = adjacency(faces, len(points))
    surface = BVHTree.FromPolygons([v.co for v in seed.vertices], [p.vertices for p in seed.polygons])
    ring = np.zeros(len(points), dtype=bool)
    for edge in ring_marks:
        ring[list(edge)] = True
    tangents = np.zeros_like(points)
    ring_neighbors = defaultdict(list)
    for a, b in ring_marks:
        ring_neighbors[a].append(b)
        ring_neighbors[b].append(a)
    ring_indices = np.flatnonzero(ring)
    ring_a, ring_b = np.asarray([ring_neighbors[i] for i in ring_indices]).T

    # Keep each ring on its section while spacing the inserted vertices along it.
    # A smaller step protects eyes, claws, plate tips and the thin lip opening.
    detail = (points[:,1] < -1.28) | (points[:,2] > 1.12) | (points[:,2] < .050)
    speed = np.where(detail, .05, .32)[:,None]
    for _ in range(steps):
        mean = np.zeros_like(points)
        np.add.at(mean, receive, points[send])
        delta = mean/count[:,None] - points
        tangent = points[ring_b] - points[ring_a]
        tangent /= np.maximum(np.linalg.norm(tangent, axis=1)[:,None], 1e-13)
        tangents[ring_indices] = tangent
        delta[ring] = (delta[ring] * tangents[ring]).sum(axis=1)[:,None] * tangents[ring]
        normals = vertex_normals(points, quad)
        delta -= (delta*normals).sum(axis=1)[:,None]*normals
        proposal = points + speed*delta
        for i, point in enumerate(proposal):
            nearest, normal, _, _ = surface.find_nearest(point, .022)
            if nearest is not None and normals[i] @ np.asarray(normal) > .20:
                proposal[i] = .35 * point + .65 * np.asarray(nearest)
        points = proposal

    # Recover source detail with a bounded, normal-aware normal-direction offset.
    normals = vertex_normals(points, quad)
    distances = []
    for i, point in enumerate(points):
        nearest, normal, _, distance = source.closest(point,normals[i])
        if distance > .014 or normals[i] @ np.asarray(normal) < .50:
            continue
        delta = np.asarray(nearest)-point
        shift = np.clip(delta @ normals[i], -.006, .006)
        points[i] += (.18 if detail[i] else .55)*shift*normals[i]
        distances.append(distance)
    print("Relaxed", len(points), "vertices, source snap percentiles",
          np.percentile(distances, [50,95,99,100]).tolist(), flush=True)
    return points


def transfer(points, faces, source, bones, iterations):
    quad, receive, send, count = adjacency(faces, len(points))
    weights = np.zeros((len(points), len(bones)))
    shapes = np.zeros((len(points), len(FEATURES), 3))
    distances = np.zeros(len(points))
    # Global interpolation before splitting preserves seams in every animation.
    normals = vertex_normals(points, quad)
    for i, point in enumerate(points):
        nearest, _, triangle, distances[i] = source.closest(point,normals[i])
        t = source.triangles[triangle]
        bary = source.barycentric(nearest, triangle)
        weights[i] = bary @ source.weights[t]
        shapes[i] = np.tensordot(bary, source.shapes[t], axes=(0,0))
    raw = weights.copy()
    x, y, z = points.T
    limbs = (np.abs(x) > .165) & (y > -1.18) & (y < .35) & (z > .045) & (z < .91)
    blend = np.zeros(len(points))
    def ramp(lo, hi, a):
        s = np.clip((a-lo)/(hi-lo),0.,1.)
        return s*s*(3-2*s)
    blend[limbs] = .57 * ramp(.045,.18,z[limbs]) * (1-ramp(.64,.91,z[limbs]))
    for _ in range(iterations):
        mean = np.zeros_like(weights)
        np.add.at(mean, receive, weights[send])
        weights += blend[:,None]*(mean/count[:,None]-weights)

    # Use smooth anatomical weights with bounded spatial gradients on the
    # torso and limbs. Adjacent, skinny ring quads must not sample separate
    # original weight islands on opposite sides of a crease.
    if iterations:
        analytic = np.zeros_like(weights)
        absx = np.abs(x)
        fore_aft = ramp(-.71,-.57,y)
        front_z = ramp(.36,.79,z)
        rear_z = ramp(.48,.94,z)
        lateral = ramp(.07,.30,absx)
        front_limb = (1-fore_aft)*lateral*(1-front_z)
        rear_limb = fore_aft*lateral*(1-rear_z)
        leg_union = front_limb+rear_limb
        spine = ramp(-1.03,-.56,y)
        hip = ramp(-.64,-.09,y)
        analytic[:,bones["Bone_002"]] = (1-spine)*(1-leg_union)
        analytic[:,bones["Bone_004"]] = spine*(1-hip)*(1-leg_union)
        analytic[:,bones["Bone_001"]] = spine*hip*(1-leg_union)
        front_ankle = ramp(.018,.285,z)
        front_elbow = ramp(.086,.545,z)
        rear_ankle = ramp(.020,.29,z)
        rear_hock = ramp(.10,.58,z)
        rear_knee = ramp(.18,.73,z)
        for sign,front,rear in ((1,("Bone_032","Bone_033","Bone_034"),
                                 ("Bone_007","Bone_008","Bone_009","Bone_010")),
                                (-1,("Bone_027","Bone_028","Bone_029"),
                                 ("Bone_013","Bone_014","Bone_015","Bone_016"))):
            f = front_limb*(sign*x>0)
            h = rear_limb*(sign*x>0)
            for bone,value in zip(front,(1-front_ankle,front_ankle*(1-front_elbow),front_ankle*front_elbow)):
                analytic[:,bones[bone]] = f*value
            for bone,value in zip(rear,(1-rear_ankle,rear_ankle*(1-rear_hock),
                                       rear_ankle*rear_hock*(1-rear_knee),rear_ankle*rear_hock*rear_knee)):
                analytic[:,bones[bone]] = h*value

        # Feet attach to their existing rigid pads. Weight crossfade tapers
        # smoothly into the preserved face, tail, and back-plate weights.
        lower = 1-ramp(.67,.96,z)
        envelope = lower*ramp(-1.23,-1.10,y)*(1-ramp(.23,.48,y))
        core = envelope*ramp(.012,.072,z)
        weights += core[:,None]*(analytic-weights)

        # Marked anatomical bands can contain an extremely short edge.
        # Distance-aware interpolation limits abrupt gradients on such edges.
        lengths = np.linalg.norm(points[receive]-points[send],axis=1)
        proximity = 1/np.maximum(lengths,.002)
        total_proximity = np.bincount(receive,weights=proximity,minlength=len(points))
        for _ in range(8):
            mean = np.zeros_like(weights)
            np.add.at(mean,receive,weights[send]*proximity[:,None])
            weights += (.12*envelope)[:,None]*(mean/total_proximity[:,None]-weights)

    # Retain eight dominant influences and remove insignificant residuals.
    sort = np.argsort(weights, axis=1)
    np.put_along_axis(weights, sort[:, :-8], 0., axis=1)
    weights[weights < .0002] = 0
    total = weights.sum(axis=1, keepdims=True)
    if total.min() <= 0:
        raise RuntimeError("One or more cage vertices lost every deform influence")
    weights /= total
    changes = np.abs(weights-raw).sum(axis=1)
    evidence = {
        "nearestSourceDistanceBlenderUnitsP50P95P99Max": np.percentile(distances,[50,95,99,100]).tolist(),
        "smoothedLimbVertices": int(np.count_nonzero(blend)) if iterations else 0,
        "diffusionIterations": iterations,
        "weightChangeL1P50P95P99Max": np.percentile(changes,[50,95,99,100]).tolist(),
        "maxInfluences": int(np.count_nonzero(weights,axis=1).max()),
        "maxWeightNormalizationError": float(np.abs(1-weights.sum(axis=1)).max()),
    }
    print("Weight and shape transfer", json.dumps(evidence), flush=True)
    return weights, shapes, evidence


def split_surface(source, originals, positions, faces, marks, cage_offset, rig, bones, iterations, collection):
    points = np.asarray(positions)
    quad = np.asarray(faces)
    centers = points[quad].mean(axis=1)
    weight, shape, evidence = transfer(points, faces, source, bones, iterations)
    neutral = points.copy()
    if len(originals)>1:
        neutral[:,2] += .019*weight[:,bones["LowerJaw"]]
    normals = vertex_normals(neutral, quad)
    by_part = defaultdict(list)
    ownership = []
    uv_cache = {}
    uv_distances = []
    face_directions = np.cross(points[quad[:,1]]-points[quad[:,0]],points[quad[:,2]]-points[quad[:,0]])
    face_directions /= np.maximum(np.linalg.norm(face_directions,axis=1)[:,None],1e-14)
    for face_index, center in enumerate(centers):
        _, _, tri, _ = source.closest(center,face_directions[face_index])
        ownership.append(int(source.owner[tri]))
        by_part[int(source.owner[tri])].append((face_index, int(source.chart[tri])))
    for missing in set(range(len(originals))) - by_part.keys():
        which = source.positions[source.triangles[source.owner == missing]].mean(axis=(0,1))
        for candidate in np.argsort(np.linalg.norm(centers-which, axis=1)):
            group = ownership[candidate]
            if len(by_part[group]) <= 1:
                continue
            record = next(row for row in by_part[group] if row[0] == candidate)
            by_part[group].remove(record)
            chart = int(next(c for c,o in zip(source.chart,source.owner) if o == missing))
            by_part[missing].append((int(candidate), chart))
            ownership[candidate] = missing
            break
    results = {}
    for part, records in sorted(by_part.items()):
        old = originals[part]
        name = old.name
        ids = sorted({int(i) for index,_ in records for i in faces[index]})
        local = {value:index for index,value in enumerate(ids)}
        mesh = bpy.data.meshes.new(name + "_QuadMesh")
        mesh.from_pydata(neutral[ids].tolist(), [], [tuple(local[i] for i in faces[index]) for index,_ in records])
        mesh.update()
        mesh.materials.append(source.materials[part])
        uv = mesh.uv_layers.new(name="UVMap")
        ring = mesh.attributes.new(name="JointRing", type="INT", domain="EDGE")
        mesh.attributes.new(name="CageVertex", type="INT", domain="POINT")
        mesh.attributes["CageVertex"].data.foreach_set("value", [i+cage_offset for i in ids])
        ring.data.foreach_set("value", [marks.get(tuple(sorted(ids[i] for i in e.vertices)), 0) for e in mesh.edges])
        for polygon, (face_index, chart) in zip(mesh.polygons, records, strict=True):
            polygon.use_smooth = True
            for loop, vertex in zip(polygon.loop_indices, faces[face_index], strict=True):
                cache_key = (chart, vertex)
                if cache_key not in uv_cache:
                    uv_cache[cache_key] = source.texture_at(points[vertex], chart)
                texel, distance = uv_cache[cache_key]
                uv.data[loop].uv = texel
                uv_distances.append(distance)
        mesh.normals_split_custom_set_from_vertices(normals[ids].tolist())
        old.name = name + "_Source"
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        obj.parent = rig
        armature = obj.modifiers.new(name="CreatureDeform", type="ARMATURE")
        armature.object = rig
        for bone, index in bones.items():
            used = [(local[i], weight[i,index]) for i in ids if weight[i,index] > 0]
            if used:
                group = obj.vertex_groups.new(name=bone)
                for vertex, value in used:
                    group.add([vertex], value, "REPLACE")
        for index, feature in enumerate(FEATURES):
            delta = shape[ids,index]
            if np.max(np.abs(delta)) < 1e-6:
                continue
            if obj.data.shape_keys is None:
                obj.shape_key_add(name="Basis")
            key = obj.shape_key_add(name=feature, from_mix=False)
            key.data.foreach_set("co", (neutral[ids]+delta).ravel())
            driver = key.driver_add("value").driver
            driver.type = "AVERAGE"
            variable = driver.variables.new()
            variable.name = "amount"
            variable.type = "SINGLE_PROP"
            variable.targets[0].id = rig
            control = "CTRL_Pelvis" if feature == "Breath" else "CTRL_Head"
            variable.targets[0].data_path = f'pose.bones["{control}"]["Shape_{feature}"]'
        results[name] = len(mesh.polygons)
    evidence["sourceUvCharts"] = len(source.chart_members)
    evidence["uvChartProjectionBlenderUnitsP50P95P99Max"] = np.percentile(uv_distances,[50,95,99,100]).tolist()
    return results, evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--relax", type=int, default=8)
    parser.add_argument("--skin-smooth", type=int, default=10)
    opt = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    seed = bpy.data.objects["RetopologySeed"]
    rig = next(obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE")
    bones = {name:i for i,name in enumerate(b.name for b in rig.data.bones if b.use_deform)}
    originals = sorted([obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.parent == rig],
                       key=lambda obj:obj.name)
    assert len(originals) == 25 and len(bones) == 36, (len(originals),len(bones))
    tongue = next(o for o in originals if o.name == "Tongue")
    exterior = [o for o in originals if o != tongue]
    collection = exterior[0].users_collection[0]
    points, faces, marks, bands, budget = make_cage(seed.data, 29736)
    source = Surface(exterior,bones)
    points = relax(points,faces,marks,seed.data,source,opt.relax)
    part_counts, metrics = split_surface(source,exterior,points,faces,marks,0,rig,bones,opt.skin_smooth,collection)
    surface = Surface([tongue],bones)
    tongue_points, tongue_faces = tongue_cage(surface)
    tongue_counts, tongue_metrics = split_surface(surface,[tongue],tongue_points,tongue_faces,{},len(points),rig,bones,0,collection)
    parts = part_counts | tongue_counts
    assert len(parts) == 25 and sum(parts.values()) == 30000 and parts["Tongue"] == 264, parts
    for obj in (*originals, seed):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    rig["Retopology"] = "30000 quads / 25 render parts / 54 closed edge rings"
    rig["TopologyCoordinates"] = "Blender: Z up, -Y forward"
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.context.scene.frame_set(1)
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opt.output.resolve()), compress=True)
    report = {
        "quads": sum(parts.values()), "renderParts": parts,
        "deformBones": len(bones), "ringLabels": bands,
        "markedEdges": len(marks), "surfaceBudget": budget, "geometryRelaxationIterations": opt.relax,
        "exteriorTransfer": metrics, "tongueTransfer": tongue_metrics,
    }
    opt.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("RET0POLOGY", json.dumps(report,ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
