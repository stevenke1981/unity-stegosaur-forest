"""Make a detail-preserving polygon seed from the original partitioned surface."""
import argparse
import bmesh
import bpy
from collections import Counter, defaultdict
from itertools import product
import math
from pathlib import Path
import sys


def lip_guide(point, jaw_weight):
    # The source has coincident lip points which follow different bones. A
    # small guide opening retains this distinction during decimation and
    # while finding source texture and skin correspondences.
    return point[0], point[1], point[2] - .026 * jaw_weight


def corner_surface(parts):
    vertices, by_position, triangles, triangle_owners, triangle_normals, halfedges = [], {}, [], [], [], defaultdict(list)
    faces_seen = set()
    for owner, part in enumerate(parts):
        mesh = part.data
        mesh.calc_loop_triangles()
        positions = []
        jaw = part.vertex_groups.get("LowerJaw")
        for v in mesh.vertices:
            influence = next((g.weight for g in v.groups if jaw and g.group == jaw.index),0.)
            position = lip_guide(v.co,influence)
            key = tuple(round(x,6) for x in position)
            idx = by_position.get(key)
            if idx is None:
                idx = len(vertices)
                by_position[key] = idx
                vertices.append(position)
            positions.append(idx)
        for t in mesh.loop_triangles:
            corners = [positions[i] for i in t.vertices]
            key = tuple(sorted(corners))
            if len(set(corners)) < 3 or key in faces_seen:
                continue
            faces_seen.add(key)
            i = len(triangles)
            triangles.append(corners)
            triangle_owners.append(owner)
            triangle_normals.append(t.normal.copy())
            for a,b in zip(range(3),(1,2,0),strict=True):
                v1,v2 = corners[a],corners[b]
                halfedges[tuple(sorted((v1,v2)))].append((i,a,b,v1 < v2))
    parent = list(range(len(triangles)*3))
    def root(i):
        while i != parent[i]:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    pair_stats = Counter()
    for edges in halfedges.values():
        left = [e for e in edges if e[3]]
        right = [e for e in edges if not e[3]]
        # At coincident lips and detail shells, avoid welding non-neighboring
        # faces together. Match the most coplanar, consistently oriented pair.
        choices = sorted(((triangle_normals[a[0]].dot(triangle_normals[b[0]])
                           + .075*(triangle_owners[a[0]]==triangle_owners[b[0]]),a,b)
                          for a,b in product(left,right)),reverse=True)
        consumed = set()
        for _,a,b in choices:
            if a in consumed or b in consumed:
                continue
            consumed.update((a,b))
            i,j = a[0]*3,b[0]*3
            parent[root(i+a[1])] = root(j+b[2])
            parent[root(i+a[2])] = root(j+b[1])
        pair_stats[(len(edges),len(edges)-len(consumed))] += 1
    point_ids, points, out = {},[],[]
    for i,corners in enumerate(triangles):
        face = []
        for j,v in enumerate(corners):
            r = root(3*i+j)
            if r not in point_ids:
                point_ids[r] = len(points)
                points.append(vertices[v])
            face.append(point_ids[r])
        out.append(face)
    print("Source oriented half-edge pairing",len(points),len(out),dict(pair_stats),flush=True)
    return points,out,triangle_owners


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=int, nargs="+", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--stem", default="detail-seed")
    opt = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    for target in opt.targets:
        bpy.ops.wm.open_mainfile(filepath=str(opt.source.resolve()))
        source = sorted([o for o in bpy.context.scene.objects if o.type=="MESH" and o.find_armature() and o.name!="Tongue"],
                        key=lambda o:o.name)
        vertices,faces,owners = corner_surface(source)
        mesh = bpy.data.meshes.new("Seam-matched source")
        mesh.from_pydata(vertices,[],faces)
        for obj in source:
            mesh.materials.append(bpy.data.materials.new("part-marker-" + obj.name))
        for face,i in zip(mesh.polygons,owners,strict=True):
            face.material_index = i
        mesh.validate()
        obj = bpy.data.objects.new("RetopologySeed",mesh)
        bpy.context.collection.objects.link(obj)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        mod = obj.modifiers.new("Error-based collapse","DECIMATE")
        mod.ratio = target/len(mesh.polygons)
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        before = Counter(len(e.link_faces) for e in bm.edges)
        # Close only the small residual source gaps identified after oriented
        # pairing. The upper lip and lower jaw remain separate surface sheets.
        splinters = {min(e.link_faces,
                         key=lambda f:(-sum(x.is_boundary for x in f.edges),f.calc_area()))
                     for e in bm.edges if len(e.link_faces) > 2}
        if splinters:
            bmesh.ops.delete(bm, geom=list(splinters), context="FACES")
        filled = bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=22)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        repaired = Counter(len(e.link_faces) for e in bm.edges)
        if set(repaired) != {2}:
            for edge in bm.edges:
                if len(edge.link_faces) != 2:
                    print("Remaining edge",len(edge.link_faces),[tuple(v.co) for v in edge.verts],flush=True)
            if set(repaired) - {1,2} or repaired[1]>16:
                raise RuntimeError(f"Residual source seam edges: {repaired}")
        bmesh.ops.join_triangles(bm, faces=list(bm.faces), cmp_materials=True,
            angle_face_threshold=math.radians(38), angle_shape_threshold=math.radians(66))
        topology = Counter(len(f.verts) for f in bm.faces)
        print("Seed",target,"source parts",len(source),"pre-join edge incidence",before,"small holes",len(filled["faces"]),"polygons",topology,
              "initial subdivided",sum(k*v for k,v in topology.items()),flush=True)
        bm.to_mesh(obj.data)
        bm.free()
        for material in list(obj.data.materials):
            if material and material.name.startswith("part-marker-"):
                bpy.data.materials.remove(material)
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=str((opt.output_dir/f"{opt.stem}-{target}.blend").resolve()), compress=True)


if __name__ == "__main__":
    main()
