"""Repair the cheek and throat UV projection while keeping the original atlas outside the patch."""
import argparse
import heapq
import json
from collections import defaultdict
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_fbx import pixels
from refine_stegosaur import smooth

SIDE = 2048
GUTTER = 18


def unit(vectors):
    return vectors / np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-15)


def sampling(image, uv):
    height, width = image.shape[:2]
    locations = uv * [width, height] - .5
    base = np.floor(locations).astype(np.int32)
    t = (locations - base).astype(np.float32)
    x0, y0 = np.clip(base, (0, 0), (width-1, height-1)).T
    x1, y1 = np.clip(base+1, (0, 0), (width-1, height-1)).T
    u, v = t.T[:, :, None]
    return (1-v)*((1-u)*image[y0,x0] + u*image[y0,x1]) + v*((1-u)*image[y1,x0] + u*image[y1,x1])


def basis(points, uv, normals):
    v1, v2 = points[:,1] - points[:,0], points[:,2] - points[:,0]
    uv1, uv2 = uv[:,1] - uv[:,0], uv[:,2] - uv[:,0]
    sign = np.where(uv1[:,0]*uv2[:,1] - uv1[:,1]*uv2[:,0] < 0., -1., 1.)[:,None]
    tangent = sign * (uv2[:,1,None]*v1 - uv1[:,1,None]*v2)
    tangent = unit(tangent - (tangent*normals).sum(axis=1)[:,None] * normals)
    return tangent, sign * np.cross(normals, tangent)


def tangent_detail(texels, t0, b0, t1, b1, strength):
    detail = 2. * texels[:,:2] - 1.
    perturbation = t0 * detail[:,0,None] + b0 * detail[:,1,None]
    return strength[:,None] * np.stack(((perturbation*t1).sum(axis=1), (perturbation*b1).sum(axis=1)), axis=1)


def triangle_arrays(mesh):
    mesh.calc_loop_triangles()
    return (np.array([v.co[:] for v in mesh.vertices], dtype=np.float64),
            np.array([tri.vertices[:] for tri in mesh.loop_triangles], dtype=np.int32),
            np.array([tri.loops[:] for tri in mesh.loop_triangles], dtype=np.int32))


def rasterize(triangles):
    uv_points = triangles * SIDE - .5
    owner = np.full((SIDE, SIDE), -1, dtype=np.int32)
    bary = np.zeros((SIDE, SIDE, 2), dtype=np.float32)
    for index, (a,b,c) in enumerate(uv_points):
        points = uv_points[index]
        x0,y0 = np.maximum(np.ceil(points.min(axis=0)).astype(int), 0)
        x1,y1 = np.minimum(np.floor(points.max(axis=0)).astype(int), SIDE-1)
        if x0>x1 or y0>y1:
            continue
        e1,e2 = b-a,c-a
        den = e1[0]*e2[1]-e1[1]*e2[0]
        if abs(den)<1e-10:
            continue
        dx = (np.arange(x0,x1+1)-a[0])[None,:]
        dy = (np.arange(y0,y1+1)-a[1])[:,None]
        u = (dx*e2[1]-dy*e2[0])/den
        v = (e1[0]*dy-e1[1]*dx)/den
        inside = (u>=-1e-7) & (v>=-1e-7) & (u+v<=1.+1e-7)
        owner[y0:y1+1,x0:x1+1][inside] = index
        bary[y0:y1+1,x0:x1+1][inside] = np.stack((u,v), axis=-1)[inside]
    return owner, bary


def atlas_image(name, left, right, path, data):
    values = np.concatenate((left, right), axis=1)
    image = bpy.data.images.new(name, width=2*SIDE, height=SIDE, alpha=True, is_data=data)
    image.colorspace_settings.name = "Non-Color" if data else "sRGB"
    image.alpha_mode = "STRAIGHT"
    image.pixels.foreach_set(values.astype(np.float32, copy=False).ravel())
    image.filepath_raw = str(path.resolve())
    image.file_format = "PNG"
    image.save()
    image.pack()
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original-surface", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--maps", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    opt = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    opt.maps.mkdir(parents=True, exist_ok=True)
    material = bpy.data.materials["BakedMaterial"]
    nodes = material.node_tree.nodes
    atlas_nodes = [nodes[name] for name in ("Image Texture","Image Texture.002","Image Texture.001")]
    source_images = [pixels(node.image) for node in atlas_nodes]
    with bpy.data.libraries.load(str(opt.original_surface.resolve())) as (available, loaded):
        if "Mesh0" not in available.objects:
            raise ValueError("Original Meshy surface Mesh0 is missing")
        loaded.objects = ["Mesh0"]
    source = loaded.objects[0]
    old_points, old_tris, old_loops = triangle_arrays(source.data)
    source_coords = old_points[old_tris]
    old_uv = np.array([item.uv[:] for item in source.data.uv_layers.active.data])[old_loops]
    old_normals = np.array([item.vector[:] for item in source.data.corner_normals])[old_loops]
    bvh = BVHTree.FromPolygons(old_points.tolist(), old_tris.tolist(), all_triangles=True)

    points, ids, faces, lookup, previous = [], {}, [], [], []
    outside_uv = {}
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH" or not obj.data.materials or obj.data.materials[0] != material:
            continue
        mesh = obj.data
        cage = mesh.attributes["CageVertex"].data
        uv = np.array([item.uv[:] for item in mesh.uv_layers.active.data])
        outside_uv[obj.name] = uv
        for polygon in mesh.polygons:
            center = np.array([mesh.vertices[i].co[:] for i in polygon.vertices]).mean(axis=0)
            x, y, z = center
            if not (-1.667 < y < -.745 and .418 < z < .869 and abs(x) < .298):
                continue
            face = []
            for index in polygon.vertices:
                label = cage[index].value
                if label not in ids:
                    ids[label] = len(points)
                    points.append(mesh.vertices[index].co[:])
                face.append(ids[label])
            faces.append(face)
            lookup.append((obj.name, polygon.loop_indices[:]))
            previous.extend(uv[list(polygon.loop_indices)])
    points = np.asarray(points)
    previous = np.asarray(previous)
    patch_mesh = bpy.data.meshes.new("Temporary cheek and throat unwrap")
    patch_mesh.from_pydata(points.tolist(), [], faces)
    patch_mesh.update()
    patch_obj = bpy.data.objects.new("Temporary cheek and throat unwrap", patch_mesh)
    bpy.context.scene.collection.objects.link(patch_obj)
    bpy.ops.object.select_all(action="DESELECT")
    patch_obj.select_set(True)
    bpy.context.view_layer.objects.active = patch_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, margin_method="FRACTION", island_margin=.014, area_weight=.25)
    bpy.ops.object.mode_set(mode="OBJECT")
    _, patch_tris, patch_loops = triangle_arrays(patch_mesh)
    polygon_uv = np.array([item.uv[:] for item in patch_mesh.uv_layers.active.data])
    polygon_uv = polygon_uv*(1-2*GUTTER/SIDE)+GUTTER/SIDE
    old_target_uv = previous[patch_loops]
    target_uv = polygon_uv[patch_loops]
    target_coords = points[patch_tris]

    # Feather the alternate projection by distance to the actual patch boundary.
    uses = defaultdict(int)
    for face in faces:
        for a,b in zip(face, face[1:] + face[:1]):
            uses[tuple(sorted((a,b)))] += 1
    adjacency = [[] for _ in points]
    distance = np.full(len(points), np.inf)
    for (a,b),count in uses.items():
        length = float(np.linalg.norm(points[a]-points[b]))
        adjacency[a].append((b,length))
        adjacency[b].append((a,length))
        if count == 1:
            distance[a] = distance[b] = 0.
    queue = [(0.,int(i)) for i in np.flatnonzero(distance == 0.)]
    heapq.heapify(queue)
    while queue:
        d, a = heapq.heappop(queue)
        if d != distance[a]:
            continue
        for b, length in adjacency[a]:
            if d + length < distance[b]:
                distance[b] = d + length
                heapq.heappush(queue, (d+length,b))
    weights = smooth(.0001, .052, distance)[patch_tris]
    normals = []
    for name, loops in lookup:
        mesh = bpy.data.objects[name].data
        normals.extend(mesh.corner_normals[i].vector[:] for i in loops)
    target_normals = np.asarray(normals)[patch_loops]

    owner, interpolation = rasterize(target_uv)
    hits = np.flatnonzero(owner.ravel()>=0)
    maps = [np.zeros((SIDE,SIDE,4),dtype=np.float32) for _ in atlas_nodes]
    projection_distances = []
    print(f"UV patch: {len(faces)} quads, {len(hits)} populated texels", flush=True)
    for first in range(0, len(hits), 32768):
        ids = hits[first:first+32768]
        tri = owner.ravel()[ids]
        u,v = interpolation.reshape((-1,2))[ids].T
        bary = np.stack((1.-u-v,u,v), axis=1)
        coord = (bary[:,:,None]*target_coords[tri]).sum(axis=1)
        normal = unit((bary[:,:,None]*target_normals[tri]).sum(axis=1))
        weight = (weights[tri]*bary).sum(axis=1)
        blend = weight.copy()
        # Stay near the original color of the two cut lip edges at the front of the beak.
        lip = np.interp(-coord[:,1], [1.515,1.545,1.560,1.585,1.615,1.645,1.672,1.699],
                       [.781,.774,.758,.746,.735,.727,.721,.722])
        blend *= 1.-.82*smooth(1.535,1.625,-coord[:,1])*(1.-smooth(.007,.036,np.abs(coord[:,2]-lip)))
        legacy_uv = (bary[:,:,None]*old_target_uv[tri]).sum(axis=1)
        source_uv = legacy_uv.copy()
        good = np.flatnonzero(blend>.001)
        chosen = []
        locations = []
        for i in good:
            location, direction, triangle, separation = bvh.find_nearest(coord[i])
            if direction.dot(normal[i]) < .38:
                options = bvh.find_nearest_range(coord[i], min(.05,separation+.018))
                if options:
                    location,direction,triangle,separation = min(
                        options,key=lambda hit:hit[3]+.013*(1.-hit[1].dot(normal[i]))**2)
            chosen.append(triangle)
            locations.append(location[:])
            projection_distances.append(separation)
        chosen = np.array(chosen,dtype=np.int32)
        old_basis = old_normals[chosen].mean(axis=1)
        ref_t,ref_b = basis(old_points[old_tris[chosen]],old_uv[chosen],unit(old_basis))
        a,b,c = source_coords[chosen].transpose((1,0,2))
        e1,e2,p = b-a,c-a,np.asarray(locations).reshape((-1,3))-a
        d00,d01,d11 = [(p1*p2).sum(axis=1) for p1,p2 in ((e1,e1),(e1,e2),(e2,e2))]
        d20,d21 = (p*e1).sum(axis=1),(p*e2).sum(axis=1)
        den = np.maximum(d00*d11-d01*d01,1e-22)
        v,w = (d11*d20-d01*d21)/den,(d00*d21-d01*d20)/den
        q = np.maximum(np.stack((1.-v-w,v,w),axis=1),0.)
        source_uv[good] = ((q/q.sum(axis=1)[:,None])[:,:,None]*old_uv[chosen]).sum(axis=1)

        for i in (0,2):
            first_color, next_color = sampling(source_images[i],legacy_uv),sampling(source_images[i],source_uv)
            color = first_color + blend[:,None]*(next_color-first_color)
            maps[i].reshape((-1,4))[ids] = color
        dst_t,dst_b = basis(target_coords[tri],target_uv[tri],normal)
        legacy_t,legacy_b = basis(target_coords[tri],old_target_uv[tri],normal)
        legacy = tangent_detail(sampling(source_images[1],legacy_uv),legacy_t,legacy_b,dst_t,dst_b,1.-weight*.85)
        transferred = tangent_detail(sampling(source_images[1],source_uv[good]),ref_t,ref_b,dst_t[good],dst_b[good],weight[good]*.28)
        legacy[good] += transferred
        blue = np.sqrt(np.maximum(0.,1.-(legacy**2).sum(axis=1)))
        normal_color = np.column_stack((.5+.5*legacy,.5+.5*blue,np.ones(len(ids))))
        maps[1].reshape((-1,4))[ids] = np.clip(normal_color,0.,1.)
        if first % (32768*12) == 0:
            print(f"Projected {min(first+len(ids),len(hits))}/{len(hits)}",flush=True)

    # Extend island edges in the atlas so mipmap filtering never sees blank neighboring pixels.
    addresses = np.arange(SIDE*SIDE,dtype=np.int32).reshape((SIDE,SIDE))
    donors = np.where(owner>=0,addresses,-1)
    for _ in range(13):
        prior = donors.copy()
        for a,b in ((np.s_[1:,:],np.s_[:-1,:]),(np.s_[:-1,:],np.s_[1:,:]),
                    (np.s_[:,1:],np.s_[:,:-1]),(np.s_[:,:-1],np.s_[:,1:])):
            target = donors[a]
            take = (target<0)&(prior[b]>=0)
            target[take] = prior[b][take]
    extra = (owner<0)&(donors>=0)
    for array in maps:
        array[extra] = array.reshape((-1,4))[donors[extra]]

    for name, uv in outside_uv.items():
        uv[:,0] *= .5
    for patch_face, (name, indices) in zip(patch_mesh.polygons,lookup,strict=True):
        uv = polygon_uv[list(patch_face.loop_indices)].copy()
        uv[:,0] = .5 + uv[:,0]*.5
        outside_uv[name][list(indices)] = uv
    for name, uv in outside_uv.items():
        bpy.data.objects[name].data.uv_layers.active.data.foreach_set("uv", uv.ravel())
    bpy.data.objects.remove(patch_obj,do_unlink=True)
    bpy.data.meshes.remove(patch_mesh)
    bpy.data.objects.remove(source,do_unlink=True)
    for i, name in enumerate(("Albedo", "Normal", "MetallicRoughness")):
        original = source_images[i]
        if original.shape[:2] == (2*SIDE,2*SIDE):
            original = original.reshape((SIDE,2,SIDE,2,4)).mean(axis=(1,3))
        atlas_nodes[i].image = atlas_image(f"Refined_{name}",original,maps[i],opt.maps/(name+".png"),i!=0)
        if i == 2:
            converted = []
            for half in (original,maps[i]):
                pixel = np.empty_like(half)
                pixel[:,:,:3] = half[:,:,2,None]
                pixel[:,:,3] = 1. - half[:,:,1]
                converted.append(pixel)
            unity_image = atlas_image("UnityMetallicGloss",*converted,opt.maps/"MetallicGloss.png",True)
            bpy.data.images.remove(unity_image)
    bpy.data.objects["SmartRigArmature"]["RevisionUV"] = "Local seam repair from original closed Mesh0 with feathered PBR atlas"
    bpy.ops.file.pack_all()
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opt.output.resolve()),compress=True)
    report = dict(quadsInUvPatch=len(faces), atlasSize=[2*SIDE,SIDE], filledTexels=len(hits),
                  sourceProjectedTexels=len(projection_distances), mipPaddingTexels=int(extra.sum()),
                  projectionDistanceBlenderUnitsP50P95Max=np.percentile(projection_distances,[50,95,100]).tolist(),
                  channels=["Base Color (sRGB)","Tangent Normal","Metallic Roughness","Unity Metallic Gloss"])
    opt.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(report,flush=True)


if __name__ == "__main__":
    main()
