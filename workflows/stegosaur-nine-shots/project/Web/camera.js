import { MathUtils, Vector3 } from 'three';

// The Unity layout is left-handed. Mirror its X axis for Blender glTF / Three.js.
const rigs = {
    Wide: [[6.6, 2.8, 4.4], [5.9, 2.5, 4], 45],
    Track: [[-6.5, 1.8, 2.4], [-6.15, 1.64, 2.95], 46],
    Low: [[5.65, 1.06, -.55], [5.15, 1.27, 1.95], 51],
    High: [[7.3, 4.1, 1.4], [6.5, 4.6, 2.35], 47],
    Reverse: [[-7.1, 2.55, 3.15], [-3.4, 3, 6.1], 46],
    Close: [[2.84, 1.22, 5.2], [2.25, 1.52, 4.93], 28],
    Head: [[-4.7, 1.6, 3.85], [-4.2, 1.53, 4.2], 36],
    Tail: [[6.3, 4.05, -2.75], [5.75, 3.74, -2.65], 49],
    Profile: [[-6.45, 2.04, -.3], [-6, 2.12, 1.12], 47],
};
const position = new Vector3();
const end = new Vector3();
const focus = new Vector3();
const detail = new Vector3();
const forward = new Vector3();

export function placeSubject(subject, shot, motion, seconds) {
    const turn = MathUtils.degToRad(motion.turnDegreesPerSecond);
    const angle = turn * seconds;
    if (Math.abs(turn) < 1e-6) {
        subject.position.set(-shot.x, 0, shot.z + motion.speed * seconds);
    } else {
        const radius = motion.speed / turn;
        subject.position.set(-shot.x + radius * (1 - Math.cos(angle)), 0, shot.z + radius * Math.sin(angle));
    }
    subject.rotation.y = angle;
    subject.updateMatrixWorld(true);
}

export function compose(camera, view, frame, lastFrame, subject, head, tip) {
    const t = MathUtils.smoothstep(frame, 0, lastFrame);
    const [start, finish, fov] = rigs[view];
    position.fromArray(start).lerp(end.fromArray(finish), t);
    position.x *= -1;
    camera.position.copy(position).add(subject.position);
    focus.copy(subject.position).add(detail.set(0, 1.37, .13));
    forward.set(0, 0, 1).applyQuaternion(subject.quaternion);
    switch (view) {
        case 'Track':
            focus.z += .26;
            break;
        case 'Low':
            focus.z += MathUtils.lerp(-.1, .48, t);
            focus.y -= .31;
            break;
        case 'Close':
            focus.copy(head.getWorldPosition(detail)).addScaledVector(forward, .30);
            focus.y += .13;
            break;
        case 'Head':
            focus.lerp(head.getWorldPosition(detail), .78).addScaledVector(forward, .30);
            break;
        case 'Tail':
            focus.lerp(tip.getWorldPosition(detail), .26);
            break;
    }
    camera.fov = fov;
    camera.updateProjectionMatrix();
    camera.lookAt(focus);
    return focus;
}
