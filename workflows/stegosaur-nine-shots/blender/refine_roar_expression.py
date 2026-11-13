"""Fair the shared lip rim and author eye performance on the existing quad rig."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refine_stegosaur import smooth


def xyz(collection):
    values = np.empty(len(collection) * 3, dtype=np.float32)
    collection.foreach_get("co", values)
    return values.reshape((-1, 3))


def main():
    parser = argparse.ArgumentParser()
    for arg in ("output", "report", "motion", "web-motion"):
        parser.add_argument("--" + arg, type=Path, required=True)
    opts = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    rig = bpy.data.objects["SmartRigArmature"]
    control = rig.pose.bones["CTRL_Head"]
    head = bpy.data.objects["HeadNeck"]
    lining = bpy.data.objects["MouthInterior"]
    mesh = head.data
    head_keys = mesh.shape_keys.key_blocks

    indices = {
        part.name: np.array([item.value for item in part.data.attributes["CageVertex"].data])
        for part in rig.children if part.type == "MESH"
    }
    head_ids, lining_ids = (indices[part.name] for part in (head, lining))
    base, inner = xyz(mesh.vertices), xyz(lining.data.vertices)
    x, y, z = base.T
    seam = np.isin(head_ids, lining_ids)
    head_only = ~np.isin(head_ids, np.concatenate([ids for name, ids in indices.items() if name != head.name]))
    side = np.abs(x)
    source_normals = np.array([v.normal[:] for v in mesh.vertices])
    jaw_index = head.vertex_groups["LowerJaw"].index
    jaw = np.array([next((g.weight for g in v.groups if g.group == jaw_index), 0.)
                    for v in mesh.vertices])

    # The corner is shared by the skin and the lining. Fair the rim along its
    # own two paths, without averaging across the opening or the palate.
    neighbors = defaultdict(list)
    for edge in mesh.edges:
        a, b = edge.vertices
        if seam[a] == seam[b]:
            neighbors[a].append(b)
            neighbors[b].append(a)
    region = smooth(-1.586, -1.550, y) * (1. - smooth(-1.510, -1.475, y))
    region *= smooth(.743, .768, z) * (1. - smooth(.790, .823, z))
    region *= smooth(.084, .110, side) * (1. - smooth(.161, .190, side))
    delta = np.zeros_like(base)
    for i in np.flatnonzero(region > 0.):
        linked = np.array(neighbors[i], dtype=np.int32)
        if len(linked) < 2:
            continue
        steps = base[linked] - base[i]
        lengths = np.linalg.norm(steps, axis=1)
        if seam[i] and len(linked) > 2:
            keep = np.argsort(lengths)[:2]
            steps, lengths = steps[keep], lengths[keep]
        fair = np.average(steps, axis=0, weights=1. / np.maximum(lengths, .001))
        strength = .40 if seam[i] else .35
        delta[i] = region[i] * np.clip(strength * fair, -.006, .006)
    # At the hinge the lower lip stays close to the upper lip until the
    # opening widens toward the snout. Ease the pinched upper edge into that curve.
    corner = smooth(-1.590, -1.558, y) * (1. - smooth(-1.535, -1.516, y))
    upper = corner * seam * (1. - smooth(.002, .010, jaw))
    upper *= 1. - smooth(-.30, .10, source_normals[:, 2])
    delta[:, 0] += np.sign(x) * .0025 * upper
    delta[:, 2] -= .0040 * upper
    delta[~(head_only | seam)] = 0.
    original = xyz(head_keys["RoarTension"].data)
    revised = original + delta
    head_keys["RoarTension"].data.foreach_set("co", revised.ravel())

    lining_keys = lining.data.shape_keys.key_blocks
    inner_target = lining_keys.get("RoarTension")
    if inner_target is None:
        inner_target = lining.shape_key_add(name="RoarTension", from_mix=False)
    inner_revised = inner.copy()
    offsets = {int(label): d for label, d in zip(head_ids[seam], (revised - base)[seam], strict=True)}
    inner_x, inner_y, inner_z = inner.T
    recession = smooth(.066, .118, np.abs(inner_x)) * (1. - smooth(.140, .154, np.abs(inner_x)))
    recession *= smooth(-1.580, -1.534, inner_y) * (1. - smooth(-1.505, -1.467, inner_y))
    recession *= smooth(.745, .769, inner_z) * (1. - smooth(.791, .803, inner_z))
    inner_revised[:, 0] -= np.sign(inner_x) * .002 * recession
    for i, label in enumerate(lining_ids):
        if int(label) in offsets:
            inner_revised[i] = inner[i] + offsets[int(label)]
    inner_target.data.foreach_set("co", inner_revised.ravel())

    # Match the lip corner's surface normal to the surrounding cheek. The
    # welded cage originally averaged in the up/down-facing palate at this
    # narrow strip, which created a dark dimple on the exterior.
    lip_shading = smooth(-1.604, -1.563, y) * (1. - smooth(-1.514, -1.475, y))
    lip_shading *= smooth(.727, .763, z) * (1. - smooth(.794, .824, z))
    lip_shading *= smooth(.078, .106, side) * (1. - smooth(.166, .194, side))
    mouth_normal = np.column_stack((np.sign(x) * (.91 - .24 * smooth(-1.570, -1.512, y)),
                                    -.43 - .29 * smooth(-1.570, -1.512, y),
                                    np.clip(5. * (z - .781), -.24, .16)))
    mouth_normal /= np.linalg.norm(mouth_normal, axis=1)[:, None]
    head_lip_normals = source_normals * (1. - lip_shading[:, None]) + mouth_normal * lip_shading[:, None]
    head_lip_normals /= np.linalg.norm(head_lip_normals, axis=1)[:, None]
    mesh.normals_split_custom_set_from_vertices(head_lip_normals.tolist())

    # This strip belongs to the skin material visually. Move the material
    # border one inner ring back so dark mouth lining cannot show on the cheek.
    lining_mesh = lining.data
    skin_material = mesh.materials[0]
    lip_material = skin_material.copy()
    lip_material.name = "LipTransition"
    for node in lip_material.node_tree.nodes:
        if node.type == "NORMAL_MAP":
            node.inputs["Strength"].default_value = .38
    material_slot = len(lining_mesh.materials)
    lining_mesh.materials.append(lip_material)
    head_uv = mesh.uv_layers.active.data
    lining_uv = lining_mesh.uv_layers.active.data
    candidates = []
    for polygon in mesh.polygons:
        v = np.asarray(polygon.vertices[:])
        center = base[v].mean(axis=0)
        if -1.59 < center[1] < -1.50 and .751 < center[2] < .817 and abs(center[0]) > .092:
            coords = np.asarray([head_uv[i].uv[:] for i in polygon.loop_indices])
            candidates.append((center, base[v], coords))
    exterior_centers = np.asarray([item[0] for item in candidates])
    seam_material_faces = 0
    lining_rim = np.isin(lining_ids, head_ids)
    for polygon in lining_mesh.polygons:
        v = np.asarray(polygon.vertices[:])
        px, py, pz = inner[v].mean(axis=0)
        if not (lining_rim[v].any() and .118 < abs(px) and -1.550 < py < -1.508 and .768 < pz < .786):
            continue
        center_dist = np.linalg.norm(exterior_centers - (px, py, pz), axis=1)
        _, world_patch, uv_patch = candidates[int(center_dist.argmin())]
        # Keep all four corners in the same source UV chart.
        dist = np.linalg.norm(inner[v, None] - world_patch, axis=2)
        blend = 1. / np.maximum(dist, .0003)**4
        coords = (blend / blend.sum(axis=1, keepdims=True)) @ uv_patch
        for loop, uv in zip(polygon.loop_indices, coords, strict=True):
            lining_uv[loop].uv = uv
        polygon.material_index = material_slot
        seam_material_faces += 1

    # The iris is painted on the skin. Move its center tangentially with a
    # falloff inside the eyelid, so a glance does not move the whole head.
    track = head.shape_key_add(name="EyeTrack", from_mix=False)
    iris = np.sqrt(((y + 1.512) / .052)**2 + ((z - .860) / .044)**2)
    mask = (1. - smooth(.19, 1., iris)) * smooth(.084, .119, side) * head_only
    look = np.array((0., .022, .008))
    tangent = look - (source_normals @ look)[:, None] * source_normals
    eye_delta = mask[:, None] * tangent
    track.data.foreach_set("co", (base + eye_delta).astype(np.float32).ravel())

    # Use a small eyelid aperture field for the close-up, separate from the
    # existing broad Blink target used by other takes. Fade before the cheek.
    eyelid = head.shape_key_add(name="RoarBlink", from_mix=False)
    eye_center = .877 + .22 * (y + 1.474)
    aperture = z - eye_center
    eye_width = np.abs(y + 1.475)
    lid_region = (1. - smooth(.043, .083, eye_width)) * smooth(.086, .118, side) * head_only
    lid_region *= 1. - smooth(.037, .060, np.abs(aperture))
    lid_delta = np.zeros_like(base)
    lid_delta[:, 2] = -.84 * aperture * lid_region
    lid_delta[:, 0] = -.0018 * np.sign(x) * lid_region
    eyelid.data.foreach_set("co", (base + lid_delta).ravel())

    for target in (inner_target, track, eyelid):
        path = target.path_from_id("value")
        animation = target.id_data.animation_data
        if animation and animation.drivers.find(path):
            continue
        property_name = "Shape_" + target.name
        if property_name not in control:
            control[property_name] = 0.
            control.id_properties_ui(property_name).update(min=0., max=1.)
        driver = target.driver_add("value").driver
        driver.type = "AVERAGE"
        var = driver.variables.new()
        var.name, var.type = "amount", "SINGLE_PROP"
        var.targets[0].id = rig
        var.targets[0].data_path = control.path_from_id() + f'["{property_name}"]'

    motion = json.loads(opts.motion.read_text(encoding="utf-8-sig"))
    blink = [(0, 0), (7, 0), (10, 76), (11, 100), (13, 55), (16, 0),
             (25, 0), (34, 18), (44, 20), (51, 0), (58, 0), (62, 77),
             (63, 96), (65, 55), (69, 0), (72, 0)]
    glance = [(0, 0), (4, 12), (8, 94), (14, 91), (20, 68), (26, 6),
              (34, 16), (44, 43), (52, 78), (58, 93), (64, 88), (72, 42)]
    ad = rig.animation_data
    old_action, old_slot = ad.action, ad.action_slot
    for clip in motion["clips"]:
        count = clip["intervals"] + 1
        is_roar = clip["name"] == "Roar"
        eye_shape = {"name": "EyeTrack", "values": [0.] * count}
        blink_shape = {"name": "RoarBlink", "values": [0.] * count}
        if is_roar:
            for spec, marks in ((blink_shape, blink), (eye_shape, glance)):
                for (start, low), (end, high) in zip(marks, marks[1:]):
                    frames = np.arange(start, end + 1)
                    spec["values"][start:end+1] = np.round(low + (high - low) * smooth(start, end, frames), 4).tolist()
        names = {eye_shape["name"], blink_shape["name"]}
        clip["shapes"] = [s for s in clip["shapes"] if s["name"] not in names] + [eye_shape, blink_shape]
        action = bpy.data.actions[clip["name"] + "_Controls"]
        ad.action = action
        ad.action_slot = action.slots[0]
        curves = action.layers[0].strips[0].channelbag(ad.action_slot).fcurves
        for spec in clip["shapes"]:
            if spec["name"] not in names:
                continue
            path = f'["Shape_{spec["name"]}"]'
            full_path = control.path_from_id() + path
            if old_curve := curves.find(full_path):
                curves.remove(old_curve)
            for frame, value in enumerate(spec["values"], 1):
                control[f'Shape_{spec["name"]}'] = value / 100.
                control.keyframe_insert(data_path=path, frame=frame, group=control.name)
            for key in curves.find(full_path).keyframe_points:
                key.interpolation = "LINEAR"
    ad.action, ad.action_slot = old_action, old_slot
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    rig["RoarExpressionRevision"] = "Shared lip rim fairing; authored blink and eye glance"
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opts.output.resolve()), compress=True)
    serialized = json.dumps(motion, separators=(",", ":"), ensure_ascii=False) + "\n"
    for path in (opts.motion, opts.web_motion):
        path.write_text(serialized, encoding="utf-8")
    report = dict(
        lipExteriorVertices=int(np.count_nonzero(np.linalg.norm(delta, axis=1))),
        lipSharedVertices=int(np.count_nonzero(seam & (np.linalg.norm(delta, axis=1) > 0.))),
        lipLiningVertices=int(np.count_nonzero(np.linalg.norm(inner_revised - inner, axis=1))),
        lipNormalVertices=int(np.count_nonzero(lip_shading)),
        lipSkinOverlapQuads=seam_material_faces,
        lipMaxDeltaBlenderUnits=float(np.linalg.norm(delta, axis=1).max()),
        irisVertices=int(np.count_nonzero(mask)),
        irisMaxDeltaBlenderUnits=float(np.linalg.norm(eye_delta, axis=1).max()),
        lidVertices=int(np.count_nonzero(lid_region)),
        lidMaxDeltaBlenderUnits=float(np.linalg.norm(lid_delta, axis=1).max()),
        blinkKeysPercent=blink,
        glanceKeysPercent=glance,
    )
    opts.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
