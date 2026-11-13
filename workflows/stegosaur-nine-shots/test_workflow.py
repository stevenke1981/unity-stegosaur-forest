"""Regression checks for shared timecodes and the two render report formats."""

import copy
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest

from delivery import CUTS, FPS, FRAMES, JAW_PEAK, REPORTS, SHOTS, SIZE, check_capture, read_json, write_json
from workflow import MOTION, TEMPLATE, assets_for


class WorkflowTests(unittest.TestCase):
    def test_shared_motion_and_timecodes(self):
        motion = read_json(TEMPLATE / MOTION)
        self.assertEqual(motion["fps"], FPS)
        self.assertEqual(tuple((shot["id"], shot["frames"]) for shot in motion["shots"]), SHOTS)
        self.assertEqual(CUTS, (0, 66, 168, 243, 327, 411, 489, 547, 614))
        self.assertEqual(FRAMES, 676)
        self.assertEqual(9 * FPS - CUTS[2], 48)
        self.assertEqual(JAW_PEAK / FPS, 19)

    def test_asset_contract(self):
        unity = set(assets_for(("unity",)))
        three = set(assets_for(("three",)))
        self.assertIn(Path("Assets/Source/Dinosaur/CreatureStegosaur.fbx"), unity)
        self.assertIn(Path("Web/assets/stegosaur-quad30k.glb"), three)
        self.assertEqual(unity | three, set(assets_for(("unity", "three"))))
        self.assertTrue(all(path.parts[0] == "Assets" for path in unity))
        self.assertTrue(all(path.parts[0] == "Web" for path in three))

    def test_capture_report_and_diagnostic_frames(self):
        # These are PNG headers only. Real pixels and complete decoding are covered by the local engine run.
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", *SIZE)
        run_frames = [168, 205, 206, 211, 216, 222, 229, 242]
        roar_frames = [411, 419, 422, 429, 450, 456, 465, 474, 479, 488]
        base = {"fps": FPS, "smoke": True}
        for engine in REPORTS:
            with self.subTest(engine=engine), TemporaryDirectory(prefix="capture proof ") as temp:
                capture = Path(temp)
                frames = capture / "frames"
                frames.mkdir()
                for index in run_frames + roar_frames:
                    (frames / f"frame_{index:05d}.png").write_bytes(png)
                if engine == "unity":
                    report = dict(base, width=SIZE[0], height=SIZE[1], timelineFrames=FRAMES, frames=18,
                                  nativeSubsurface=True, partial=True, shots=[
                        dict(id=name, firstFrame=indices[0], sourceFrames=count,
                             frames=len(indices), capturedTimelineFrames=indices)
                        for name, indices, count in (("run", run_frames, 75), ("roar", roar_frames, 78))
                    ])
                else:
                    report = dict(base, dimensions=SIZE, frames=FRAMES, outputFrames=18, errors=[],
                                  requestsOutsideLoopback=[], deterministicReplay="PASS", toggleChangedPixels="PASS",
                                  shots=[
                        dict(id=name, start=indices[0], frames=count, renderedFrames=len(indices),
                             samples=[{"frame": frame - indices[0]} for frame in indices])
                        for name, indices, count in (("run", run_frames, 75), ("roar", roar_frames, 78))
                    ])
                file = capture / REPORTS[engine]
                write_json(file, report)
                self.assertEqual(len(check_capture(engine, capture, smoke=True)[1]), 18)
                with self.assertRaisesRegex(ValueError, "capture dimensions"):
                    check_capture(engine, capture)

                wrong = copy.deepcopy(report)
                if engine == "unity":
                    wrong["nativeSubsurface"] = False
                else:
                    wrong["requestsOutsideLoopback"] = ["https://example.invalid"]
                write_json(file, wrong)
                with self.assertRaisesRegex(ValueError, "native HDRP|local-only"):
                    check_capture(engine, capture, smoke=True)
                write_json(file, report)
                frame = frames / "frame_00216.png"
                frame.write_bytes(png[:-4] + struct.pack(">I", 360))
                with self.assertRaisesRegex(ValueError, "PNG dimensions"):
                    check_capture(engine, capture, smoke=True)
                frame.unlink()
                with self.assertRaisesRegex(ValueError, "PNG sequence"):
                    check_capture(engine, capture, smoke=True)


if __name__ == "__main__":
    unittest.main()
