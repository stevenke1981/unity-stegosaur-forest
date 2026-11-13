"""Reopen the editable Blender deliverable and measure the turn deformation."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

import bpy
import numpy as np


def coordinates(mesh):
    points = np.empty(len(mesh.vertices)*3,dtype=np.float32)
    mesh.vertices.foreach_get("co",points)
    return points.reshape((-1,3))


def check_topology(parts, build):
    faces, rings, seam = Counter(), defaultdict(set), {}
    counts, influences, minimum_area = {}, 0, float("inf")
    mirrored_vertices = 0
    per_part = {}
    for part in parts:
        mesh = part.data
        assert "CageVertex" in mesh.attributes and "JointRing" in mesh.attributes
        ids = np.zeros(len(mesh.vertices),dtype=np.int32)
        mesh.attributes["CageVertex"].data.foreach_get("value", ids)
        tag_ids = np.zeros(len(mesh.edges),dtype=np.int32)
        mesh.attributes["JointRing"].data.foreach_get("value", tag_ids)
        keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
        names = {key.name for key in keys} - {"Basis"}
        counts[part.name] = len(mesh.polygons)
        assert all(len(p.vertices) == 4 for p in mesh.polygons)
        assert mesh.uv_layers.active is not None
        minimum_area = min(minimum_area,min(p.area for p in mesh.polygons))
        for polygon in mesh.polygons:
            for first,second in polygon.edge_keys:
                faces[tuple(sorted((int(ids[first]),int(ids[second]))))] += 1
        for edge, tag in zip(mesh.edges,tag_ids,strict=True):
            if tag:
                rings[int(tag)].add(tuple(sorted(int(ids[i]) for i in edge.vertices)))
        joint_name = [g.name for g in part.vertex_groups]
        for v, global_index in zip(mesh.vertices,ids,strict=True):
            w = {joint_name[g.group]:g.weight for g in v.groups}
            influences = max(influences,len(w))
            assert 0 < len(w) <= 8 and abs(sum(w.values())-1) < 1e-6
            shapes = {name:tuple(keys[name].data[v.index].co-v.co) for name in names}
            global_index = int(global_index)
            if global_index in seam:
                mirrored_vertices += 1
                old_co, old_weights, old_shapes = seam[global_index]
                assert tuple(v.co) == old_co and w == old_weights, (part.name,global_index)
                for feature in shapes.keys() | old_shapes.keys():
                    a = shapes.get(feature,(0.,0.,0.))
                    b = old_shapes.get(feature,(0.,0.,0.))
                    assert max(abs(x-y) for x,y in zip(a,b)) < 1.1e-6, (part.name,global_index,feature)
            seam[global_index] = tuple(v.co), w, shapes
        jaw = part.vertex_groups.get("LowerJaw")
        per_part[part.name] = {"vertices":len(mesh.vertices), "quads":len(mesh.polygons), "shapeTargets":sorted(names),
                              "verticesAffectedByLowerJaw":sum(1 for v in mesh.vertices
                                  if jaw and any(g.group == jaw.index for g in v.groups))}
    assert sum(counts.values()) == 30000 and len(counts) == 25
    assert set(faces.values()) <= {1,2}, Counter(faces.values())
    boundary = [edge for edge,count in faces.items() if count == 1]
    assert all(seam[i][0][1] < -1.45 for edge in boundary for i in edge), boundary
    assert set(rings) == set(range(1,55))
    used_edges = set()
    band_check = []
    for row in build["ringLabels"]:
        edges = rings[row["tag"]]
        assert not used_edges.intersection(edges)
        used_edges.update(edges)
        neighbors = defaultdict(set)
        for a,b in edges:
            neighbors[a].add(b)
            neighbors[b].add(a)
        assert all(len(row) == 2 for row in neighbors.values())
        queue = [next(iter(neighbors))]
        visited = set(queue)
        while queue:
            for nxt in neighbors[queue.pop()] - visited:
                queue.append(nxt)
                visited.add(nxt)
        assert len(visited) == len(neighbors)
        band_check.append(dict(row, edgesInSavedQuads=len(edges), closed=True))
    report = {"quads":sum(counts.values()),"renderParts":per_part,"uniqueCageVertices":len(seam),
              "additionalPartVerticesWithIdenticalSkin":mirrored_vertices,"markedRingEdges":len(used_edges),
              "maxInfluences":influences,"minimumQuadAreaBlenderSquared":minimum_area,
              "nonManifoldCageEdges":0,"openLipBoundaryEdges":len(boundary),"closedRings":band_check}
    print("REOPEN", report["quads"], len(parts), len(seam), len(used_edges), influences, minimum_area, flush=True)
    return report


def measure_turns(rig, parts, min_area, scale):
    scene = bpy.context.scene
    rests = {}
    for obj in parts:
        mesh = obj.data
        mesh.calc_loop_triangles()
        faces = np.array([list(t.vertices) for t in mesh.loop_triangles],dtype=np.int32)
        verts = coordinates(mesh)[faces]
        rest_area = .5 * scale**2 * np.linalg.norm(np.cross(verts[:,1]-verts[:,0],verts[:,2]-verts[:,0]),axis=1)
        valid = rest_area >= min_area
        rests[obj.name] = faces[valid],rest_area[valid],verts[valid].mean(axis=1),int((~valid).sum())
    results = {}
    for name in ("TurnLeft","TurnRight"):
        action = bpy.data.actions[name+"_Controls"]
        rig.animation_data.action = action
        rig.animation_data.action_slot = action.slots[0]
        values = []
        metrics = {obj.name: {"over2":0,"under025":0,"max":0,"largestRestAreaOver2SquareM":0}
                   for obj in parts}
        worst = []
        for i in range(1,85):
            scene.frame_set(i)
            graph = bpy.context.evaluated_depsgraph_get()
            for obj in parts:
                faces,rest_area,centers,_ = rests[obj.name]
                vertices = coordinates(obj.evaluated_get(graph).data)[faces]
                if not len(vertices):
                    continue
                if not np.isfinite(vertices).all():
                    raise RuntimeError(f"Invalid pose {name} {i} {obj.name}")
                area = .5*scale**2*np.linalg.norm(np.cross(vertices[:,1]-vertices[:,0],vertices[:,2]-vertices[:,0]),axis=1)
                ratio = area/rest_area
                over = ratio > 2.
                under = ratio < .25
                values.append(ratio)
                row = metrics[obj.name]
                row["over2"] += int(over.sum())
                row["under025"] += int(under.sum())
                if over.any():
                    row["largestRestAreaOver2SquareM"] = max(row["largestRestAreaOver2SquareM"],float(rest_area[over].max()))
                maximum = float(ratio.max())
                if maximum > row["max"]:
                    row["max"] = maximum
                    idx = int(ratio.argmax())
                    worst.append({"ratio":maximum,"part":obj.name,"frame":i,"areaSquareM":float(rest_area[idx]),
                                  "centerBlender":centers[idx].tolist()})
        ratios = np.concatenate(values)
        result = {"frames":84,"sampled":len(ratios),"restTrianglesBelowCutoff":sum(row[3] for row in rests.values()),
                  "max":float(ratios.max()),"percentiles":{str(p):float(np.percentile(ratios,p)) for p in (1,50,95,99,99.9,99.99)},
                  "over2":int((ratios>2).sum()),"under025":int((ratios<.25).sum()),
                  "partMetrics":metrics,"worst":sorted(worst,key=lambda d:d["ratio"],reverse=True)[:10]}
        results[name] = result
        print(name,{k:result[k] for k in ("sampled","restTrianglesBelowCutoff","over2","under025","max","percentiles")}, flush=True)
        print("worst",result["worst"][:4],flush=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--build",type=Path)
    parser.add_argument("--area",type=float,default=1e-7)
    opt = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    rig = bpy.data.objects["SmartRigArmature"]
    rig.animation_data.use_nla = False
    parts = sorted([o for o in bpy.context.scene.objects if o.type == "MESH" and o.parent == rig],
                   key=lambda o:o.name)
    report = {"restAreaCutoffSquareM":opt.area,"scale":1.92,"deformBones":sum(b.use_deform for b in rig.data.bones),
              "controllerBones":sum(not b.use_deform for b in rig.data.bones),
              "actions":sorted(a.name for a in bpy.data.actions if a.name.endswith("_Controls"))}
    if opt.build:
        report["topology"] = check_topology(parts, json.loads(opt.build.read_text(encoding="utf-8-sig")))
    report["turns"] = measure_turns(rig,parts,opt.area,1.92)
    opt.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    main()
