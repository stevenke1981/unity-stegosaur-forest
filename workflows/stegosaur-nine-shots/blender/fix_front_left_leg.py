"""Rebind both forelegs while maintaining one set of weights per cage vertex."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from check_quad_stegosaur import coordinates
from refine_stegosaur import smooth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    opt = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    rig = bpy.data.objects["SmartRigArmature"]
    parts = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.parent == rig]
    names = [bone.name for bone in rig.data.bones if bone.use_deform]
    column = {name: i for i, name in enumerate(names)}
    ids_by_part = {obj.name: np.array([item.value for item in obj.data.attributes["CageVertex"].data])
                   for obj in parts}
    size = max(ids.max() for ids in ids_by_part.values()) + 1
    points, original = np.zeros((size, 3)), np.zeros((size, len(names)))
    for obj in parts:
        ids = ids_by_part[obj.name]
        points[ids] = coordinates(obj.data)
        group_columns = [column[group.name] for group in obj.vertex_groups]
        for vertex, cage_id in zip(obj.data.vertices, ids, strict=True):
            for group in vertex.groups:
                original[cage_id, group_columns[group.group]] = group.weight

    x, y, z = points.T
    exterior = np.zeros(size, dtype=bool)
    for obj in parts:
        if obj.name in {"FrontLeftLeg", "FrontRightLeg", "Body", "HeadNeck", "HindLeftLeg", "HindRightLeg"}:
            exterior[ids_by_part[obj.name]] = True
    # The part cuts do not follow bone influence. Use a continuous anatomical field
    # on the shared cage, including the narrow collar of the adjoining body surface.
    forelimb = (smooth(-1.24, -1.12, y) * (1. - smooth(-.58, -.40, y))
                * (1. - smooth(.75, .92, z)) * exterior)
    shoulder = smooth(.52, .87, z)
    chest = 1. - smooth(-.87, -.54, y)
    elbow = smooth(.17, .46, z)
    # Vertices above the front of the ankle still belong to the upper foot.
    # Slope and widen the blend so the top of the foot does not fan open on flexion.
    ankle = smooth(.065, .332, z - .28 * np.maximum(-y - .760, 0.))
    toe = .18 * (1. - smooth(-1.12, -1.015, y)) * (1. - smooth(.041, .105, z))
    edited = np.zeros_like(original)
    combined = np.zeros(size)
    for sign, upper, lower, foot, digits in (
        (1, "Bone_034", "Bone_033", "Bone_032", "Bone_031"),
        (-1, "Bone_029", "Bone_028", "Bone_027", "Bone_026"),
    ):
        target = np.zeros_like(original)
        for name, values in (
            ("Bone_004", shoulder * (1. - chest)),
            ("Bone_002", shoulder * chest),
            (upper, (1. - shoulder) * elbow),
            (lower, (1. - shoulder) * (1. - elbow) * ankle),
            (foot, (1. - shoulder) * (1. - elbow) * (1. - ankle) * (1. - toe)),
            (digits, (1. - shoulder) * (1. - elbow) * (1. - ankle) * toe),
        ):
            target[:, column[name]] = values
        factor = forelimb * smooth(.025, .115, sign * (x - .007))
        if sign == 1:
            # Share a common weight set over the very thin outer shoulder triangles.
            strip = np.array([4553, 16951, 16901, 4567, 16946, 22773])
            common = 1. - smooth(.031, .09, np.linalg.norm(points - points[strip].mean(axis=0), axis=1))
            target = target * (1. - common[:, None]) + target[strip].mean(axis=0) * common[:, None]
            factor = np.maximum(factor, common * .94 * exterior)
        edited += target * factor[:, None]
        combined += factor
    edited += original * (1. - combined[:, None])

    # Relax the inner leg-to-sternum blend on the welded cage. The cut between
    # the leg and belly pieces crosses this transition, so process shared IDs once.
    links = []
    for obj in parts:
        ids = ids_by_part[obj.name]
        links.extend((ids[a], ids[b]) for edge in obj.data.edges for a, b in [edge.vertices]
                     if exterior[ids[a]] and exterior[ids[b]])
    edges = np.unique(np.sort(np.asarray(links), axis=1), axis=0)
    strength = (.68 * (1. - smooth(.10, .25, np.abs(x - .007)))
                * smooth(-1.04, -.94, y) * (1. - smooth(-.47, -.34, y))
                * smooth(.10, .22, z) * (1. - smooth(.51, .66, z)) * exterior)
    active = np.flatnonzero(strength > 1e-7)
    first, second = np.concatenate((edges, edges[:, ::-1])).T
    relevant = strength[first] > 1e-7
    first, second = first[relevant], second[relevant]
    samples = np.bincount(first, minlength=size)[active, None]
    amount = strength[active, None]
    for _ in range(12):
        neighbor_sum = np.zeros_like(edited)
        np.add.at(neighbor_sum, first, edited[second])
        edited[active] = (1. - amount) * edited[active] + amount * neighbor_sum[active] / samples
    rows = np.flatnonzero((combined > 1e-7) | (strength > 1e-7))
    edited = edited[rows]
    keep = np.argsort(-edited, axis=1, kind="stable")[:, :8]
    values = np.take_along_axis(edited, keep, axis=1)
    values[values < .00015] = 0.
    normalized = np.zeros_like(edited)
    np.put_along_axis(normalized, keep, values / values.sum(axis=1, keepdims=True), axis=1)
    new = original.copy()
    new[rows] = normalized
    edited_per_part = {}
    for obj in parts:
        ids = ids_by_part[obj.name]
        groups = {group.name: group for group in obj.vertex_groups}
        indices = np.flatnonzero((combined[ids] > 1e-7) | (strength[ids] > 1e-7))
        if len(indices):
            edited_per_part[obj.name] = len(indices)
        for i in indices:
            for group in tuple(obj.data.vertices[i].groups):
                obj.vertex_groups[group.group].remove([int(i)])
            for j in np.flatnonzero(new[ids[i]]):
                name = names[j]
                if name not in groups:
                    groups[name] = obj.vertex_groups.new(name=name)
                groups[name].add([int(i)], new[ids[i], j], "REPLACE")

    rig["FrontLegRevision"] = "Both forelegs rebound through toes, ankle, elbow and chest"
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opt.output.resolve()), compress=True)
    summary = {"uniqueVerticesChanged": len(rows), "affectedParts": edited_per_part}
    for side, suffix in (("FrontLeftLeg", (7, 8, 9, 10)), ("FrontRightLeg", (13, 14, 15, 16))):
        indices = ids_by_part[side]
        rear = [column[f"Bone_{i:03}"] for i in suffix]
        summary[side] = {"peakRearLegWeightBefore": float(original[indices][:, rear].sum(axis=1).max()),
                         "peakRearLegWeightAfter": float(new[indices][:, rear].sum(axis=1).max())}
    opt.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
