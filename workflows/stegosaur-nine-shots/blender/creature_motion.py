"""Path, contact schedule, and shot list for the forest stegosaur."""
from dataclasses import dataclass
from math import cos, floor, pi, sin

FPS = 24
BODY_SCALE = 1.92
CLIP_ORDER = ("Idle", "Walk", "Run", "TurnLeft", "TurnRight", "Roar", "Bite", "TailWhip", "HitReaction")


@dataclass(frozen=True)
class Clip:
    intervals: int
    period: int = 1
    speed: float = 0.0
    turn: float = 0.0
    duty: float = 1.0
    lift: float = 0.0
    bob: float = 0.0
    cyclic: bool = False


CLIPS = {
    "Idle": Clip(96, cyclic=True),
    "Walk": Clip(48, 48, .34, duty=.70, lift=.086, bob=.014, cyclic=True),
    "Run": Clip(32, 32, .72, duty=.63, lift=.134, bob=.031, cyclic=True),
    "TurnLeft": Clip(84, 32, .30, +.33, .70, .093, .014),
    "TurnRight": Clip(84, 32, .30, -.33, .70, .093, .014),
    "Roar": Clip(72),
    "Bite": Clip(58),
    "TailWhip": Clip(67),
    "HitReaction": Clip(62),
}

# label, action, length in frames, camera, Blender-space ground position
SHOTS = [
    ("idle", "Idle", 66, "Wide", (-.16, 2.10)),
    ("walk", "Walk", 102, "Track", (.25, 3.00)),
    ("run", "Run", 75, "Low", (-.35, 2.48)),
    ("left", "TurnLeft", 84, "High", (-.05, .10)),
    ("right", "TurnRight", 84, "Reverse", (.22, -.18)),
    ("roar", "Roar", 78, "Close", (0.0, -.15)),
    ("bite", "Bite", 58, "Head", (.28, .30)),
    ("tail", "TailWhip", 67, "Tail", (.05, -.07)),
    ("reaction", "HitReaction", 62, "Profile", (.04, -.20)),
]


def smooth(a, b, value):
    t = max(0.0, min(1.0, (value - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def path(t, clip):
    """Local X is left, forward is -Y. Extend the path for off-clip foot plants."""
    heading = t * clip.turn
    if clip.turn:
        radius = clip.speed / clip.turn
        return radius * (1 - cos(heading)), -radius * sin(heading), heading
    return 0.0, -clip.speed * t, 0.0


def step_at(frame, clip, offset):
    """Return the normalized phase and the absolute start of its foot plant."""
    pos = frame / clip.period + offset
    step = floor(pos)
    return pos - step, (step - offset) * clip.period / FPS


def frame_data():
    clips = [{"name": name, "intervals": clip.intervals, "speed": clip.speed * BODY_SCALE,
              "turnDegreesPerSecond": clip.turn * 180 / pi, "cyclic": clip.cyclic}
             for name, clip in CLIPS.items()]
    shots = [{"id": name, "clip": clip, "frames": frames, "camera": camera,
              "x": -x * BODY_SCALE, "z": -y * BODY_SCALE}
             for name, clip, frames, camera, (x, y) in SHOTS]
    return {"fps": FPS, "assetLength": 6.5, "clips": clips, "shots": shots}
