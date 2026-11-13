"""Refine the existing quad cage's shared skin weights and roar targets."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np


def smooth(low, high, value):
    fraction = np.clip((value - low) / (high - low), 0., 1.)
    return fraction**2 * (3. - 2. * fraction)


def patch(points, center, inner, outer):
    return 1. - smooth(inner, outer, np.linalg.norm(points - center, axis=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    opt = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    rig = bpy.data.objects["SmartRigArmature"]
    parts = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.parent == rig]
    names = [bone.name for bone in rig.data.bones if bone.use_deform]
    bone = {name: index for index, name in enumerate(names)}
    ids_by_part = {}
    for obj in parts:
        ids = np.empty(len(obj.data.vertices), dtype=np.int32)
        obj.data.attributes["CageVertex"].data.foreach_get("value", ids)
        ids_by_part[obj.name] = ids
    size = max(ids.max() for ids in ids_by_part.values()) + 1
    points = np.zeros((size, 3), dtype=np.float64)
    weights = np.zeros((size, len(bone)), dtype=np.float64)
    exterior = np.zeros(size, dtype=bool)
    support = {name: np.ones(size, dtype=bool) for name in ("Throat", "RoarTension")}
    for obj in parts:
        ids = ids_by_part[obj.name]
        coordinates = np.empty(len(ids) * 3, dtype=np.float64)
        obj.data.vertices.foreach_get("co", coordinates)
        points[ids] = coordinates.reshape((-1, 3))
        groups = [bone[group.name] for group in obj.vertex_groups]
        for vertex, cage_id in zip(obj.data.vertices, ids, strict=True):
            weights[cage_id] = 0.
            for group in vertex.groups:
                weights[cage_id, groups[group.group]] = group.weight
        if obj.data.materials[0].name == "BakedMaterial":
            exterior[ids] = True
        keys = obj.data.shape_keys
        for feature, enabled in support.items():
            if keys is None or feature not in keys.key_blocks:
                enabled[ids] = False
    raw = weights.copy()
    x, y, z = points.T
    pelvis = bone["Bone_001"]
    for sign, foot, hock, knee, thigh in (
        (1., "Bone_007", "Bone_008", "Bone_009", "Bone_010"),
        (-1., "Bone_013", "Bone_014", "Bone_015", "Bone_016"),
    ):
        # The toe and sole stay rigid. Blend the calf into the foot above the shoe-line.
        sole = (1. - smooth(.061, .150, z)) * smooth(.115, .159, sign*x)
        sole *= smooth(-.28, -.13, y) * (1. - smooth(.28, .39, y))
        weights *= 1. - sole[:, None]
        weights[:, bone[foot]] += sole
        # Adjacent short edges at the inner hock must interpolate a common transform.
        hock_patch = patch(points, np.array([sign*.188, -.068, .352]), .053, .192)
        weights *= 1. - hock_patch[:, None]
        for index, share in ((pelvis, .53), (bone[hock], .24), (bone[knee], .19), (bone[thigh], .04)):
            weights[:, index] += share * hock_patch
        # The side of the hip needs a slower transition than the source transfer supplied.
        hip = patch(points, np.array([sign*.270, .197, .617]), .022, .125) * .75
        weights *= 1. - hip[:, None]
        weights[:, pelvis] += hip * .59
        weights[:, bone[thigh]] += hip * .41
    # The two export triangulators split this nearly collinear strip differently.
    # Share its existing mean influences over the strip and blend to nearby vertices.
    strip = np.array([5832, 28109, 19504])
    calf = patch(points, points[strip].mean(axis=0), .011, .045)
    weights = (1. - calf[:, None]) * weights + calf[:, None] * weights[strip].mean(axis=0)
    rear = np.abs(weights - raw).sum(axis=1) > 1e-7

    throat = smooth(-1.49, -1.29, y) * (1. - smooth(-1.045, -.84, y))
    throat *= smooth(.385, .511, z) * (1. - smooth(.705, .812, z))
    throat *= 1. - smooth(.147, .238, np.abs(x))
    throat *= exterior
    neck = 1. - smooth(-1.31, -.94, y)
    weights *= 1. - throat[:, None]
    weights[:, bone["Bone_002"]] += throat * (1. - neck)
    weights[:, bone["Bone_024"]] += throat * neck

    jaw = bone["LowerJaw"]
    # Keep both lips and the inner lining separate, with a softer hinge at the mouth corner.
    hinge = 1. - smooth(1.534, 1.625, -y)
    hinge *= smooth(1.45, 1.516, -y)
    hinge *= smooth(.704, .758, z) * (1. - smooth(.825, .868, z))
    removed = weights[:, jaw] * hinge * .78
    weights[:, jaw] -= removed
    weights[:, bone["Bone_022"]] += removed
    weights[weights < .000015] = 0.
    max_influences = int(np.count_nonzero(raw, axis=1).max())
    changed = (np.abs(weights - raw).sum(axis=1) > 1e-7) & (rear | (throat > 0.) | (removed > 0.))
    rows = np.flatnonzero(changed)
    keep = np.argpartition(-weights[rows], max_influences - 1, axis=1)[:, :max_influences]
    normalized = np.zeros_like(weights[rows])
    np.put_along_axis(normalized, keep, np.take_along_axis(weights[rows], keep, axis=1), axis=1)
    weights[rows] = normalized / normalized.sum(axis=1, keepdims=True)
    for obj in parts:
        ids = ids_by_part[obj.name]
        groups = {group.name: group for group in obj.vertex_groups}
        for index in np.flatnonzero(changed[ids]):
            vertex = obj.data.vertices[index]
            for group in tuple(vertex.groups):
                obj.vertex_groups[group.group].remove([int(index)])
            for column in np.flatnonzero(weights[ids[index]]):
                name = names[column]
                if name not in groups:
                    groups[name] = obj.vertex_groups.new(name=name)
                groups[name].add([int(index)], weights[ids[index], column], "REPLACE")

    # Use continuous fields so the sides of the throat move together in every frame.
    # x itself gives a continuous lateral component through the throat midline.
    pulse = smooth(-1.48, -1.25, y) * (1. - smooth(-1.12, -.86, y))
    pulse *= smooth(.445, .546, z) * (1. - smooth(.694, .810, z))
    pulse *= 1. - smooth(.127, .231, np.abs(x))
    cheek = smooth(-1.626, -1.563, y) * (1. - smooth(-1.452, -1.373, y))
    cheek *= smooth(.709, .768, z) * (1. - smooth(.815, .879, z))
    cheek *= smooth(.080, .146, np.abs(x)) * (1. - smooth(.196, .239, np.abs(x)))
    deltas = {
        "Throat": pulse[:, None] * np.column_stack((.05*x, np.full(size, -.004), np.full(size, -.012))),
        "RoarTension": cheek[:, None] * np.column_stack((.012*x, np.full(size, .004), np.full(size, .003))),
    }
    shape_counts = {}
    for name, delta in deltas.items():
        # A part boundary stays watertight even when only one object has the shape.
        delta[~(support[name] & exterior)] = 0.
        shape_counts[name] = int(np.count_nonzero(np.linalg.norm(delta, axis=1)))
        for obj in parts:
            keys = obj.data.shape_keys
            if keys and (target := keys.key_blocks.get(name)):
                ids = ids_by_part[obj.name]
                target.data.foreach_set("co", (points[ids] + delta[ids]).ravel())
    rig["Revision"] = "Localized hock, sole, hip and jaw weights; continuous Throat and RoarTension"
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opt.output.resolve()), compress=True)
    summary = {
        "weightEdits": int(changed.sum()), "rearWeightEdits": int(rear.sum()),
        "throatWeightEdits": int(np.count_nonzero(throat)), "jawWeightEdits": int(np.count_nonzero(removed)),
        "shapeVertices": shape_counts, "unchangedBaseTopology": True,
        "maxInfluences": max_influences,
        "normalizedWeightMaxError": float(np.abs(weights[rows].sum(axis=1)-1.).max()),
    }
    opt.report.write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    print(summary, flush=True)


if __name__ == "__main__":
    main()
