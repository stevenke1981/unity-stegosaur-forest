"""Prepare separately editable Unity FBX assets from the previous forest project.

Usage:
  blender -b -t 4 --python-exit-code 1 --python Tools/prepare_unity_assets.py -- --source <forest-project> --sky <HDR>
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys

import bpy
from mathutils import Vector
import numpy as np


SCENE_IDS = {
    "tree_small_02": {"tree_small_02_branches": "Branches", "tree_small_02_leaves": "Foliage", "tree_small_02_trunk": "Trunk"},
    "pine_sapling_small": {"pine_sapling_small_bark": "Trunk", "pine_sapling_small_twig": "Needles"},
    "tree_stump_01": {},
    "shrub_02_a": {},
    "shrub_02_b": {},
    "shrub_03_a": {},
    "shrub_03_b": {},
    "shrub_sorrel_01": {},
}
ACTIONS = ("Walk", "Run", "HeadShake", "Roar_Pose")
WEIGHT_PARTS = {
    "HeadNeck": (2, 22, 23, 24),
    "FrontLeftLeg": (30, 31, 32, 33, 34),
    "FrontRightLeg": (25, 26, 27, 28, 29),
    "HindLeftLeg": (5, 6, 7, 8, 9, 10),
    "HindRightLeg": (11, 12, 13, 14, 15, 16),
    "Tail": (17, 18, 19, 20, 21),
    "Body": (0, 1, 3, 4),
}
UNITY_ROOT = Path(__file__).resolve().parent.parent
OUT = UNITY_ROOT / "Assets" / "Source"


def glb_file(path):
    with path.open("rb") as stream:
        magic, version, total, json_length, json_tag = struct.unpack("<4sIII4s", stream.read(20))
        if (magic, version, total, json_tag) != (b"glTF", 2, path.stat().st_size, b"JSON"):
            raise ValueError(f"Invalid GLB header in {path.name}")
        content = json.loads(stream.read(json_length))
        binary_length, binary_tag = struct.unpack("<I4s", stream.read(8))
        if binary_tag != b"BIN\0":
            raise ValueError(f"No embedded texture buffer in {path.name}")
        return content, stream.read(binary_length)


def pixels(image):
    width, height = image.size[:]
    if width < 1 or height < 1:
        raise ValueError(f"Could not load {image.name}")
    values = np.empty((height, width, 4), dtype=np.float32)
    image.pixels.foreach_get(values.ravel())
    return values


def metallic_gloss(image, path, metallic_factor, roughness_factor, from_gltf):
    source = pixels(image)
    # Resample data channels by area when the source uses an integer multiple of 1K.
    height, width = source.shape[:2]
    step = max(1, math.ceil(max(width, height) / 1024))
    if step > 1:
        h = height // step
        w = width // step
        source = source[:h * step, :w * step].reshape(h, step, w, step, 4).mean(axis=(1, 3))
    rgba = np.empty_like(source)
    rgba[..., :3] = source[..., 2, None] * metallic_factor if from_gltf else metallic_factor
    rgba[..., 3] = 1.0 - source[..., 1 if from_gltf else 0] * roughness_factor
    np.clip(rgba, 0.0, 1.0, out=rgba)

    h, w = rgba.shape[:2]
    target = bpy.data.images.new(path.stem, width=w, height=h, alpha=True, float_buffer=False, is_data=True)
    target.colorspace_settings.name = "Non-Color"
    target.alpha_mode = "STRAIGHT"
    target.pixels.foreach_set(rgba.ravel())
    target.filepath_raw = str(path)
    target.file_format = "PNG"
    target.save()
    bpy.data.images.remove(target)

    check = bpy.data.images.load(str(path), check_existing=False)
    check.colorspace_settings.name = "Non-Color"
    actual = pixels(check)
    bpy.data.images.remove(check)
    if float(np.max(np.abs(actual - rgba))) > 1.1 / 255:
        raise ValueError(f"PNG changed non-color R/A values in {path.name}")


def export_materials(gltf, binary, already_written):
    result = []
    for material in gltf["materials"]:
        slot = material["name"]
        maps = material["pbrMetallicRoughness"]
        directory = OUT / "Textures" / slot
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "slot": slot,
            "albedo": "",
            "normal": "",
            "packedMetallicGloss": "",
            "cutout": material.get("alphaMode") in ("BLEND", "MASK"),
            "cutoff": material.get("alphaCutoff", 0.45),
            "doubleSided": material.get("doubleSided", False),
            "normalScale": material.get("normalTexture", {}).get("scale", 1.0),
        }
        for key, item, label in (
            ("albedo", maps.get("baseColorTexture"), "Albedo"),
            ("normal", material.get("normalTexture"), "Normal"),
            ("packedMetallicGloss", maps.get("metallicRoughnessTexture"), "MetallicGloss"),
        ):
            if item is None:
                continue
            info = gltf["images"][gltf["textures"][item["index"]]["source"]]
            view = gltf["bufferViews"][info["bufferView"]]
            offset = view.get("byteOffset", 0)
            embedded = binary[offset:offset + view["byteLength"]]
            factor = (maps.get("metallicFactor", 1.0), maps.get("roughnessFactor", 1.0))
            ident = (slot, key)
            digest = hashlib.sha256(embedded).hexdigest()
            ext = ".png" if key == "packedMetallicGloss" or info["mimeType"] == "image/png" else ".jpg"
            path = directory / (label + ext)
            if ident in already_written:
                if already_written[ident] != (digest, factor):
                    raise ValueError(f"Conflicting texture data for shared material {slot}")
            elif key == "packedMetallicGloss":
                source = bpy.data.images[info["name"]]
                source.colorspace_settings.name = "Non-Color"
                metallic_gloss(source, path, *factor, from_gltf=True)
            else:
                path.write_bytes(embedded)
            already_written[ident] = (digest, factor)
            record[key] = path.relative_to(UNITY_ROOT).as_posix()
        result.append(record)
    return result


def partition_mesh(source, indices, name):
    original = source.data
    faces = [original.polygons[int(i)] for i in indices]
    vertex_indices = sorted({v for face in faces for v in face.vertices})
    new_indices = {old: new for new, old in enumerate(vertex_indices)}
    part_data = bpy.data.meshes.new(name)
    part_data.from_pydata(
        [original.vertices[index].co for index in vertex_indices],
        [],
        [[new_indices[i] for i in face.vertices] for face in faces],
    )
    part_data.update()
    part = source.copy()
    part.data = part_data
    part.name = name
    bpy.context.scene.collection.objects.link(part)

    for material in original.materials:
        part_data.materials.append(material)
    for face, target in zip(faces, part_data.polygons, strict=True):
        target.material_index = face.material_index
        target.use_smooth = face.use_smooth

    loops = [loop for face in faces for loop in face.loop_indices]
    for layer in original.uv_layers:
        coords = np.empty((len(original.loops), 2), np.float32)
        layer.data.foreach_get("uv", coords.ravel())
        new_layer = part_data.uv_layers.new(name=layer.name)
        new_layer.data.foreach_set("uv", coords[loops].ravel())

    # Match the original shading, including at boundaries shared by separate meshes.
    if original.has_custom_normals:
        original_sharp = {tuple(sorted(edge.vertices)) for edge in original.edges if edge.use_edge_sharp}
        for edge in part_data.edges:
            a, b = edge.vertices
            edge.use_edge_sharp = (vertex_indices[a], vertex_indices[b]) in original_sharp or (vertex_indices[b], vertex_indices[a]) in original_sharp
        normals = [original.corner_normals[index].vector[:] for index in loops]
        part_data.normals_split_custom_set(normals)
        rebuilt = np.empty((len(loops), 3), dtype=np.float32)
        part_data.corner_normals.foreach_get("vector", rebuilt.ravel())
        min_normal_dot = float(np.min(np.einsum("ij,ij->i", rebuilt, normals)))
        if min_normal_dot < 0.999:
            raise ValueError(f"Corner normals changed in {name} (min cosine {min_normal_dot})")
    else:
        min_normal_dot = None

    for group in source.vertex_groups:
        part.vertex_groups.new(name=group.name)
    for new_index, old_index in enumerate(vertex_indices):
        weights = [(g.group, g.weight) for g in original.vertices[old_index].groups]
        for group, weight in weights:
            part.vertex_groups[group].add([new_index], weight, "REPLACE")
        copied = [(g.group, g.weight) for g in part_data.vertices[new_index].groups]
        if copied != weights:
            raise ValueError(f"Skin weights changed in {name} at vertex {old_index}")

    return part, vertex_indices, min_normal_dot


def dino_parts(source):
    mesh = source.data
    if any(len(face.vertices) != 3 for face in mesh.polygons):
        raise ValueError("The source dinosaur should be a triangulated skinned mesh")
    positions = np.empty((len(mesh.vertices), 3), np.float32)
    mesh.vertices.foreach_get("co", positions.ravel())
    vertex_indices = np.array([p.vertices[:] for p in mesh.polygons], dtype=np.int32)
    centers = positions[vertex_indices].mean(axis=1)
    x, y, z = centers.T
    weights = np.zeros((len(mesh.vertices), len(source.vertex_groups)), dtype=np.float32)
    for vertex in mesh.vertices:
        for group in vertex.groups:
            weights[vertex.index, group.group] = group.weight
    scores = []
    for name, bones in WEIGHT_PARTS.items():
        group_indices = [source.vertex_groups[f"Bone_{bone:03d}"].index for bone in bones]
        score = weights[:, group_indices].sum(axis=1)[vertex_indices].mean(axis=1)
        if name == "HeadNeck":
            score = np.where(y < -0.70, score, 0)
        if name == "Tail":
            score = np.where(y > 0.29, score, 0)
        scores.append(score)
    base_names = list(WEIGHT_PARTS)
    labels = [base_names[i] for i in np.argmax(scores, axis=0)]

    # The original model bakes copper plates/spikes into a skin texture. Identify
    # the copper surfaces, then use shared mesh edges to separate each structure.
    uv = np.empty((len(mesh.loops), 2), np.float32)
    mesh.uv_layers.active.data.foreach_get("uv", uv.ravel())
    face_uv = uv.reshape((-1, 3, 2)).mean(axis=1)
    rgb = pixels(bpy.data.images["texture_0"])
    image_size = np.array([rgb.shape[1], rgb.shape[0]], dtype=np.int32)
    at = np.clip((face_uv * image_size).astype(np.int32), 0, image_size - 1)
    red, green, blue = rgb[at[:, 1], at[:, 0], :3].T
    copper = (red > green * 1.18 + 0.025) & (green > blue * 0.9) & (z > 0.40)
    welded = [tuple(c) for c in np.rint(positions * 100000).astype(np.int32)]
    edge_faces = defaultdict(list)
    for face, indices in enumerate(vertex_indices):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((welded[indices[a]], welded[indices[b]])))
            edge_faces[edge].append(face)
    neighbors = [[] for _ in labels]
    for faces in edge_faces.values():
        for face in faces:
            neighbors[face].extend(j for j in faces if face != j)

    seen = set()
    plates = []
    for start in np.flatnonzero(copper):
        if start in seen:
            continue
        seen.add(start)
        queue = [start]
        group = []
        while queue:
            current = queue.pop()
            group.append(current)
            for other in neighbors[current]:
                if other not in seen and copper[other]:
                    seen.add(other)
                    queue.append(other)
        if len(group) < 60:
            continue
        center = centers[group].mean(axis=0)
        if center[1] > 1.38:
            row = "Rear" if center[1] > 1.53 else "Front"
            for face in group:
                side = "Left" if x[face] > 0.004 else "Right"
                labels[face] = f"TailSpike_{row}{side}"
        elif center[1] > 1.22:
            for face in group:
                labels[face] = "TailSpike_Center"
        else:
            # Small islands from the same oblique neck plate share a piece.
            for index, existing in enumerate(plates):
                delta = center - centers[existing].mean(axis=0)
                if float(np.linalg.norm(delta)) < 0.045:
                    plates[index].extend(group)
                    break
            else:
                plates.append(group)
    for index, group in enumerate(sorted(plates, key=lambda g: float(y[g].mean())), start=1):
        for face in group:
            labels[face] = f"BackPlate_{index:02}"

    # Extend the color boundary by one face and fill enclosed one-face holes
    # from the original texturing. Boundary vertices still have identical weights.
    extended = list(labels)
    for face, name in enumerate(labels):
        if name not in WEIGHT_PARTS:
            continue
        options = [labels[j] for j in neighbors[face] if labels[j] not in WEIGHT_PARTS]
        if options and len(set(options)) == 1:
            extended[face] = options[0]
    parts = defaultdict(list)
    for face, name in enumerate(extended):
        parts[name].append(face)
    if set(WEIGHT_PARTS) - set(parts) or not plates:
        raise ValueError("Could not identify the major body pieces or the dorsal plates")

    pieces = []
    mapping = {}
    stats = []
    for name, group in sorted(parts.items()):
        obj, source_indices, normal_dot = partition_mesh(source, group, name)
        pieces.append(obj)
        mapping[obj.name] = source_indices
        stats.append({
            "part": obj.name,
            "triangles": len(obj.data.polygons),
            "vertices": len(obj.data.vertices),
            "minNormalDot": normal_dot,
        })
    if sum(row["triangles"] for row in stats) != len(mesh.polygons):
        raise ValueError("The modular character did not preserve the complete mesh")
    return pieces, mapping, stats


def export_fbx(path, objects, animated):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
        if obj.type == "MESH":
            for material in obj.data.materials:
                # Unity binds all PBR maps from the manifest. Export only the
                # slot names so FBX never carries Blender-local image paths.
                material.use_nodes = False
    result = bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        axis_forward="-Z",
        axis_up="Y",
        object_types={"ARMATURE", "MESH"} if animated else {"MESH"},
        use_mesh_modifiers=False,
        mesh_smooth_type="OFF",
        use_tspace=True,
        use_metadata=False,
        add_leaf_bones=False,
        bake_anim=animated,
        bake_anim_use_all_bones=True,
        bake_anim_use_all_actions=animated,
        bake_anim_use_nla_strips=False,
        bake_anim_simplify_factor=0.0,
        path_mode="STRIP",
        embed_textures=False,
    )
    if result != {"FINISHED"} or not path.is_file() or path.stat().st_size >= 100_000_000:
        raise ValueError(f"Invalid or oversized FBX {path.name}")
    if bpy.data.filepath:
        # Blender 4.5 still writes the absolute ApplicationNativeFile string
        # with use_metadata=False. Replace its one FBX string record with a
        # same-length neutral value so node offsets and other data stay intact.
        private_path = bpy.data.filepath.encode("utf-8")
        field_header = b"S" + struct.pack("<I", len(private_path))
        exported = path.read_bytes()
        if exported.count(field_header + private_path) != 1:
            raise ValueError(f"Unexpected Blender source metadata in {path.name}")
        neutral = b" " * len(private_path)
        sanitized = exported.replace(field_header + private_path, field_header + neutral)
        if private_path in sanitized:
            raise ValueError(f"Absolute Blender source path remains in {path.name}")
        path.write_bytes(sanitized)


def verify_pose_deformation(source, rig, pieces, mapping):
    max_error = 0.0
    trials = {"Walk": 13, "Run": 7, "HeadShake": 22, "Roar_Pose": 37}
    for action, frame in trials.items():
        rig.animation_data.action = bpy.data.actions[action]
        rig.animation_data.action_slot = rig.animation_data.action.slots[0]
        bpy.context.scene.frame_set(frame)
        graph = bpy.context.evaluated_depsgraph_get()
        reference = source.evaluated_get(graph).data
        reference_coords = np.empty((len(reference.vertices), 3), np.float32)
        reference.vertices.foreach_get("co", reference_coords.ravel())
        for piece in pieces:
            data = piece.evaluated_get(graph).data
            part_coords = np.empty((len(data.vertices), 3), np.float32)
            data.vertices.foreach_get("co", part_coords.ravel())
            error = float(np.max(np.abs(part_coords - reference_coords[mapping[piece.name]])))
            max_error = max(max_error, error)
    if max_error > 1e-5:
        raise ValueError(f"Modular skinning differs from the original by {max_error} m")
    rig.animation_data.action = None
    bpy.context.scene.frame_set(1)
    return max_error


def prepare_dinosaur(forest, cache):
    folder = forest / "stegosaur_actions"
    gltf, binary = glb_file(folder / "stegosaur_actions.glb")
    bpy.ops.wm.open_mainfile(filepath=str(folder / "stegosaur_actions.blend"))
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.render.fps_base = 1.0
    rig = bpy.data.objects["SmartRigArmature"]
    source = bpy.data.objects["Mesh0"]
    if set(ACTIONS) != {action.name for action in bpy.data.actions}:
        raise ValueError("Unexpected animation actions in the source blend")
    rig.animation_data.use_nla = False
    pieces, mapping, stats = dino_parts(source)
    pose_error = verify_pose_deformation(source, rig, pieces, mapping)
    materials = export_materials(gltf, binary, cache)
    destination = OUT / "Dinosaur" / "Stegosaur.fbx"
    export_fbx(destination, [rig, *pieces], animated=True)
    return {
        "id": "stegosaur",
        "fbx": destination.relative_to(UNITY_ROOT).as_posix(),
        "yawDegrees": 0.0,
        "materials": materials,
    }, {
        "sourceTriangles": len(source.data.polygons),
        "sourceVertices": len(source.data.vertices),
        "sourceBones": len(rig.data.bones),
        "sourceActions": {a.name: round((a.frame_range[1] - a.frame_range[0]) / 24.0, 5) for a in bpy.data.actions},
        "maxPoseCoordinateError": pose_error,
        "parts": stats,
    }


def prepare_vegetation(forest, cache, model_id, separate_parts):
    path = forest / "assets" / f"{model_id}_textured.glb"
    gltf, binary = glb_file(path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    source_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    bpy.context.view_layer.update()
    # The shrub variants contain rotation. A transformed axis-aligned bounding
    # box reaches below their vertices and makes them float over the floor.
    world_vertices = [obj.matrix_world @ v.co for obj in source_objects for v in obj.data.vertices]
    min_xyz = [min(vertex[axis] for vertex in world_vertices) for axis in range(3)]
    max_xyz = [max(vertex[axis] for vertex in world_vertices) for axis in range(3)]
    origin = Vector(((min_xyz[0] + max_xyz[0]) * 0.5, (min_xyz[1] + max_xyz[1]) * 0.5, min_xyz[2]))
    materials = export_materials(gltf, binary, cache)
    transforms = {}
    for material in gltf["materials"]:
        pbr = material["pbrMetallicRoughness"]
        albedo = pbr["baseColorTexture"]
        transform = albedo.get("extensions", {}).get("KHR_texture_transform", {})
        coord = transform.get("texCoord", albedo.get("texCoord", 0))
        if coord or transform:
            if transform.get("rotation", 0):
                raise ValueError(f"Unexpected rotated material UV in {model_id}")
            transforms[material["name"]] = (coord, transform.get("offset", [0, 0]), transform.get("scale", [1, 1]))

    for obj in source_objects:
        mesh = obj.data
        for face in mesh.polygons:
            transform = transforms.get(mesh.materials[face.material_index].name)
            if transform is None:
                continue
            channel, offset, scale = transform
            for loop in face.loop_indices:
                u, v = mesh.uv_layers[channel].data[loop].uv
                mesh.uv_layers[0].data[loop].uv = (
                    u * scale[0] + offset[0],
                    1.0 - ((1.0 - v) * scale[1] + offset[1]),
                )
        if separate_parts and len(mesh.materials) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.separate(type="MATERIAL")
            bpy.ops.object.mode_set(mode="OBJECT")

    parts = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if separate_parts:
        for part in parts:
            used = {poly.material_index for poly in part.data.polygons}
            if len(used) != 1:
                raise ValueError(f"{model_id} separation did not produce single-material pieces")
            material = part.data.materials[used.pop()]
            part.name = separate_parts[material.name]
            part.data.name = part.name
            part.data.materials.clear()
            part.data.materials.append(material)
    for part in parts:
        part.location -= origin
    destination = OUT / "Environment" / f"{model_id}.fbx"
    export_fbx(destination, parts, animated=False)
    return {
        "id": model_id,
        "fbx": destination.relative_to(UNITY_ROOT).as_posix(),
        "materials": materials,
    }, {
        "sourceDimensions": [round(max_xyz[i] - min_xyz[i], 5) for i in (0, 2, 1)],
        "parts": [{"part": part.name, "triangles": len(part.data.polygons)} for part in parts],
    }


def verify_fbx(model, expected):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path = UNITY_ROOT / model["fbx"]
    result = bpy.ops.import_scene.fbx(filepath=str(path), use_anim=model["id"] == "stegosaur")
    if result != {"FINISHED"}:
        raise ValueError(f"Reimport failed for {path.name}")
    scene = bpy.context.scene
    mesh = [obj for obj in scene.objects if obj.type == "MESH"]
    material_slots = sorted({m.name for obj in mesh for m in obj.data.materials})
    expected_slots = sorted(m["slot"] for m in model["materials"])
    if material_slots != expected_slots:
        raise ValueError(f"Material slots do not match in {path.name}: {material_slots} vs {expected_slots}")
    expected_parts = {row["part"]: row["triangles"] for row in expected["parts"]}
    actual_parts = {obj.name: len(obj.data.polygons) for obj in mesh}
    if expected_parts != actual_parts or not all(obj.data.uv_layers for obj in mesh):
        raise ValueError(f"Missing UVs or geometry on FBX round trip: {path.name}")

    actual = {
        "bytes": path.stat().st_size,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
        "meshParts": len(mesh),
        "polygons": sum(actual_parts.values()),
        "materialSlots": material_slots,
    }
    if model["id"] != "stegosaur":
        lowest_vertex = min((obj.matrix_world @ v.co).z for obj in mesh for v in obj.data.vertices)
        if abs(lowest_vertex) > 1e-5:
            raise ValueError(f"{path.name} is not grounded: {lowest_vertex} m")
        actual["lowestVertexMeters"] = round(lowest_vertex, 8)
        return actual

    rig, = [obj for obj in scene.objects if obj.type == "ARMATURE"]
    if len(rig.data.bones) != expected["sourceBones"]:
        raise ValueError("The FBX round trip changed the number of bones")
    for part in mesh:
        skin, = [m for m in part.modifiers if m.type == "ARMATURE"]
        if skin.object != rig or any(not v.groups for v in part.data.vertices):
            raise ValueError(f"{part.name} did not keep the shared armature and vertex weights")

    actual["boneCount"] = len(rig.data.bones)
    actual["clips"] = {}
    head = bpy.data.objects["HeadNeck"]
    tail = bpy.data.objects["Tail"]
    actual["headVsTailBlenderY"] = round(
        sum((head.matrix_world @ v.co).y for v in head.data.vertices) / len(head.data.vertices)
        - sum((tail.matrix_world @ v.co).y for v in tail.data.vertices) / len(tail.data.vertices),
        5,
    )
    rig.animation_data.use_nla = False
    for action in bpy.data.actions:
        name = action.name.split("|")[-1]
        if name not in ACTIONS:
            raise ValueError(f"Unexpected FBX take {action.name}")
        rig.animation_data.action = action
        rig.animation_data.action_slot = action.slots[0]
        first, last = action.frame_range[:]
        previous_pose = None
        motion = 0.0
        for sample in range(9):
            frame = first + (last - first) * sample / 8.0
            scene.frame_set(int(frame), subframe=frame - int(frame))
            pose = np.array([tuple(bone.matrix.to_quaternion()) for bone in rig.pose.bones])
            if previous_pose is not None:
                motion = max(motion, float(np.max(np.minimum(
                    np.linalg.norm(pose - previous_pose, axis=1),
                    np.linalg.norm(pose + previous_pose, axis=1),
                ))))
            previous_pose = pose
        if motion < 1e-3:
            raise ValueError(f"FBX take {action.name} is static")
        actual["clips"][name] = {
            "importedName": action.name,
            "seconds": round((last - first) * scene.render.fps_base / scene.render.fps, 5),
            "maxQuaternionStep": round(motion, 5),
        }
    if set(actual["clips"]) != set(ACTIONS):
        raise ValueError(f"The FBX round trip did not retain all takes: {actual['clips']}")
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sky", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    forest = args.source.resolve(strict=True)
    source_sky = args.sky.resolve(strict=True)

    cache = {}
    dino, dino_details = prepare_dinosaur(forest, cache)
    models = [dino]
    details = {dino["id"]: dino_details}
    for model_id, separate_parts in SCENE_IDS.items():
        model, stats = prepare_vegetation(forest, cache, model_id, separate_parts)
        models.append(model)
        details[model_id] = stats
    for model in models:
        model_id = model["id"]
        details[model_id]["fbxReimport"] = verify_fbx(model, details[model_id])

    terrain = forest / "assets" / "polyhaven" / "leaves_forest_ground"
    ground_dir = OUT / "Ground"
    ground_dir.mkdir(parents=True, exist_ok=True)
    ground = {}
    for key, src, name in (
        ("albedo", "diff", "Albedo.jpg"),
        ("normal", "nor_gl", "Normal.jpg"),
        ("packedMetallicGloss", "rough", "MetallicGloss.png"),
    ):
        path = ground_dir / name
        original = terrain / f"leaves_forest_ground_{src}_2k.jpg"
        if src == "rough":
            image = bpy.data.images.load(str(original), check_existing=False)
            image.colorspace_settings.name = "Non-Color"
            metallic_gloss(image, path, 0.0, 1.0, from_gltf=False)
            bpy.data.images.remove(image)
        else:
            shutil.copyfile(original, path)
        ground[key] = path.relative_to(UNITY_ROOT).as_posix()
    sky = OUT / "Lighting" / "ForestSky.hdr"
    sky.parent.mkdir(parents=True, exist_ok=True)
    if source_sky != sky.resolve():
        shutil.copyfile(source_sky, sky)
    manifest = {"models": models, "ground": ground, "sky": sky.relative_to(UNITY_ROOT).as_posix()}
    (OUT / "asset-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    evidence = UNITY_ROOT / "Evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "asset-check.json").write_text(json.dumps({
        "status": "PASS",
        "blender": bpy.app.version_string,
        "unityImport": "NOT_RUN",
        "sky": {
            "bytes": sky.stat().st_size,
            "sha256": hashlib.file_digest(sky.open("rb"), "sha256").hexdigest(),
        },
        "preExportAndFbxReimport": details,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"PREPARED {len(models)} models, {len(dino_details['parts'])} independently skinned dinosaur mesh parts")


if __name__ == "__main__":
    main()
