"""Retune the two control actions with shorter steps and a shallower turn posture."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

import bpy
from mathutils import Quaternion, Vector

sys.path.insert(0,str(Path(__file__).resolve().parent))
from build_creature_rig import LEGS, track
from creature_motion import CLIPS, FPS, BODY_SCALE


def retune(rig, motion, speed, period, drop, bias, yaw, roll, lift):
    saved = dict(CLIPS)
    tuning = {"drop":drop,"bias":bias,"footYaw":yaw,"footRoll":roll}
    scene = bpy.context.scene
    anim = rig.animation_data
    anim.use_nla = False
    specs = {}
    bones = rig.data.bones
    for side,(_, *chain) in LEGS.items():
        foot = "CTRL_Foot." + side
        specs[side] = {"chain":chain,"sole":bones[foot].head_local.copy(),
                       "ankle":bones[chain[2]].head_local.copy(),"base":bones[chain[-2]].head_local.copy(),
                       "foot":foot,"toe":"CTRL_Toe."+side,"heelControl":"CTRL_Heel."+side,
                       "endpoint":"CTRL_Hock."+side if len(chain)==5 else "CTRL_FootTarget."+side}
    controls = [p for p in rig.pose.bones if p.name.startswith("CTRL_")]
    reports = {}
    for row in motion["clips"]:
        name = row["name"]
        if name not in ("TurnLeft","TurnRight"):
            continue
        clip = CLIPS[name] = replace(saved[name],speed=speed,period=period,lift=lift)
        row["speed"] = speed*BODY_SCALE
        old = bpy.data.actions[name+"_Controls"]
        anim.action = None
        bpy.data.actions.remove(old)
        action = bpy.data.actions.new(name+"_Controls")
        action.use_fake_user = True
        action.use_frame_range = True
        action.frame_start,action.frame_end = 1.,float(clip.intervals+1)
        anim.action = action
        anim.action_slot = action.slots.new("OBJECT",rig.name)
        shapes = {shape["name"]:shape["values"] for shape in row["shapes"]}
        previous = {}
        anchors = {name:{} for name in specs}
        for index in range(clip.intervals+1):
            scene.frame_set(index+1)
            root = rig.pose.bones["CTRL_Root"]
            root.location = (0,0,0)
            root.rotation_quaternion = Quaternion()
            (x,y,heading), contacts, values = track(rig,specs,name,index,turn_tuning=tuning)
            basis = root.bone.matrix_local.to_3x3()
            root.location = basis.inverted() @ Vector((x,y,0))
            root.rotation_quaternion = basis.to_quaternion().inverted() @ Quaternion((0,0,1),heading) @ basis.to_quaternion()
            for side,item in contacts.items():
                if item["locked"]:
                    anchors[side].setdefault(item["step"],[]).append(item["target"])
            for feature,value in values.items():
                shapes[feature][index] = round(value*100,3)
            for control in controls:
                q = control.rotation_quaternion.copy()
                if control.name in previous:
                    q.make_compatible(previous[control.name])
                control.rotation_quaternion = q
                previous[control.name] = q.copy()
                control.keyframe_insert(data_path="location",frame=index+1,group=control.name)
                control.keyframe_insert(data_path="rotation_quaternion",frame=index+1,group=control.name)
                for prop in control.keys():
                    if prop.startswith("Shape_"):
                        control.keyframe_insert(data_path=f'["{prop}"]',frame=index+1,group=control.name)
        for curve in action.layers[0].strips[0].channelbag(anim.action_slot).fcurves:
            for key in curve.keyframe_points:
                key.interpolation = "LINEAR"
        reports[name] = {side:max(((p-items[0]).length*BODY_SCALE for items in values.values() for p in items),default=0.)
                         for side,values in anchors.items()}
        CLIPS[name] = saved[name]
        print(name,"planted target motion in world metres",reports[name],flush=True)
    action = bpy.data.actions["Idle_Controls"]
    anim.action = action
    anim.action_slot = action.slots[0]
    scene.frame_set(1)
    return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--motion",required=True,type=Path)
    parser.add_argument("--new-motion",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--speed",type=float,default=.19)
    parser.add_argument("--period",type=int,default=24)
    parser.add_argument("--drop",type=float,default=-.037)
    parser.add_argument("--bias",type=float,default=.018)
    parser.add_argument("--yaw",type=float,default=.72)
    parser.add_argument("--roll",type=float,default=.60)
    parser.add_argument("--lift",type=float,default=.071)
    opt = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    motion = json.loads(opt.motion.read_text(encoding="utf-8-sig"))
    rig = bpy.data.objects["SmartRigArmature"]
    retune(rig,motion,opt.speed,opt.period,opt.drop,opt.bias,opt.yaw,opt.roll,opt.lift)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=str(opt.output.resolve()),compress=True)
    opt.new_motion.write_text(json.dumps(motion,separators=(",",":"))+"\n",encoding="utf-8")


if __name__ == "__main__":
    main()
