"""Build, render and verify the Stegosaur nine-shot preset with local assets."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from delivery import (
    CUTS, FPS, FRAMES, REPORTS, SHOTS, SIZE, SMOKE_SHOTS,
    check_capture, conform, digest, read_json, write_json,
)

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "project"
MOTION = Path("Assets/Source/Dinosaur/CreatureMotion.json")
SCENE = Path("Assets/Source/asset-manifest.json")
SKIN = ("SkinDetail.png", "SkinRegions.png")
WEB_ASSETS = (
    "stegosaur-quad30k.glb", "forest.hdr", "ground-diff.jpg", "ground-nor_gl.jpg", "ground-rough.jpg",
    "tree_small_02_textured.glb", "pine_sapling_small_textured.glb", "tree_stump_01_textured.glb",
    "shrub_sorrel_01_textured.glb", "shrub_02_a_textured.glb", "shrub_02_b_textured.glb",
    "shrub_03_a_textured.glb", "shrub_03_b_textured.glb", *SKIN,
)
UNITY_VERSION = "6000.3.28f1"
HDRP = "com.unity.render-pipelines.high-definition"
HDRP_VERSION = "17.3.0"


def run(args, cwd):
    print("> " + subprocess.list2cmdline([str(arg) for arg in args]), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def assets_for(engines):
    paths = set()
    if "unity" in engines:
        scene = read_json(TEMPLATE / SCENE)
        materials = [surface for model in scene["models"] for surface in model["materials"]]
        for surface in [*materials, scene["ground"]]:
            paths.update(Path(surface[key]) for key in ("albedo", "normal", "packedMetallicGloss")
                         if surface.get(key))
        paths.update(Path(model["fbx"]) for model in scene["models"])
        paths.add(Path(scene["sky"]))
        paths.update(Path("Assets/Source/Textures/StegosaurSkin") / name for name in SKIN)
    if "three" in engines:
        paths.update(Path("Web/assets") / name for name in WEB_ASSETS)
    return sorted(paths)


def get_capture_dirs(opt, parser):
    captures = {"unity": opt.unity_capture, "three": opt.three_capture}
    if opt.stage == "conform":
        captures = {engine: path.resolve(strict=True) for engine, path in captures.items() if path is not None}
        if not captures or opt.engine is not None:
            parser.error("conform selects engines with --unity-capture and/or --three-capture.")
        return captures
    if any(captures.values()):
        parser.error("--unity-capture and --three-capture are only for conform.")
    engines = ("unity", "three") if opt.engine in (None, "both") else (opt.engine,)
    return dict.fromkeys(engines)


def preflight(opt, engines):
    rendering = opt.stage in ("check", "smoke", "render")
    unity = rendering and "unity" in engines
    browser = (rendering and "three" in engines) or opt.stage in ("check", "render", "conform")
    tools = {}
    needed = []
    if browser:
        needed.extend(("node", "npm.cmd" if os.name == "nt" else "npm"))
    if opt.stage in ("check", "render", "conform"):
        needed.extend(("ffmpeg", "ffprobe"))
    if unity:
        if os.name != "nt":
            raise ValueError("The Unity capture adapter uses Windows Direct3D 11.")
    for name in needed:
        command = shutil.which(name)
        if not command:
            raise FileNotFoundError(f"Required executable is missing from PATH: {name}")
        tools[name.removesuffix(".cmd").removesuffix(".exe")] = command
    if browser:
        if opt.chrome is None or not opt.chrome.is_file():
            raise FileNotFoundError("Pass the installed Chrome executable with --chrome.")
        tools["chrome"] = opt.chrome.resolve()
    if unity:
        if opt.unity is None or not opt.unity.is_file():
            raise FileNotFoundError(f"Pass the Unity {UNITY_VERSION} executable with --unity.")
        tools["unity"] = opt.unity.resolve()
        hdrp = tools["unity"].parent / "Data/Resources/PackageManager/BuiltInPackages" / HDRP
        version = read_json(hdrp / "package.json")["version"]
        if version != HDRP_VERSION:
            raise ValueError(f"HDRP {HDRP_VERSION} is required, found {version} in {hdrp}.")
        tools["hdrp"] = hdrp

    paths = assets_for(engines) if rendering else []
    if rendering:
        if opt.assets is None or not opt.assets.is_dir():
            raise FileNotFoundError("Pass the unpacked asset root with --assets.")
        missing = [path.as_posix() for path in paths if not (opt.assets / path).is_file()]
        if missing:
            raise FileNotFoundError("Missing input files:\n  " + "\n  ".join(missing))
    audio = opt.audio
    if audio is None and opt.assets is not None:
        audio = opt.assets / "Web/assets/roar.m4a"
    if opt.stage != "smoke" or "three" in engines:
        if audio is None or not audio.is_file():
            raise FileNotFoundError("Supply the original AAC with --audio or Web/assets/roar.m4a under --assets.")
        audio = audio.resolve()
    return tools, paths, audio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("check", "smoke", "render", "conform"))
    parser.add_argument("--engine", choices=("both", "unity", "three"))
    parser.add_argument("--assets", type=Path, help="Unpacked model and forest asset root")
    parser.add_argument("--output", type=Path, help="New run directory")
    parser.add_argument("--unity", type=Path, help=f"Unity {UNITY_VERSION} executable")
    parser.add_argument("--chrome", type=Path, help="Chrome executable for capture or MP4 playback")
    parser.add_argument("--audio", type=Path, help="Existing 28.166667 s 48 kHz mono AAC source")
    parser.add_argument("--unity-capture", type=Path, help="Existing full Unity capture for conform")
    parser.add_argument("--three-capture", type=Path, help="Existing full Three.js capture for conform")
    opt = parser.parse_args()
    if opt.stage != "check" and opt.output is None:
        parser.error("--output is required for smoke, render and conform.")
    captures = get_capture_dirs(opt, parser)
    if opt.output is not None and opt.output.exists():
        raise FileExistsError(f"Use a new output directory: {opt.output}")
    tools, assets, audio = preflight(opt, captures)
    motion = read_json(TEMPLATE / MOTION)
    if (motion["fps"], tuple((shot["id"], shot["frames"]) for shot in motion["shots"])) != (FPS, SHOTS):
        raise ValueError("The shared motion JSON does not match the nine-shot preset.")
    if opt.stage == "check":
        print(f"Preflight PASS. Checked {len(assets)} asset files and the local tool entry points.")
        return
    if opt.stage == "conform":
        for engine, capture in captures.items():
            check_capture(engine, capture)

    output = opt.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    unity = opt.stage != "conform" and "unity" in captures
    if unity:
        for name in ("Assets", "Packages", "ProjectSettings", "Tools"):
            shutil.copytree(TEMPLATE / name, project / name)
    web = opt.stage != "smoke" or "three" in captures
    if web:
        shutil.copytree(TEMPLATE / "Web", project / "Web")
    source_models = {}
    for path in assets:
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(opt.assets / path, target)
        if (path.suffix == ".fbx" and path.parent == MOTION.parent) or path.name == WEB_ASSETS[0]:
            source_models[path.name] = digest(target)
    if web and opt.stage != "conform":
        shutil.copyfile(TEMPLATE / MOTION, project / "Web/motion.json")
        if audio is not None:
            (project / "Web/assets").mkdir(exist_ok=True)
            shutil.copyfile(audio, project / "Web/assets/roar.m4a")
    if web:
        run([tools["npm"], "ci", "--ignore-scripts", "--no-audit", "--no-fund"], project / "Web")
    if opt.stage in ("smoke", "render") and "three" in captures:
        loader = project / "Web/node_modules/three/examples/jsm/loaders/GLTFLoader.js"
        source = loader.read_text(encoding="utf-8")
        normalization = "mesh.normalizeSkinWeights();"
        if source.count(normalization) != 1:
            raise ValueError("Review the pinned GLTFLoader before changing its eight-weight skinning behavior.")
        # The stock loader normalizes WEIGHTS_0 alone. Both sets already sum to one in our GLB.
        guarded = f"if ( ! geometry.hasAttribute( 'weights_1' ) ) {normalization}"
        loader.write_text(source.replace(normalization, guarded), encoding="utf-8", newline="\n")

    if opt.stage in ("smoke", "render"):
        for engine in captures:
            captures[engine] = output / f"{engine}-capture"
        smoke = opt.stage == "smoke"
        if unity:
            run([sys.executable, project / "Tools/build_hdrp_skin_shader.py", tools["hdrp"]], project)
            capture = captures["unity"]
            capture.mkdir()
            args = [tools["unity"], "-batchmode", "-quit", "-force-d3d11", "-projectPath", project,
                    "-executeMethod", "Dinosaug.CreatureRigStudio.RenderBatch", "-creature-output", capture,
                    "-creature-width", str(SIZE[0]), "-creature-height", str(SIZE[1]), "-creature-rebuild",
                    "-logFile", capture / "editor.log"]
            if smoke:
                args.extend(("-creature-smoke", "-creature-shots", ",".join(SMOKE_SHOTS)))
            run(args, project)
            report_path, _ = check_capture("unity", capture, smoke=smoke)
            if read_json(report_path)["unityVersion"] != UNITY_VERSION:
                raise ValueError(f"Expected Unity {UNITY_VERSION}. See {report_path}.")
        if "three" in captures:
            capture = captures["three"]
            args = [tools["node"], "capture.mjs", "--output", capture, "--chrome", tools["chrome"]]
            if smoke:
                args.extend(("--smoke", "--shots", ",".join(SMOKE_SHOTS)))
            run(args, project / "Web")
            check_capture("three", capture, smoke=smoke)

    results = {}
    for engine, capture in captures.items():
        row = {"captureReportSha256": digest(capture / REPORTS[engine])}
        if opt.stage != "smoke":
            video = conform(engine, capture, audio, output)
            run([tools["node"], "verify_video.mjs", output, "--engine", engine, "--chrome", tools["chrome"]],
                project / "Web")
            row.update(file=video.name, sha256=digest(video), fullDecode="PASS", browserPlayback="PASS")
        results[engine] = row
    write_json(output / "workflow-report.json", {
        "stage": opt.stage, "status": "PASS", "timeline": {"fps": FPS, "dimensions": SIZE, "frames": FRAMES, "cuts": CUTS},
        "motionSha256": digest(TEMPLATE / MOTION), "sourceModels": source_models, "engines": results,
        "manualFullWatchAndListen": "NOT_RUN",
    })
    print(f"PASS: {output / 'workflow-report.json'}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"Workflow failed: {error}")
