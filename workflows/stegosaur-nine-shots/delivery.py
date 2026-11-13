"""Capture and MP4 checks for the Stegosaur nine-shot preset."""

import hashlib
import json
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory

FPS = 24
SIZE = (1280, 720)
SHOTS = (
    ("idle", 66), ("walk", 102), ("run", 75), ("left", 84), ("right", 84),
    ("roar", 78), ("bite", 58), ("tail", 67), ("reaction", 62),
)
CUTS = tuple(sum(length for _, length in SHOTS[:index]) for index in range(len(SHOTS)))
FRAMES = sum(length for _, length in SHOTS)
RUN_REVIEW = (206, 211, 216, 222, 229)
JAW_PEAK = 456
SMOKE_SHOTS = ("run", "roar")
REPORTS = {"unity": "creature-unity-report.json", "three": "three-render-report.json"}
FFMPEG = ("ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check_capture(engine, capture, *, smoke=False):
    report_path = capture / REPORTS[engine]
    report = read_json(report_path)
    unity = engine == "unity"
    dimensions = (report["width"], report["height"]) if unity else tuple(report["dimensions"])
    timeline_frames = report["timelineFrames" if unity else "frames"]
    if (dimensions, report["fps"], timeline_frames, report["smoke"]) != (SIZE, FPS, FRAMES, smoke):
        raise ValueError(f"{engine}: capture dimensions, frame rate, timeline or smoke mode is incorrect.")
    if unity:
        if report["nativeSubsurface"] is not True or report["partial"] != smoke:
            raise ValueError("Unity must report native HDRP SSS and the requested capture scope.")
    elif (report["errors"] or report["requestsOutsideLoopback"] or report["deterministicReplay"] != "PASS"
          or report["toggleChangedPixels"] != "PASS"):
        raise ValueError("Three.js reported capture, local-only, or tail replay errors.")

    expected = [(name, cut, count) for (name, count), cut in zip(SHOTS, CUTS, strict=True)
                if not smoke or name in SMOKE_SHOTS]
    source_count = "sourceFrames" if unity else "frames"
    start_field = "firstFrame" if unity else "start"
    actual = [(shot["id"], shot[start_field], shot[source_count]) for shot in report["shots"]]
    if actual != expected:
        raise ValueError(f"{engine}: capture shot IDs, starts or lengths differ from the preset.")
    indices = []
    for shot, (name, cut, count) in zip(report["shots"], expected, strict=True):
        if smoke:
            captured = (shot["capturedTimelineFrames"] if unity
                        else [cut + sample["frame"] for sample in shot["samples"]])
        else:
            captured = list(range(cut, cut + count))
            if unity and shot["capturedTimelineFrames"] != captured:
                raise ValueError(f"Unity: reported frames for {name} are not contiguous.")
        if (shot["frames" if unity else "renderedFrames"] != len(captured)
                or captured != sorted(set(captured)) or not captured
                or captured[0] < cut or captured[-1] >= cut + count):
            raise ValueError(f"{engine}: invalid captured frames for {name}.")
        if smoke and not set(RUN_REVIEW if name == "run" else (JAW_PEAK,)).issubset(captured):
            raise ValueError(f"{engine}: {name} is missing diagnostic frames.")
        indices.extend(captured)

    frames = sorted((capture / "frames").glob("frame_*.png"))
    count_field = "frames" if unity else "outputFrames"
    if report[count_field] != len(indices) or [path.name for path in frames] != [
        f"frame_{index:05d}.png" for index in indices
    ]:
        raise ValueError(f"{engine}: PNG sequence differs from the capture report.")
    for path in frames:
        with path.open("rb") as source:
            header = source.read(24)
        if (len(header) != 24 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                or struct.unpack(">II", header[16:]) != SIZE):
            raise ValueError(f"{engine}: unexpected PNG dimensions or header in {path.name}.")
    return report_path, frames


def probe(path, *options):
    return json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", *options, "-of", "json", path], encoding="utf-8"
    ))


def audio_hash(path):
    return subprocess.check_output(
        [*FFMPEG, "-i", path, "-map", "0:a:0", "-f", "streamhash", "-hash", "sha256", "-"],
        encoding="ascii",
    ).strip().rsplit("=", 1)[1]


def conform(engine, capture, audio, output):
    report_path, sequence = check_capture(engine, capture)
    duration = FRAMES / FPS
    title = "Unity-HDRP-SSS" if engine == "unity" else "ThreeJS"
    video = output / f"Stegosaur-{title}-Nine-Shots.mp4"
    with TemporaryDirectory(prefix=f"{engine}-conform-", dir=output) as folder:
        encoded = Path(folder) / video.name
        subprocess.run([
            *FFMPEG, "-n", "-framerate", str(FPS), "-start_number", "0",
            "-i", capture / "frames" / "frame_%05d.png", "-i", audio,
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", "scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-profile:v", "high", "-level:v", "3.1",
            "-force_key_frames", ",".join(f"{cut / FPS:.9f}" for cut in CUTS),
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "iec61966-2-1",
            "-c:a", "copy", "-movflags", "+faststart", encoded,
        ], check=True)
        inspection = probe(encoded, "-show_format", "-show_streams")
        picture, sound = inspection["streams"]
        if (
            picture["codec_name"], picture["pix_fmt"], picture["avg_frame_rate"], int(picture["nb_frames"]),
            picture["width"], picture["height"], sound["codec_name"], sound["sample_rate"], sound["channels"]
        ) != ("h264", "yuv420p", f"{FPS}/1", FRAMES, *SIZE, "aac", "48000", 1):
            raise ValueError("Expected 720p H.264 at 24 fps and mono 48 kHz AAC.")
        if any(abs(float(stream["duration"]) - duration) > .002
               for stream in (picture, sound, inspection["format"])):
            raise ValueError("Audio and video durations must match the edit. Supply the conformed AAC source.")
        timeline = probe(encoded, "-select_streams", "v:0", "-show_frames",
                         "-show_entries", "frame=key_frame,pts_time")["frames"]
        if len(timeline) != FRAMES or any(
            abs(float(frame["pts_time"]) - index / FPS) > 2e-6 for index, frame in enumerate(timeline)
        ):
            raise ValueError("The MP4 does not present every frame on the 24 fps timeline.")
        if any(not timeline[cut]["key_frame"] for cut in CUTS):
            raise ValueError("The MP4 is missing a keyframe at an edit point.")
        original_audio = audio_hash(audio)
        if audio_hash(encoded) != original_audio:
            raise ValueError("The decoded audio differs from the input AAC.")
        subprocess.run([*FFMPEG, "-i", encoded, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                       check=True)
        result = {
            "file": video.name, "bytes": encoded.stat().st_size, "sha256": digest(encoded), "fullDecode": "PASS",
            "captureReportSha256": digest(report_path),
            "renderedSequenceSha256": hashlib.sha256(
                "\n".join(digest(path) for path in sequence).encode("ascii")
            ).hexdigest(),
            "video": {"dimensions": SIZE, "fps": FPS, "frames": FRAMES, "duration": duration,
                      "codec": "H.264", "pixelFormat": "yuv420p", "keyframesAtAllCuts": CUTS},
            "sound": {"codec": "AAC", "sampleRate": 48000, "channels": 1,
                      "sourceDecodedPcmSha256": original_audio, "decodedPcmSha256": original_audio,
                      "sourceBitstreamCopied": True, "jawPeakAt": JAW_PEAK / FPS},
            "manualFullWatchAndListen": "NOT_RUN",
        }
        encoded.rename(video)
        write_json(output / f"{engine}-video-report.json", result)
    return video
