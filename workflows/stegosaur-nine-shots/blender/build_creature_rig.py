"""Build the stegosaur's editable control rig and bake its Unity motion library."""
import argparse
from collections import defaultdict
import hashlib
import json
from math import cos, pi, radians, sin
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from creature_motion import BODY_SCALE, CLIPS, FPS, frame_data, path, smooth, step_at
from export_fbx import export_fbx

UP = Vector((0, 0, 1))
SIDE = Vector((1, 0, 0))
FORWARD = Vector((0, -1, 0))
PELVIS = "Bone_000"
CHEST = "Bone_002"
SPINE = "Bone_004"
NECK = "Bone_024"
HEAD = "Bone_023"
JAW = "LowerJaw"
TAIL = ("Bone_021", "Bone_020", "Bone_019", "Bone_018", "Bone_017")
FK = (("Spine", SPINE), ("Chest", CHEST), ("Neck", NECK), ("Head", HEAD), ("Jaw", JAW),
      *((f"Tail_{i + 1}", name) for i, name in enumerate(TAIL)))
LEGS = {
    "FL": ("FrontLeftLeg", "Bone_034", "Bone_033", "Bone_032", "Bone_031"),
    "FR": ("FrontRightLeg", "Bone_029", "Bone_028", "Bone_027", "Bone_026"),
    "HL": ("HindLeftLeg", "Bone_010", "Bone_009", "Bone_008", "Bone_007", "Bone_006"),
    "HR": ("HindRightLeg", "Bone_016", "Bone_015", "Bone_014", "Bone_013", "Bone_012"),
}
PREFIX = "CTRL_"


def create_controls(rig, pieces):
    bones = rig.data.bones
    specs = {}
    for side, (mesh, *chain) in LEGS.items():
        hip, knee, ankle = (bones[bone].head_local.copy() for bone in chain[:3])
        base = bones[chain[-2]].head_local.copy()
        mesh_points = [vertex.co.copy() for vertex in pieces[mesh].data.vertices if vertex.co.z < .014]
        x = base.x
        front_y = min(p.y for p in mesh_points)
        back_y = max(p.y for p in mesh_points)
        ball = Vector((x, front_y + .009, 0))
        heel = Vector((x, back_y - .026, 0))
        sole = Vector((x, (front_y + back_y) * .5, 0))
        target = ankle.copy() if len(chain) == 5 else base.copy()
        line = target - hip
        bend = (knee - hip) - (knee - hip).project(line)
        pole = knee + bend.normalized() * .73
        specs[side] = {"mesh": mesh, "chain": chain, "hip": hip, "knee": knee, "ankle": ankle,
                       "base": base, "sole": sole, "ball": ball, "heel": heel, "pole": pole,
                       "samples": [v.index for v in pieces[mesh].data.vertices if v.co.z < .0035]}
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    edit = rig.data.edit_bones

    def add(name, position, length, parent=None):
        bone = edit.new(PREFIX + name)
        bone.head = position
        bone.tail = position + UP * length
        bone.use_deform = False
        if parent:
            bone.parent = edit[parent]
        return bone.name

    root = add("Root", Vector((0, 0, 0)), .32)
    for label, bone_name, length in (
        ("Pelvis", PELVIS, .29),
        *((label, name, .12 if label.startswith("Tail") or label == "Jaw" else .19) for label, name in FK),
    ):
        position = edit[bone_name].head.copy()
        add(label, position, length, root if label == "Pelvis" else None)
        if label == "Pelvis":
            edit[PELVIS].parent = edit[PREFIX + label]
        else:
            edit[PREFIX + label].parent = edit[bone_name].parent

    add("SpineTarget", edit["Bone_003"].tail, .19, root)
    for side, spec in specs.items():
        foot = add(f"Foot.{side}", spec["sole"], .20, root)
        heel = add(f"Heel.{side}", spec["heel"], .14, foot)
        toe = add(f"Toe.{side}", spec["ball"], .14, heel)
        pole = add(f"KneePole.{side}", spec["pole"], .15, root)
        ankle = toe
        if len(spec["chain"]) == 5:
            ankle = add(f"Hock.{side}", spec["ankle"], .18, toe)
        base = add(f"FootTarget.{side}", spec["base"], .14, toe)
        endpoint = ankle if len(spec["chain"]) == 5 else base
        spec.update(foot=foot, toe=toe, heelControl=heel, poleControl=pole, endpoint=endpoint, baseTarget=base)
    bpy.ops.object.mode_set(mode="OBJECT")
    pose = rig.pose.bones
    all_controls = rig.data.collections.new("動畫控制器")
    internals = rig.data.collections.new("IK 內部目標")
    deformation = rig.data.collections.new("變形骨")
    for bone in rig.data.bones:
        for collection in list(bone.collections):
            collection.unassign(bone)
        if bone.name.startswith(PREFIX + "FootTarget"):
            internals.assign(bone)
        elif bone.name.startswith(PREFIX):
            all_controls.assign(bone)
        else:
            deformation.assign(bone)
    deformation.is_visible = False
    internals.is_visible = False
    for label, bone_name in FK:
        pose[PREFIX + label]["DeformBone"] = bone_name

    for side, spec in specs.items():
        _, shin, *distal = spec["chain"]
        for name in spec["chain"][:2]:
            pose[name].ik_stretch = 0.0
        ik = pose[shin].constraints.new("IK")
        ik.name = "Two-link leg IK"
        ik.target = ik.pole_target = rig
        ik.subtarget, ik.pole_subtarget = spec["endpoint"], spec["poleControl"]
        ik.chain_count = 2
        ik.use_stretch = False
        ik.iterations = 128
        # This constraint makes the lower foot follow the actual reverse roll.
        # The front foot and the rear pad are transferred by a calibrated helper.
        foot_bone = pose[distal[-2]]
        follow = foot_bone.constraints.new("COPY_ROTATION")
        follow.name = "Reverse foot orientation"
        follow.target, follow.subtarget = rig, spec["baseTarget"]
        follow.owner_space = follow.target_space = "POSE"

        if len(spec["chain"]) == 5:
            hock = pose[distal[0]].constraints.new("DAMPED_TRACK")
            hock.name = "Aim metatarsus at the pad"
            hock.target, hock.subtarget = rig, spec["baseTarget"]
            hock.track_axis = "TRACK_Y"
        spec["ik"] = ik

    # Make FK controls share each deform bone's rest orientation. Copy Rotation
    # in pose space can then drive the deform bone without a rest-pose offset.
    bpy.ops.object.mode_set(mode="EDIT")
    edit = rig.data.edit_bones
    for label, bone_name in FK:
        control = edit[PREFIX + label]
        original = edit[bone_name]
        length = control.length
        control.matrix = original.matrix
        control.length = length
    for spec in specs.values():
        control = edit[spec["baseTarget"]]
        original = edit[spec["chain"][-2]]
        control.matrix = original.matrix
        control.length = .115
    bpy.ops.object.mode_set(mode="OBJECT")
    for label, bone_name in FK:
        copy = pose[bone_name].constraints.new("COPY_ROTATION")
        copy.name = "Animator FK control"
        copy.target, copy.subtarget = rig, PREFIX + label
        copy.owner_space = copy.target_space = "POSE"
    spine_target = pose[PREFIX + "SpineTarget"]
    spine_target["SpineIK"] = 0.0
    spine_target.id_properties_ui("SpineIK").update(min=0.0, max=1.0,
        description="Blend the FK spine toward this world-space chest target.")
    constraint = pose["Bone_003"].constraints.new("IK")
    constraint.name = "Two-link optional spine IK"
    constraint.target, constraint.subtarget = rig, spine_target.name
    constraint.chain_count, constraint.use_stretch = 2, False
    driver = constraint.driver_add("influence").driver
    driver.type = "AVERAGE"
    variable = driver.variables.new()
    variable.name = "blend"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = rig
    variable.targets[0].data_path = f'pose.bones["{spine_target.name}"]["SpineIK"]'
    for p in pose:
        p.rotation_mode = "QUATERNION"

    # The imported joints have different local rolls. Find each pole angle
    # against the actual unposed knee and thigh instead of assuming symmetry.
    for spec in specs.values():
        ik = spec["ik"]
        thigh, shin = (pose[n] for n in spec["chain"][:2])

        def score(angle):
            ik.pole_angle = (angle + pi) % (2 * pi) - pi
            bpy.context.view_layer.update()
            return (shin.head - spec["knee"]).length_squared + (thigh.head - spec["hip"]).length_squared

        best = min((i * pi / 36 for i in range(-36, 36)), key=score)
        best = min((best + i * pi / 1800 for i in range(-25, 26)), key=score)
        spec["kneeError"] = score(best) ** .5
        spec["poleAngle"] = ik.pole_angle
        if spec["kneeError"] > .017:
            raise ValueError(f"Could not calibrate the knee for {spec['mesh']}: {spec['kneeError']}")
    return specs


def make_shapes(rig, pieces, *, blink_center=(-1.460, .840), blink_radii=(.098, .094)):
    blend_by_name = defaultdict(list)

    def feature(name, obj, fn):
        base = obj.shape_key_add(name="Basis") if obj.data.shape_keys is None else obj.data.shape_keys.key_blocks[0]
        key = obj.shape_key_add(name=name, from_mix=False)
        for target, source in zip(key.data, base.data, strict=True):
            target.co = source.co + fn(source.co)
        blend_by_name[name].append((obj.data.shape_keys, key))

    def breathe(p):
        s = smooth(.10, .34, abs(p.x)) * smooth(-.85, -.50, p.y) * (1 - smooth(-.22, .23, p.y))
        s *= smooth(.46, .76, p.z) * (1 - smooth(1.03, 1.18, p.z))
        return Vector((.021 * s if p.x > 0 else -.021 * s, 0, .008 * s))

    for mesh in ("Body", "FrontLeftLeg", "FrontRightLeg", "HindLeftLeg", "HindRightLeg",
                 "BackPlate_05", "BackPlate_06", "BackPlate_07"):
        feature("Breath", pieces[mesh], breathe)

    def throat(p):
        s = smooth(-1.48, -1.24, p.y) * (1 - smooth(-1.14, -.82, p.y))
        s *= smooth(.485, .55, p.z) * (1 - smooth(.63, .745, p.z)) * (1 - smooth(.12, .22, abs(p.x)))
        return Vector((.011 * s if p.x > 0 else -.011 * s, -.006 * s, -.026 * s))

    def blink(p):
        y, z = blink_center
        if abs(p.x) < .082:
            return Vector()
        d = ((p.y - y) / blink_radii[0]) ** 2 + ((p.z - z) / blink_radii[1]) ** 2
        s = 1 - smooth(.37, 1.12, d)
        s *= smooth(.083, .14, abs(p.x))
        return Vector(((-.016 if p.x > 0 else .016) * s, 0, (z - p.z) * .91 * s))

    def flare(p):
        s = smooth(-1.685, -1.655, p.y) * (1 - smooth(-1.60, -1.559, p.y))
        s *= smooth(.773, .801, p.z) * (1 - smooth(.858, .893, p.z))
        s *= smooth(.034, .064, abs(p.x)) * (1 - smooth(.122, .157, abs(p.x)))
        return Vector((.010 * s if p.x > 0 else -.010 * s, -.005 * s, .006 * s))

    def tension(p):
        s = smooth(-1.592, -1.55, p.y) * (1 - smooth(-1.49, -1.455, p.y))
        s *= smooth(.743, .770, p.z) * (1 - smooth(.795, .825, p.z))
        s *= smooth(.10, .155, abs(p.x)) * (1 - smooth(.195, .227, abs(p.x)))
        return Vector((.004 * s if p.x > 0 else -.004 * s, .010 * s, .006 * s))

    for name, modifier in (("Throat", throat), ("Blink", blink), ("NostrilFlare", flare), ("RoarTension", tension)):
        feature(name, pieces["HeadNeck"], modifier)
        property_name = "Shape_" + name
        rig.pose.bones[PREFIX + "Head"][property_name] = 0.0
        rig.pose.bones[PREFIX + "Head"].id_properties_ui(property_name).update(min=0.0, max=1.0)
    rig.pose.bones[PREFIX + "Pelvis"]["Shape_Breath"] = 0.0
    rig.pose.bones[PREFIX + "Pelvis"].id_properties_ui("Shape_Breath").update(min=0.0, max=1.0)

    for name, targets in blend_by_name.items():
        owner = PREFIX + ("Pelvis" if name == "Breath" else "Head")
        for _, key in targets:
            driver = key.driver_add("value").driver
            driver.type = "AVERAGE"
            var = driver.variables.new()
            var.name = "amount"
            var.type = "SINGLE_PROP"
            var.targets[0].id = rig
            var.targets[0].data_path = f'pose.bones["{owner}"]["Shape_{name}"]'
    return blend_by_name


def topology_sections(objects, specs, rig):
    # Weld exact boundary duplicates virtually, without touching UV islands.
    triangles = []
    for obj in objects:
        verts = obj.data.vertices
        triangles.extend(tuple(verts[i].co.copy() for i in p.vertices) for p in obj.data.polygons)
    checks = []
    for side, spec in specs.items():
        for label, index in (("upper", 0), ("knee", 1), ("ankle", 2)):
            bone = rig.data.bones[spec["chain"][index]]
            center = bone.head_local.copy()
            radius = (.365 if label == "upper" else .278) if side.startswith("H") else (.29 if label == "upper" else .218)
            checks.append((f"{label}.{side}", center, UP, radius, .033))
    checks.extend([
        ("neck", Vector((0.006, -.955, .835)), FORWARD, .264, .065),
        ("tailBase", Vector((.006, .475, .715)), -FORWARD, .313, .078),
        ("tailMid", Vector((.006, .986, .545)), -FORWARD, .237, .06),
    ])
    output = {}
    for name, center, normal, radius, step in checks:
        band = []
        near = [face for face in triangles if any((p - center).length < radius * 1.65 for p in face)]
        for distance in (-step, 0.0, step):
            target = center + normal * (distance + .00037)
            nodes = defaultdict(set)
            for face in near:
                offsets = [normal.dot(point - target) for point in face]
                points = [face[i].lerp(face[j], offsets[i] / (offsets[i] - offsets[j]))
                          for i, j in ((0, 1), (1, 2), (2, 0)) if offsets[i] * offsets[j] < 0]
                if len(points) != 2:
                    continue
                if min((p - target).length for p in points) > radius:
                    continue
                a, b = (tuple(round(n, 4) for n in p) for p in points)
                if a != b:
                    nodes[a].add(b)
                    nodes[b].add(a)
            seen, islands = set(), []
            for start in nodes:
                if start in seen:
                    continue
                pending = [start]
                seen.add(start)
                group = []
                while pending:
                    vertex = pending.pop()
                    group.append(vertex)
                    for neighbor in nodes[vertex] - seen:
                        seen.add(neighbor)
                        pending.append(neighbor)
                islands.append(group)
            largest = max(islands, key=len, default=[])
            band.append({"segments": sum(len(nodes[p]) for p in largest) // 2,
                         "closed": bool(largest) and all(len(nodes[p]) == 2 for p in largest)})
        output[name] = band
    return output


def track(rig, specs, name, frame, turn_tuning=None):
    clip = CLIPS[name]
    turn_tuning = turn_tuning if clip.turn else None
    seconds = frame / FPS
    percent = frame / clip.intervals
    wave = 2 * pi * frame / clip.period
    x, y, heading = path(seconds, clip)
    rot = Quaternion(UP, heading)
    local = lambda x, y, z=0: rot.inverted() @ (Vector((x, y, z)) - Vector(path(seconds, clip)[:2] + (0,)))
    pose = rig.pose.bones

    def translate(bone, displacement):
        pose[bone].location = pose[bone].bone.matrix_local.to_3x3().inverted() @ Vector(displacement)

    def rotate(bone, pitch=0, yaw=0, roll=0):
        basis = pose[bone].bone.matrix_local.to_quaternion()
        pose[bone].rotation_quaternion = basis.inverted() @ (
            Quaternion(UP, radians(yaw)) @ Quaternion(SIDE, radians(pitch)) @ Quaternion(FORWARD, radians(roll))
        ) @ basis

    inhale = smooth(.10, .30, percent)
    relax = 1 - smooth(.65, .99, percent)
    body_yaw = body_pitch = lunge = neck_pitch = head_pitch = head_yaw = jaw_angle = tail_push = 0.0
    response = inhale * relax
    shapes = {"Breath": .24 + .62 * (.5 - .5 * cos(2 * pi * seconds / 2.0)),
              "Blink": 0., "Throat": 0., "RoarTension": 0., "NostrilFlare": 0.}
    if name == "Idle":
        head_yaw = 8.3 * sin(2 * pi * percent)
        neck_pitch = 1.65 * sin(2 * pi * percent)
        shapes["Blink"] = smooth(.293, .319, percent) * (1 - smooth(.335, .375, percent))
    elif name == "Roar":
        peak = smooth(.16, .37, percent) * (1 - smooth(.63, .90, percent))
        lift = smooth(.05, .32, percent) * (1 - smooth(.68, .97, percent))
        head_pitch, neck_pitch, body_pitch = -12.5 * lift, -6.5 * lift, -1.4 * lift
        jaw_angle = peak * (35.5 + 1.8 * sin(2 * pi * 4.4 * seconds))
        shapes.update(Breath=.3 + .7 * inhale * (1 - smooth(.35, .73, percent)),
                      Throat=peak * (.65 + .32 * sin(2 * pi * 5 * seconds)),
                      NostrilFlare=.83 * peak, RoarTension=.82 * peak)
    elif name == "Bite":
        coil = smooth(.05, .28, percent) * (1 - smooth(.31, .47, percent))
        snap = smooth(.26, .48, percent) * (1 - smooth(.56, .86, percent))
        lunge = .115 * snap - .045 * coil
        body_pitch, neck_pitch, head_pitch = 1.8 * snap, 5.4 * snap - 3.2 * coil, 8.6 * snap
        jaw_angle = 29.0 * smooth(.15, .31, percent) * (1 - smooth(.35, .48, percent))
    elif name == "TailWhip":
        windup = smooth(.03, .30, percent) * (1 - smooth(.32, .49, percent))
        swing = smooth(.29, .54, percent) * (1 - smooth(.56, .94, percent))
        tail_push, body_yaw, head_yaw = 22 * windup - 29 * swing, 3.0 * swing, 13.0 * windup - 12.0 * swing
    elif name == "HitReaction":
        thump = smooth(.17, .28, percent) * (1 - smooth(.31, .62, percent))
        settle = smooth(.39, .50, percent) * (1 - smooth(.53, .92, percent))
        body_yaw, body_pitch, lunge = -5.5 * thump + 2.5 * settle, -3.4 * thump, -.10 * thump
        head_yaw, head_pitch, tail_push = -15 * thump + 6.0 * settle, -9 * thump + 5 * settle, 12 * thump - 9 * settle
        shapes["Blink"] = thump
    else:
        direction = clip.turn / abs(clip.turn) if clip.turn else 0
        next_turn = smooth(0, .27, percent) * (1 - smooth(.76, 1.0, percent))
        head_yaw = 16 * direction * next_turn + (2.2 if name == "Run" else 1.35) * sin(wave - .30)
        neck_pitch = (1.9 if name == "Run" else 1.15) * sin(2 * wave - .48)
        head_pitch = -0.54 * neck_pitch
        tail_push = -6.2 * direction * next_turn
        body_yaw = 2.8 * direction * next_turn

    walk = clip.speed > 0
    bob = clip.bob * (.5 - .5 * cos(wave * 2 - .25)) if walk else .003 * sin(2 * pi * seconds / 2)
    pelvis_drop = -.077 if walk else -.018
    if name == "Run":
        pelvis_drop = -.071
    elif name in ("TurnLeft", "TurnRight"):
        pelvis_drop = -.091
    if turn_tuning:
        pelvis_drop = turn_tuning["drop"]
        bob *= .65
    sway = (.024 if name == "Run" else .014) * sin(wave) if walk else 0
    if turn_tuning:
        sway *= .60
    translate(PREFIX + "Pelvis", (sway + .033 * response * (name == "HitReaction"), -lunge, pelvis_drop + bob))
    rotate(PREFIX + "Pelvis", body_pitch + (1.8 * sin(2 * wave - .4) if walk else 0), body_yaw, (-3.4 if name == "Run" else -1.6) * sin(wave) if walk else 0)
    rotate(PREFIX + "Chest", .63 * neck_pitch - .33 * body_pitch, .43 * head_yaw - .70 * body_yaw, 1.1 * sin(wave - .25) if walk else 0)
    rotate(PREFIX + "Neck", .51 * neck_pitch + .20 * head_pitch, .31 * head_yaw)
    rotate(PREFIX + "Head", .80 * head_pitch, .32 * head_yaw, .45 * sin(wave) if walk else 0)
    rotate(PREFIX + "Jaw", jaw_angle)
    for i, bone in enumerate(TAIL):
        delay = .30 + i * .43
        phase = (wave if walk else 2 * pi * seconds / 2.0) - delay
        tip_weight = .35 if i == len(TAIL) - 1 else 1.0
        sway_degrees = tip_weight * (2.9 if name == "Run" else 2.0 if walk else 1.2) * sin(phase)
        # Successive sections follow the initiating movement with phase lag.
        action_swing = tail_push * (.65 + .075 * i)
        if name == "TailWhip" and i:
            delayed = max(0, percent - i * .027)
            action_swing = (22 * smooth(.03, .30, delayed) * (1 - smooth(.32, .49, delayed)) -
                            29 * smooth(.29, .54, delayed) * (1 - smooth(.56, .94, delayed))) * (.65 + .075 * i)
        rotate(PREFIX + f"Tail_{i + 1}", .96 * cos(phase + .2) + 2.2 * response * (name == "TailWhip"),
               sway_degrees + tip_weight * action_swing)

    for shape, amount in shapes.items():
        owner = PREFIX + ("Pelvis" if shape == "Breath" else "Head")
        pose[owner]["Shape_" + shape] = amount

    offsets = {"HL": 0.0, "FL": .79, "HR": .50, "FR": .29}
    if name == "Run":
        offsets = {"HL": 0.0, "FR": .94, "HR": .50, "FL": .44}
    contacts = {}
    for side, spec in specs.items():
        sole = spec["sole"]
        elevation = roll = hock_flex = 0.0
        placement = sole.copy()
        step = 0.0
        phase = 0.0
        is_plant = True
        if walk:
            phase, step = step_at(frame, clip, offsets[side])
            period = clip.period / FPS
            # The short forelimbs need their support a little farther forward.
            # Offset both contact anchors equally so each stance stays locked.
            forward_bias = .090 if name == "Run" else .115
            if turn_tuning:
                forward_bias = turn_tuning["bias"]
            anchor = sole + FORWARD * forward_bias if side.startswith("F") else sole
            step_x, step_y, step_heading = path(step + .50 * clip.duty * period, clip)
            current = Vector((step_x, step_y, 0)) + Quaternion(UP, step_heading) @ anchor
            local_heading = step_heading - heading
            if phase > clip.duty:
                swing = (phase - clip.duty) / (1 - clip.duty)
                nx, ny, next_heading = path(step + (1 + .5 * clip.duty) * period, clip)
                target = Vector((nx, ny, 0)) + Quaternion(UP, next_heading) @ anchor
                alpha = smooth(0, 1, swing)
                current = current.lerp(target, alpha)
                local_heading += (next_heading - step_heading) * alpha
                fore_scale = .72 if name == "Run" and side.startswith("F") else 1.0
                elevation = fore_scale * clip.lift * sin(pi * swing) ** 1.45
                roll = 8.5 * sin(2 * pi * swing) * sin(pi * swing)
                hock_flex = -9.8 * sin(pi * swing) ** 1.5
                is_plant = False
            else:
                fraction = phase / clip.duty
                roll = 4.0 * smooth(.85, .96, fraction) * (1 - smooth(.962, 1, fraction))
            placement = local(current.x, current.y, elevation)
            if turn_tuning:
                local_heading *= turn_tuning["footYaw"]
                roll *= turn_tuning["footRoll"]
                hock_flex *= .72
        else:
            local_heading = 0.0
        translate(spec["foot"], placement - sole)
        rotate(spec["foot"], yaw=local_heading * 180 / pi)
        if roll >= 0:
            rotate(spec["toe"], pitch=roll)
            rotate(spec["heelControl"])
        else:
            rotate(spec["toe"])
            rotate(spec["heelControl"], pitch=-roll)
        if len(spec["chain"]) == 5:
            delta = spec["ankle"] - spec["base"]
            translate(spec["endpoint"], Quaternion(SIDE, radians(hock_flex)) @ delta - delta)
        contacts[side] = {"locked": is_plant and abs(roll) < 0.15, "support": is_plant, "step": round(step, 4),
                          "target": (Quaternion(UP, heading) @ placement + Vector((x, y, 0))),
                          "lift": elevation}
    # In-place controls stay at the origin for the export. The placement path
    # is baked as separate metadata for Unity and in a Blender preview action.
    return (x, y, heading), contacts, shapes



def bind_foot_pads(pieces, specs):
    """Transfer rigid soles with a smooth transition into the existing ankle weights."""
    count = {}
    for side, spec in specs.items():
        mesh = pieces[spec["mesh"]]
        name = spec["chain"][-2]
        group = mesh.vertex_groups[name]
        count[side] = 0
        for vertex in mesh.data.vertices:
            if vertex.co.z >= .125:
                continue
            strength = 1.0 - smooth(.017, .125, vertex.co.z)
            old = [(item.group, item.weight) for item in vertex.groups]
            total = sum(w for _, w in old)
            if total <= 0:
                continue
            old_pad = sum(w for index, w in old if index == group.index)
            for index, weight in old:
                if index != group.index:
                    mesh.vertex_groups[index].add([vertex.index], weight * (1 - strength), "REPLACE")
            group.add([vertex.index], old_pad * (1 - strength) + strength * total, "REPLACE")
            count[side] += 1
    return count



def refine_leg_skin(pieces, specs):
    """Pin the lower belly to the trunk and smooth abrupt ankle weight changes."""
    changed = {}
    for name in ("Body", "HeadNeck", "Tail", *(spec["mesh"] for spec in specs.values())):
        obj = pieces[name]
        groups = {group.name: group for group in obj.vertex_groups}
        changed[name] = 0
        for vertex in obj.data.vertices:
            x, y, z = vertex.co
            belly = .90 * (1 - smooth(.13, .32, abs(x)))
            belly *= smooth(-.73, -.53, y) * (1 - smooth(.15, .32, y))
            belly *= smooth(.15, .25, z) * (1 - smooth(.45, .65, z))
            front = y < -.63
            side = ("F" if front else "H") + ("L" if x > 0 else "R")
            leg = specs[side]["chain"]
            lateral = smooth(.16, .27, abs(x))
            if front:
                limb = .86 * lateral * (1 - smooth(-.71, -.61, y)) * (1 - smooth(.20, .355, z))
                limb *= smooth(.040, .10, z)
                shin = smooth(.071, .222, z)
                upper = smooth(.235, .355, z)
                sleeve = {leg[2]: 1 - shin, leg[1]: shin * (1 - upper), leg[0]: shin * upper}
            else:
                limb = .80 * lateral * smooth(-.59, -.43, y) * (1 - smooth(.40, .59, z))
                limb *= smooth(.040, .105, z)
                hock = smooth(.075, .190, z)
                shin = smooth(.237, .448, z)
                upper = smooth(.365, .548, z)
                sleeve = {leg[3]: 1 - hock, leg[2]: hock * (1 - shin),
                          leg[1]: hock * shin * (1 - upper), leg[0]: hock * shin * upper}
            if max(belly, limb) < .006:
                continue
            carry = smooth(-.65, -.05, y)
            before = {obj.vertex_groups[g.group].name: g.weight for g in vertex.groups if g.weight > 0}
            total = sum(before.values())
            if not total:
                continue
            mix = {bone: weight * (1 - belly) for bone, weight in before.items()}
            for bone, weight in (("Bone_004", 1 - carry), ("Bone_001", carry)):
                mix[bone] = mix.get(bone, 0) + weight * belly * total
            for bone in mix:
                mix[bone] *= 1 - limb
            for bone, weight in sleeve.items():
                mix[bone] = mix.get(bone, 0) + weight * limb * total
            for bone, weight in mix.items():
                if bone not in groups:
                    if weight <= .00001:
                        continue
                    groups[bone] = obj.vertex_groups.new(name=bone)
                groups[bone].add([vertex.index], weight, "REPLACE")
            changed[name] += 1
    return changed


def bake(rig, pieces, specs, shape_targets):
    bpy.context.scene.render.fps = FPS
    bpy.context.scene.render.fps_base = 1
    deform = [bone for bone in rig.data.bones if bone.use_deform]
    source_animation = rig.animation_data_create()
    source_animation.use_nla = False
    baked = {}
    feature_frames = {}
    checks = {}
    preview = {}
    all_controls = [p for p in rig.pose.bones if p.name.startswith(PREFIX)]
    has_translation = {PREFIX + "Root", PREFIX + "Pelvis"}
    has_translation.update(spec["foot"] for spec in specs.values())
    has_translation.update(spec["endpoint"] for spec in specs.values() if len(spec["chain"]) == 5)
    has_rotation = {PREFIX + "Root", PREFIX + "Pelvis", *(PREFIX + label for label, _ in FK)}
    has_rotation.update(spec[key] for spec in specs.values() for key in ("foot", "toe", "heelControl"))
    for name, clip in CLIPS.items():
        action = bpy.data.actions.new(name + "_Controls")
        action.use_fake_user = True
        action.use_frame_range = True
        action.frame_start, action.frame_end = 1.0, float(clip.intervals + 1)
        action.use_cyclic = clip.cyclic
        source_animation.action = action
        if source_animation.action_slot is None:
            source_animation.action_slot = action.slots.new("OBJECT", rig.name)
        preview[name] = (action, source_animation.action_slot)
        baked[name] = []
        feature_frames[name] = {shape: [] for shape in shape_targets}
        samples = {side: [] for side in specs}
        previous_controls = {}
        for frame in range(clip.intervals + 1):
            bpy.context.scene.frame_set(frame + 1)
            root = rig.pose.bones[PREFIX + "Root"]
            root.location = (0, 0, 0)
            root.rotation_quaternion = Quaternion()
            root_xform, contacts, shapes = track(rig, specs, name, frame)
            rig.update_tag()
            bpy.context.view_layer.update()
            deps = bpy.context.evaluated_depsgraph_get()
            evaluated = rig.evaluated_get(deps).pose.bones
            matrices = {bone.name: evaluated[bone.name].matrix.copy() for bone in deform}
            row = {}
            for bone in deform:
                matrix = matrices[bone.name]
                parent = bone.parent
                if parent is not None and parent.use_deform:
                    row[bone.name] = bone.convert_local_to_pose(matrix, bone.matrix_local,
                        parent_matrix=matrices[parent.name], parent_matrix_local=parent.matrix_local, invert=True)
                else:
                    row[bone.name] = bone.convert_local_to_pose(matrix, bone.matrix_local, invert=True)
            baked[name].append(row)
            for shape, value in shapes.items():
                feature_frames[name][shape].append(round(value * 100, 3))

            x, y, heading = root_xform
            motion = Quaternion(UP, heading)
            for side, spec in specs.items():
                info = contacts[side]
                foot_obj = pieces[spec["mesh"]].evaluated_get(deps)
                sole = [foot_obj.data.vertices[i].co for i in spec["samples"]]
                min_z = min(v.z for v in sole)
                centroid = sum(sole, Vector()) / len(sole)
                marker = motion @ centroid + Vector((x, y, 0))
                chain_end = evaluated[spec["chain"][1]].tail
                endpoint = evaluated[spec["endpoint"]].head
                samples[side].append((info, min_z, marker, (chain_end - endpoint).length))
            basis = root.bone.matrix_local.to_3x3()
            root.location = basis.inverted() @ Vector((x, y, 0))
            root.rotation_quaternion = basis.to_quaternion().inverted() @ motion @ basis.to_quaternion()
            for control in all_controls:
                if control.name in has_translation:
                    control.keyframe_insert(data_path="location", frame=frame + 1, group=control.name)
                if control.name in has_rotation:
                    q = control.rotation_quaternion.copy()
                    if control.name in previous_controls:
                        q.make_compatible(previous_controls[control.name])
                    control.rotation_quaternion = q
                    previous_controls[control.name] = q.copy()
                    control.keyframe_insert(data_path="rotation_quaternion", frame=frame + 1, group=control.name)
                for prop in control.keys():
                    if prop.startswith("Shape_"):
                        control.keyframe_insert(data_path=f'["{prop}"]', frame=frame + 1, group=control.name)

        checks[name] = {}
        for side, values in samples.items():
            locked = defaultdict(list)
            for info, z, marker, error in values:
                if info["locked"]:
                    locked[info["step"]].append(marker)
            slip = max(((point - group[0]).length for group in locked.values() for point in group), default=0.0)
            plant_heights = [v[1] for v in values if v[0]["support"]]
            lifted = [v[1] for v in values if not v[0]["support"]]
            checks[name][side] = {
                "maxLockedFootDriftM": round(slip * BODY_SCALE, 5),
                "plantedSoleMinMaxM": [round(f(plant_heights) * BODY_SCALE, 5) for f in (min, max)],
                "maxSwingClearanceM": round(max(lifted, default=0) * BODY_SCALE, 5),
                "maxIkTargetResidualM": round(max(v[3] for v in values) * BODY_SCALE, 5),
                "supportFrames": sum(v[0]["support"] for v in values),
            }
        checks[name]["minimumSupportFeet"] = min(sum(row[0]["support"] for row in t) for t in zip(*samples.values()))
        if clip.cyclic:
            matrix_error = max(abs(a - b) for bone in deform for row_a, row_b in
                zip(baked[name][0][bone.name], baked[name][-1][bone.name]) for a, b in zip(row_a, row_b))
            checks[name]["loopEndMatrixError"] = round(matrix_error, 7)
        print(name, json.dumps(checks[name]), flush=True)
    return baked, preview, feature_frames, checks


def create_export_rig(rig, baked, preview, filename, meshes):
    """Strip source-only controllers and bake one transform action per Unity clip."""
    rig.animation_data.action = None
    for action, _ in preview.values():
        bpy.data.actions.remove(action)
    for curve in list(rig.animation_data.drivers):
        rig.driver_remove(curve.data_path)
    for bone in rig.pose.bones:
        for constraint in list(bone.constraints):
            bone.constraints.remove(constraint)
    for mesh in meshes:
        if mesh.data.shape_keys:
            mesh.data.shape_keys.animation_data_clear()
            for key in mesh.data.shape_keys.key_blocks:
                key.value = 0
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    edit = rig.data.edit_bones
    edit[PELVIS].parent = None
    for control in [bone for bone in edit if not bone.use_deform]:
        edit.remove(control)
    bpy.ops.object.mode_set(mode="OBJECT")

    for name, frames in baked.items():
        action = bpy.data.actions.new(name)
        action.use_fake_user = True
        rig.animation_data.action = action
        rig.animation_data.action_slot = action.slots.new("OBJECT", rig.name)
        last = {}
        for index, pose in enumerate(frames, 1):
            for bone in rig.pose.bones:
                location, quaternion, scale = pose[bone.name].decompose()
                if bone.name in last:
                    quaternion.make_compatible(last[bone.name])
                last[bone.name] = quaternion.copy()
                bone.location = location
                bone.rotation_quaternion = quaternion
                bone.scale = scale
                for prop in ("location", "rotation_quaternion", "scale"):
                    bone.keyframe_insert(data_path=prop, frame=index, group=bone.name)
        for curve in action.layers[0].strips[0].channelbag(rig.animation_data.action_slot).fcurves:
            for key in curve.keyframe_points:
                key.interpolation = "LINEAR"
    rig.animation_data.action = None
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    export_fbx(filename, [rig, *meshes], animated=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--audit-only", action="store_true")
    arguments = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    source = arguments.source.resolve(strict=True)
    project = arguments.project.resolve(strict=True)
    bpy.ops.wm.open_mainfile(filepath=str(source))
    rig = bpy.data.objects["SmartRigArmature"]
    assert len(rig.data.bones) == 36 and rig.data.bones[JAW].parent.name == "Bone_022"
    rig.animation_data_clear()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    rig.data.pose_position = "POSE"
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    pieces = {obj.name: obj for obj in bpy.context.scene.objects if obj.type == "MESH" and
              any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers)}
    assert len(pieces) == 25
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and obj.name not in pieces:
            obj.hide_render = True
            obj.hide_set(True)
    rig.show_in_front = True
    rig.data.display_type = "STICK"
    specs = create_controls(rig, pieces)
    pad_count = bind_foot_pads(pieces, specs)
    skin_count = refine_leg_skin(pieces, specs)
    section_check = topology_sections(list(pieces.values()), specs, rig)
    print("Topology sections", json.dumps(section_check), flush=True)
    print("Foot calibration", json.dumps({k: {"error": v["kneeError"], "pole": v["poleAngle"]} for k, v in specs.items()}), flush=True)
    shapes = make_shapes(rig, pieces)
    baked, preview, feature_frames, checks = bake(rig, pieces, specs, shapes)

    metrics = {
        "source": source.name,
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "blender": bpy.app.version_string,
        "frameRate": FPS,
        "deformBones": sum(b.use_deform for b in rig.data.bones),
        "controls": sum(not b.use_deform for b in rig.data.bones),
        "skinnedPieces": len(pieces),
        "footPadVertexCounts": pad_count,
        "legSkinVertexCounts": skin_count,
        "footPoleAngleDegrees": {side: round(spec["poleAngle"] * 180 / pi, 2) for side, spec in specs.items()},
        "topology": {"meshType": "triangulated", "triangles": sum(len(obj.data.polygons) for obj in pieces.values()),
                     "jointSectionProbe": section_check},
        "clipChecks": checks,
    }
    evidence = project / "Evidence" / "creature-rig-blender.json"
    evidence.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if arguments.audit_only:
        return

    # Save the hand-editable control actions with moving Blender root.
    preview_action, slot = preview["Walk"]
    rig.animation_data.action = preview_action
    rig.animation_data.action_slot = slot
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = CLIPS["Walk"].intervals + 1
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type == "VIEW_3D":
            view = area.spaces.active.region_3d
            view.view_rotation = Vector((4.5, -6, 2.5)).to_track_quat("Z", "Y")
            view.view_distance = 4.9
            view.view_location = Vector((0, 0, .69))
            area.spaces.active.shading.type = "MATERIAL"
    path_blend = project / "Source" / "Stegosaur-Creature-Rig.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path_blend), compress=True)

    fbx = project / "Assets" / "Source" / "Dinosaur" / "CreatureStegosaur.fbx"
    create_export_rig(rig, baked, preview, fbx, list(pieces.values()))
    motion_data = frame_data()
    for clip in motion_data["clips"]:
        clip["shapes"] = [{"name": name, "values": values} for name, values in feature_frames[clip["name"]].items()]
    for side, spec in specs.items():
        motion_data.setdefault("feet", []).append({"id": side, "mesh": spec["mesh"], "bone": spec["chain"][-2],
                                                     "x": -spec["sole"].x * BODY_SCALE, "z": -spec["sole"].y * BODY_SCALE})
    path_manifest = project / "Assets" / "Source" / "Dinosaur" / "CreatureMotion.json"
    path_manifest.write_text(json.dumps(motion_data, separators=(",", ":")) + "\n", encoding="utf-8")
    metrics["fbxSha256"] = hashlib.sha256(fbx.read_bytes()).hexdigest()
    metrics["blendSha256"] = hashlib.sha256(path_blend.read_bytes()).hexdigest()
    evidence.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
