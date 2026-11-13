"""Bake the source creature controls to the common Unity and Three.js deform rig."""
import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_fbx import export_fbx


def capture(rig, clips):
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.0
    anim = rig.animation_data
    anim.use_nla = False
    deform = [bone for bone in rig.data.bones if bone.use_deform]
    bake = {}
    for clip in clips:
        name = clip["name"]
        action = bpy.data.actions[name + "_Controls"]
        anim.action = action
        anim.action_slot = action.slots[0]
        frames = []
        for i in range(clip["intervals"]+1):
            scene.frame_set(i+1)
            root = rig.pose.bones["CTRL_Root"]
            root.location = (0,0,0)
            root.rotation_quaternion = Quaternion()
            rig.update_tag()
            bpy.context.view_layer.update()
            pose = rig.evaluated_get(bpy.context.evaluated_depsgraph_get()).pose.bones
            matrices = {b.name:pose[b.name].matrix.copy() for b in deform}
            basis = {}
            for bone in deform:
                parent = bone.parent
                args = {}
                if parent and parent.use_deform:
                    args.update(parent_matrix=matrices[parent.name], parent_matrix_local=parent.matrix_local)
                basis[bone.name] = bone.convert_local_to_pose(
                    matrices[bone.name], bone.matrix_local, invert=True, **args)
            frames.append(basis)
        bake[name] = frames
        print("Captured", name, len(frames), flush=True)
    anim.action = None
    return bake


def remove_controls(rig, meshes):
    for action in list(bpy.data.actions):
        if action.name.endswith("_Controls"):
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
    for bone in list(edit):
        if bone.use_deform and bone.parent and not bone.parent.use_deform:
            bone.parent = None
    for control in [bone for bone in edit if not bone.use_deform]:
        edit.remove(control)
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in rig.pose.bones:
        bone.rotation_mode = "QUATERNION"


def set_linear(action, slot):
    for curve in action.layers[0].strips[0].channelbag(slot).fcurves:
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"


def make_actions(rig, meshes, clips, baked):
    anim = rig.animation_data
    actions = {}
    for clip in clips:
        name = clip["name"]
        action = bpy.data.actions.new(name)
        action.use_fake_user = True
        anim.action = action
        anim.action_slot = action.slots.new("OBJECT", rig.name)
        actions[name] = (action,anim.action_slot)
        last = {}
        for index, pose in enumerate(baked[name],1):
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
        set_linear(action, anim.action_slot)
        print("Prepared",name,len(baked[name]),"frames",flush=True)
    anim.action = None
    for bone in rig.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)

    morphs = {}
    for mesh in meshes:
        keys = mesh.data.shape_keys
        if keys is None:
            continue
        ad = keys.animation_data_create()
        morphs[mesh.name] = (keys,{})
        for clip in clips:
            name = clip["name"]
            action = bpy.data.actions.new(mesh.name + "_" + name + "_morphs")
            ad.action = action
            ad.action_slot = action.slots.new("KEY", keys.name)
            morphs[mesh.name][1][name] = (action,ad.action_slot)
            for curve in clip["shapes"]:
                key = keys.key_blocks.get(curve["name"])
                if key:
                    for index, value in enumerate(curve["values"],1):
                        key.value = value/100
                        key.keyframe_insert(data_path="value", frame=index)
            set_linear(action, ad.action_slot)
        ad.action = None
        for key in keys.key_blocks:
            key.value = 0
    return actions,morphs


def nla(target, actions):
    ad = target.animation_data
    ad.action = None
    ad.use_nla = True
    for name,(action,slot) in actions.items():
        track = ad.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name,1,action)
        strip.action_slot = slot
        strip.extrapolation = "NOTHING"
        track.mute = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--motion",required=True,type=Path)
    parser.add_argument("--fbx",type=Path)
    parser.add_argument("--glb",type=Path)
    opt = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    clips = json.loads(opt.motion.read_text(encoding="utf-8-sig"))["clips"]
    rig = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    meshes = sorted([o for o in bpy.context.scene.objects if o.type == "MESH" and o.parent == rig],
                    key=lambda o:o.name)
    baked = capture(rig, clips)
    remove_controls(rig, meshes)
    actions,morphs = make_actions(rig,meshes,clips,baked)
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    if opt.glb:
        opt.glb.parent.mkdir(parents=True,exist_ok=True)
        nla(rig, actions)
        for keys,tracks in morphs.values():
            nla(keys, tracks)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in (rig,*meshes):
            obj.select_set(True)
        result = bpy.ops.export_scene.gltf(
            filepath=str(opt.glb.resolve()), export_format="GLB", use_selection=True,
            export_animation_mode="NLA_TRACKS", export_anim_slide_to_zero=True,
            export_skins=True, export_morph=True, export_morph_animation=True,
            export_all_influences=True, export_apply=False, export_tangents=True, export_yup=True)
        if result != {"FINISHED"} or not opt.glb.is_file():
            raise RuntimeError(f"glTF export failed: {result}")
        for target in (rig,*(row[0] for row in morphs.values())):
            ad = target.animation_data
            for track in list(ad.nla_tracks):
                ad.nla_tracks.remove(track)
            ad.use_nla = False
    if opt.fbx:
        for keys,_ in morphs.values():
            keys.animation_data_clear()
            for key in keys.key_blocks:
                key.value = 0
        bpy.context.scene.frame_set(1)
        bpy.context.view_layer.update()
        export_fbx(opt.fbx.resolve(),[rig,*meshes],animated=True)
    print("Export finished",flush=True)


if __name__ == "__main__":
    main()
