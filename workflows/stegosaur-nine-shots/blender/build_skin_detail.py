"""Bake repeatable scale detail and a shared facial mask from the editable quad model."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refine_stegosaur import smooth
from refine_stegosaur_atlas import rasterize, SIDE


def write_texture(name, data, path):
    h, w = data.shape[:2]
    image = bpy.data.images.new(name, w, h, alpha=True, is_data=True)
    image.alpha_mode = "STRAIGHT"
    image.pixels.foreach_set(data.astype(np.float32).ravel())
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def spot(y, z, yc, zc, ry, rz):
    return 1. - smooth(.23, 1., np.sqrt(((y-yc)/ry)**2 + ((z-zc)/rz)**2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    opts = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    folder = opts.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)

    side = 512
    columns, rows = 9, 8
    rng = np.random.default_rng(37)
    centers = rng.uniform(-.17, .17, (rows, columns, 2))
    grad = rng.uniform(.73, 1.08, (rows, columns))
    uu, vv = np.meshgrid((np.arange(side)+.5)/side*columns, (np.arange(side)+.5)/side*rows)
    col, row = np.floor(uu).astype(int), np.floor(vv).astype(int)
    first = np.full((side, side), 1e9)
    second = first.copy()
    character = np.ones_like(first)
    for off_row in (-1, 0, 1):
        for off_col in (-1, 0, 1):
            c, r = col+off_col, row+off_row
            point = centers[r%rows, c%columns]
            cy = r + .5 + point[:,:,1]
            cx = c + .5 + .44 * (r % 2) + point[:,:,0]
            distance = ((uu-cx)*.91)**2 + ((vv-cy)*1.08)**2
            second = np.minimum(second, np.maximum(first, distance))
            take = distance < first
            character[take] = grad[r%rows, c%columns][take]
            first = np.minimum(first, distance)
    cavity = 1. - smooth(.009, .132, np.sqrt(second) - np.sqrt(first))
    micro = (np.sin(2*np.pi*18*uu/columns + .7*np.sin(2*np.pi*25*vv/rows))
             * np.sin(2*np.pi*35*vv/rows + .55*np.sin(2*np.pi*27*uu/columns)))
    height = (1. - smooth(0., .68, np.sqrt(first))) * character * .44 - .13*cavity + .032*micro
    dx = (np.roll(height,-1,axis=1)-np.roll(height,1,axis=1))*3.15
    dy = (np.roll(height,-1,axis=0)-np.roll(height,1,axis=0))*3.15
    normal = np.stack((-dx,-dy,np.ones_like(dx)),axis=2)
    normal /= np.linalg.norm(normal,axis=2,keepdims=True)
    detail = np.dstack((.5+.5*normal[:,:,:2], cavity, np.ones_like(dx)))
    write_texture("Scales", detail, folder/"SkinDetail.png")

    # Rasterize both halves of the existing 4096 x 2048 atlas independently
    # at half resolution. Values follow model coordinates across UV charts.
    coordinates, texcoords = [], []
    for part in bpy.data.objects["SmartRigArmature"].children:
        if part.type != "MESH" or part.name not in ("HeadNeck", "MouthInterior"):
            continue
        mesh = part.data
        mesh.calc_loop_triangles()
        points = np.asarray([v.co[:] for v in mesh.vertices])
        uvs = np.asarray([loop.uv[:] for loop in mesh.uv_layers.active.data])
        for tri in mesh.loop_triangles:
            name = mesh.materials[mesh.polygons[tri.polygon_index].material_index].name
            if name in ("BakedMaterial", "LipTransition"):
                coordinates.append(points[np.asarray(tri.vertices[:])])
                texcoords.append(uvs[np.asarray(tri.loops[:])])
    coords, uv = np.asarray(coordinates), np.asarray(texcoords)
    mask_halves, active = [], []
    face_side = 1024
    # Reuse the production rasterizer at its declared resolution, then sample
    # alternate texels to produce the 2K shared map with adequate padding.
    for half in (0, 1):
        chosen = (uv[:, :, 0].mean(axis=1) > .5) == bool(half)
        chart = uv[chosen].copy()
        chart[:, :, 0] = chart[:, :, 0]*2. - half
        owner, interpolation = rasterize(chart)
        owner = owner[::2,::2].copy()
        interpolation = interpolation[::2,::2]
        valid = owner >= 0
        index = owner[valid]
        u, v = interpolation[valid].T
        pos = ((1.-u-v)[:, None]*coords[chosen][index, 0]
               + u[:, None]*coords[chosen][index, 1] + v[:, None]*coords[chosen][index, 2])
        x, y, z = pos.T
        lateral = np.abs(x)
        # Use the mesh's visible iris and cut mouth coordinates. Coat stays
        # localized, and the thickness effect is confined to thin skin.
        eye = spot(y, z, -1.501, .864, .039, .037) * smooth(.086, .121, lateral)
        nostril = spot(y,z,-1.624,.817,.029,.023) * smooth(.034,.064,lateral)
        profile = np.interp(-y,[1.515,1.545,1.560,1.585,1.615,1.645,1.672,1.699],
                            [.781,.774,.758,.746,.735,.727,.721,.722])
        lip = (1.-smooth(.002,.012,np.abs(z-profile))) * smooth(1.525,1.591,-y)
        wet = np.maximum.reduce((eye*.86,nostril*.48,lip*.68))
        thin = np.maximum.reduce((eye*.26,nostril*.58,lip*.55,
            spot(y,z,-1.365,.672,.153,.144)*.46))
        throat = spot(y,z,-1.368,.637,.175,.15) * (1.-smooth(.153,.256,lateral))
        cheek = spot(y,z,-1.490,.766,.099,.056) * smooth(.105,.164,lateral)
        wrinkle = np.maximum(throat, cheek*.67) * (1.-wet)
        mask = np.zeros((face_side, face_side, 4), dtype=np.float32)
        mask[:,:,:3][valid] = np.stack((wet,thin,wrinkle),axis=1)
        mask[:,:,3] = 1.
        donors = np.where(valid, np.arange(face_side**2).reshape((face_side,face_side)), -1)
        for _ in range(8):
            previous = donors.copy()
            for a,b in ((np.s_[1:,:],np.s_[:-1,:]),(np.s_[:-1,:],np.s_[1:,:]),
                        (np.s_[:,1:],np.s_[:,:-1]),(np.s_[:,:-1],np.s_[:,1:])):
                window = donors[a]
                take = (window<0)&(previous[b]>=0)
                window[take] = previous[b][take]
        padding = (~valid)&(donors>=0)
        mask[padding] = mask.reshape((-1,4))[donors[padding]]
        mask_halves.append(mask)
        active.append(int(np.count_nonzero(wet>.1)))
    write_texture("Facial mask", np.concatenate(mask_halves, axis=1), folder/"SkinRegions.png")
    report = dict(detailResolution=[side,side],detailChannels=["normal X","normal Y","cavity"],
                  maskResolution=[2048,1024],maskChannels=["wetness","thin tissue","wrinkle"],
                  wetMappedTexels=active,detailTileCells=[columns,rows],seed=37,
                  skinAtlasRasterResolution=SIDE, colorSpace="linear")
    opts.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
