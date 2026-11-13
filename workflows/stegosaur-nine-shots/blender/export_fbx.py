"""Blender image pixels and FBX export settings shared by the preset tools."""
import struct

import bpy
import numpy as np


def pixels(image):
    width, height = image.size[:]
    if width < 1 or height < 1:
        raise ValueError(f"Could not load {image.name}")
    values = np.empty((height, width, 4), dtype=np.float32)
    image.pixels.foreach_get(values.ravel())
    return values


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
