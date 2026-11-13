"""Measure front-leg skin distortion at the authored pose of every camera frame."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from check_quad_stegosaur import coordinates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["FrontLeftLeg", "FrontRightLeg"], default="FrontLeftLeg")
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    opt = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    motion = json.loads(opt.motion.read_text(encoding="utf-8-sig"))
    clips = {row["name"]: row for row in motion["clips"]}
    scene = bpy.context.scene
    rig = bpy.data.objects["SmartRigArmature"]
    rig.animation_data.use_nla = False
    part = bpy.data.objects[opt.part]
    mesh = part.data
    mesh.calc_loop_triangles()
    scale = motion["assetLength"] / 3.82
    tris = np.array([t.vertices[:] for t in mesh.loop_triangles])
    points = coordinates(mesh).astype(np.float64)
    rest = points[tris]
    area = .5 * scale**2 * np.linalg.norm(np.cross(rest[:, 1] - rest[:, 0], rest[:, 2] - rest[:, 0]), axis=1)
    centers = rest.mean(axis=1)
    # Include all edges of the exported triangles, including both mesh and triangulation edges.
    edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1), axis=0)
    length = scale * np.linalg.norm(points[edges[:, 1]] - points[edges[:, 0]], axis=1)
    edge_centers = points[edges].mean(axis=1)
    ids = np.array([a.value for a in mesh.attributes["CageVertex"].data])
    labels = [group.name for group in part.vertex_groups]

    def vertex_details(indices):
        return [
            {"id": int(ids[i]), "co": points[i].tolist(),
             "weights": {labels[g.group]: g.weight for g in mesh.vertices[i].groups if g.weight > .001}}
            for i in indices
        ]

    report = {"scale": scale, "part": part.name, "shots": {}}
    start = 0
    for shot in motion["shots"]:
        clip = clips[shot["clip"]]
        action = bpy.data.actions[clip["name"] + "_Controls"]
        rig.animation_data.action = action
        rig.animation_data.action_slot = action.slots[0]
        ratios, lengths = [], []
        for frame in range(shot["frames"]):
            sample = frame % clip["intervals"] if clip["cyclic"] else min(frame, clip["intervals"])
            scene.frame_set(sample + 1)
            vertices = coordinates(part.evaluated_get(bpy.context.evaluated_depsgraph_get()).data).astype(np.float64)
            pose = vertices[tris]
            deformed_area = .5 * scale**2 * np.linalg.norm(np.cross(pose[:, 1] - pose[:, 0], pose[:, 2] - pose[:, 0]), axis=1)
            ratios.append(deformed_area / np.maximum(area, 1e-20))
            lengths.append(scale * np.linalg.norm(vertices[edges[:, 1]] - vertices[edges[:, 0]], axis=1))
        grid = np.stack(ratios)
        grid[:, area < 1e-7] = 0
        stretch = np.stack(lengths) - length
        over4 = grid > 4.
        tops = np.argsort(grid.max(axis=0))[-5:][::-1]
        edge_tops = np.argsort(stretch.max(axis=0))[-5:][::-1]
        maximum = [
            {"timelineFrame": int(grid[:, i].argmax()) + start, "ratio": float(grid[:, i].max()),
             "restAreaCm2": float(area[i] * 1e4), "center": centers[i].tolist(), "vertices": vertex_details(tris[i])}
            for i in tops
        ]
        edge_maximum = [
            {"timelineFrame": int(stretch[:, i].argmax()) + start, "stretchMm": float(stretch[:, i].max() * 1e3),
             "restLengthMm": float(length[i] * 1e3), "center": edge_centers[i].tolist(), "vertices": vertex_details(edges[i])}
            for i in edge_tops
        ]
        row = {
            "frames": shot["frames"], "areaOver2": int((grid > 2.).sum()), "areaOver4": int(over4.sum()),
            "areaOver4CumulativeRestCm2": float(over4.sum(axis=0) @ area * 1e4),
            "worstArea": maximum, "worstEdge": edge_maximum,
            "distalEdgePeakStretchMm": float(stretch[:, edge_centers[:, 2] < .30].max() * 1e3),
            "distalEdgesOver8mm": int((stretch[:, edge_centers[:, 2] < .30] > .008).sum()),
            "inspectionFrames": [
                {"timelineFrame": frame, "peakAreaRatio": float(grid[frame - start].max()),
                 "areaOver4": int(over4[frame - start].sum()),
                 "peakDistalEdgeStretchMm": float(stretch[frame - start, edge_centers[:, 2] < .30].max() * 1e3)}
                for frame in (206, 211, 216, 222, 229) if start <= frame < start + shot["frames"]
            ],
            "largestBandAreaOver2": [
                {"z": round(float(z), 2), "hits": int((grid[:, (centers[:, 2] >= z) & (centers[:, 2] < z + .1)] > 2.).sum())}
                for z in np.arange(0., 1., .1)
            ],
        }
        report["shots"][shot["id"]] = row
        print(shot["id"], "area >2/>4:", row["areaOver2"], row["areaOver4"],
              "peak frame/ratio:", maximum[0]["timelineFrame"], round(maximum[0]["ratio"], 2),
              "peak distal edge mm:", round(row["distalEdgePeakStretchMm"], 2), flush=True)
        start += shot["frames"]
    opt.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
