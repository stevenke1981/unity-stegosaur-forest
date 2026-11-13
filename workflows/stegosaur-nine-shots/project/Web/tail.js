import { MathUtils, Quaternion, Vector3 } from 'three';

const frequency = [31, 26, 22, 18, 15];
const angleLimits = [6, 9, 12, 15, 18];
const dampingRatio = .78;
const step = 1 / 120;
export const tailNames = ['Bone_021', 'Bone_020', 'Bone_019', 'Bone_018', 'Bone_017'];

export class TailSpring {
    constructor(bones) {
        if (bones.length !== frequency.length || bones.some(bone => !bone)) {
            throw new Error('The five tail bones are required.');
        }

        this.bones = bones;
        this.root = new Vector3();
        this.oldRoot = new Vector3();
        this.pivot = new Vector3();
        this.axis = new Vector3();
        this.side = new Vector3();
        this.goal = new Vector3();
        this.candidate = new Vector3();
        this.direction = new Vector3();
        this.delta = new Quaternion();
        this.parentRotation = new Quaternion();
        this.tip = new Vector3();
        this.rawTip = new Vector3();
        this.tipOffset = new Vector3();
        this.ready = false;
        this.accumulated = 0;
        this.stats = { offsetMeters: 0, maxAngleDegrees: 0, lengthErrorMeters: 0, substeps: 0 };
        this.segments = bones.map((bone, i) => ({
            rawPosition: bone.getWorldPosition(new Vector3()),
            rawRotation: new Quaternion(),
            rawDirection: new Vector3(),
            oldDirection: new Vector3(),
            position: new Vector3(),
            velocity: new Vector3(),
            length: 0,
            oldLength: 0,
            maxAngle: MathUtils.degToRad(angleLimits[i]),
        }));
        const tip = this.segments.at(-1).rawPosition;
        const parent = this.segments.at(-2).rawPosition;
        this.localEnd = bones.at(-1).worldToLocal(tip.clone().addScaledVector(tip.clone().sub(parent), .75));
    }

    reset() {
        this.ready = false;
        this.accumulated = 0;
    }

    constrain(direction, axis, limit) {
        if (direction.lengthSq() < 1e-12) {
            return direction.copy(axis);
        }
        direction.normalize();
        const dot = MathUtils.clamp(direction.dot(axis), -1, 1);
        if (dot >= Math.cos(limit)) {
            return direction;
        }
        this.side.copy(direction).addScaledVector(axis, -dot);
        if (this.side.lengthSq() < 1e-12) {
            this.side.set(Math.abs(axis.y) < .8 ? 0 : 1, Math.abs(axis.y) < .8 ? 1 : 0, 0);
            this.side.addScaledVector(axis, -this.side.dot(axis));
        }
        return direction.copy(axis).multiplyScalar(Math.cos(limit))
            .addScaledVector(this.side.normalize(), Math.sin(limit));
    }

    // Caller samples the authored animation before every call.
    apply(seconds, enabled = true) {
        for (const [i, bone] of this.bones.entries()) {
            const segment = this.segments[i];
            bone.getWorldPosition(segment.rawPosition);
            bone.getWorldQuaternion(segment.rawRotation);
        }
        this.bones.at(-1).localToWorld(this.rawTip.copy(this.localEnd));
        this.root.copy(this.segments[0].rawPosition);

        for (const [i, segment] of this.segments.entries()) {
            const end = this.segments[i + 1]?.rawPosition ?? this.rawTip;
            segment.rawDirection.copy(end).sub(segment.rawPosition);
            segment.length = segment.rawDirection.length();
            segment.rawDirection.divideScalar(segment.length);
            if (!this.ready || !enabled) {
                segment.position.copy(end);
                segment.velocity.set(0, 0, 0);
                segment.oldDirection.copy(segment.rawDirection);
                segment.oldLength = segment.length;
            }
        }

        this.stats.maxAngleDegrees = 0;
        this.stats.lengthErrorMeters = 0;
        this.stats.substeps = 0;
        if (!enabled || !this.ready) {
            this.ready = enabled;
            this.accumulated = 0;
            this.oldRoot.copy(this.root);
            this.tip.copy(this.rawTip);
            this.tipOffset.set(0, 0, 0);
            this.stats.offsetMeters = 0;
            return this.stats;
        }

        const dt = MathUtils.clamp(seconds, 0, .10);
        const available = this.accumulated + dt;
        const steps = Math.floor((available + 1e-9) / step);
        for (let tick = 0; tick < steps; tick++) {
            const alpha = MathUtils.clamp(((tick + 1) * step - this.accumulated) / dt, 0, 1);
            this.pivot.copy(this.oldRoot).lerp(this.root, alpha);
            for (const [i, segment] of this.segments.entries()) {
                const omega = frequency[i];
                const length = MathUtils.lerp(segment.oldLength, segment.length, alpha);
                this.axis.copy(segment.oldDirection).lerp(segment.rawDirection, alpha).normalize();
                this.goal.copy(this.pivot).addScaledVector(this.axis, length);
                segment.velocity.multiplyScalar(1 - 2 * dampingRatio * omega * step);
                this.candidate.copy(this.goal).sub(segment.position);
                segment.velocity.addScaledVector(this.candidate, omega * omega * step);
                this.candidate.copy(segment.position).addScaledVector(segment.velocity, step).sub(this.pivot);
                this.constrain(this.candidate, this.axis, segment.maxAngle)
                    .multiplyScalar(length).add(this.pivot);
                segment.velocity.copy(this.candidate).sub(segment.position).divideScalar(step);
                segment.position.copy(this.candidate);
                this.pivot.copy(segment.position);
            }
        }
        this.accumulated = Math.max(0, available - steps * step);
        this.oldRoot.copy(this.root);
        this.stats.substeps = steps;

        for (const [i, bone] of this.bones.entries()) {
            const segment = this.segments[i];
            segment.oldDirection.copy(segment.rawDirection);
            segment.oldLength = segment.length;
            bone.getWorldPosition(this.pivot);
            this.direction.copy(segment.position).addScaledVector(segment.velocity, this.accumulated).sub(this.pivot);
            this.constrain(this.direction, segment.rawDirection, segment.maxAngle);
            this.delta.setFromUnitVectors(segment.rawDirection, this.direction).multiply(segment.rawRotation);
            bone.parent.getWorldQuaternion(this.parentRotation).invert();
            bone.quaternion.copy(this.parentRotation.multiply(this.delta));
            bone.updateWorldMatrix(false, false);
            this.stats.maxAngleDegrees = Math.max(this.stats.maxAngleDegrees,
                MathUtils.radToDeg(this.direction.angleTo(segment.rawDirection)));
        }

        this.bones.at(-1).localToWorld(this.tip.copy(this.localEnd));
        this.tipOffset.copy(this.tip).sub(this.rawTip);
        this.stats.offsetMeters = this.tipOffset.length();
        for (const [i, bone] of this.bones.entries()) {
            const end = this.bones[i + 1]?.getWorldPosition(this.candidate) ?? this.tip;
            const length = bone.getWorldPosition(this.pivot).distanceTo(end);
            this.stats.lengthErrorMeters = Math.max(this.stats.lengthErrorMeters, Math.abs(length - this.segments[i].length));
        }
        return this.stats;
    }
}
