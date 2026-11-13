using System;
using UnityEngine;

namespace Dinosaug
{
    public readonly struct TailPoseMeasurement
    {
        public readonly Vector3 AnimatedTip;
        public readonly Vector3 SimulatedTip;
        public readonly float MaxLengthErrorM;
        public readonly float MaxBendLimitExcessDegrees;
        public readonly float MaxAddedBendDegrees;

        public float TipDisplacementM => Vector3.Distance(AnimatedTip, SimulatedTip);

        public TailPoseMeasurement(Vector3 animatedTip, Vector3 simulatedTip, float lengthError,
            float limitExcess, float addedBend)
        {
            AnimatedTip = animatedTip;
            SimulatedTip = simulatedTip;
            MaxLengthErrorM = lengthError;
            MaxBendLimitExcessDegrees = limitExcess;
            MaxAddedBendDegrees = addedBend;
        }
    }

    /// <summary>A world-space damped spring per tail segment, projected to a fixed-length angular limit.</summary>
    public sealed class TailSpring
    {
        public const int TicksPerSecond = 120;
        public const float DampingRatio = .78f;
        public const float TipLengthFraction = .75f;
        private const float Step = 1f / TicksPerSecond;
        private const float MaxDeltaTime = .10f;
        private static readonly float[] NaturalFrequency = { 31f, 26f, 22f, 18f, 15f };
        private static readonly float[] BendLimitDegrees = { 6f, 9f, 12f, 15f, 18f };
        public static float[] GetNaturalFrequencies() => (float[])NaturalFrequency.Clone();
        public static float[] GetBendLimits() => (float[])BendLimitDegrees.Clone();

        private readonly Transform[] bones;
        private readonly Vector3 terminalTipLocal;
        private readonly Quaternion[] rawLocalRotations = new Quaternion[5];
        private readonly Quaternion[] rawWorldRotations = new Quaternion[5];
        private readonly Vector3[] previousDirection = new Vector3[5];
        private readonly Vector3[] targetDirection = new Vector3[5];
        private readonly float[] previousLength = new float[5];
        private readonly float[] targetLength = new float[5];
        private readonly Vector3[] positions = new Vector3[5];
        private readonly Vector3[] velocities = new Vector3[5];
        private Vector3 previousRoot;
        private double accumulator;
        private bool initialized;
        private bool posed;

        public TailSpring(Transform[] bones, Vector3 terminalTipLocal)
        {
            if (bones.Length != BendLimitDegrees.Length)
                throw new ArgumentException("The stegosaur tail requires five serial bones.", nameof(bones));
            this.bones = bones;
            this.terminalTipLocal = terminalTipLocal;
        }

        public void RestoreAnimationPose()
        {
            if (!posed)
                return;

            for (int i = 0; i < bones.Length; i++)
                bones[i].localRotation = rawLocalRotations[i];
            posed = false;
        }

        public TailPoseMeasurement Sample(float deltaTime, bool enablePhysics)
        {
            Vector3 root = bones[0].position;
            Vector3 animatedTip = bones[bones.Length - 1].TransformPoint(terminalTipLocal);
            for (int i = 0; i < bones.Length; i++)
            {
                Transform bone = bones[i];
                rawLocalRotations[i] = bone.localRotation;
                rawWorldRotations[i] = bone.rotation;
                Vector3 end = i < bones.Length - 1 ? bones[i + 1].position : animatedTip;
                Vector3 offset = end - bone.position;
                targetLength[i] = offset.magnitude;
                targetDirection[i] = offset / targetLength[i];

                if (!initialized || !enablePhysics)
                {
                    positions[i] = end;
                    velocities[i] = Vector3.zero;
                }
            }

            if (!initialized || !enablePhysics)
            {
                accumulator = 0;
                RememberTarget(root);
                initialized = enablePhysics;
                posed = false;
                return new TailPoseMeasurement(animatedTip, animatedTip, 0, 0, 0);
            }

            double elapsed = Mathf.Clamp(deltaTime, 0, MaxDeltaTime);
            double nextTick = Step - accumulator;
            accumulator += elapsed;
            while (accumulator + 1e-8 >= Step)
            {
                float blend = elapsed > 0 ? Mathf.Clamp01((float)(nextTick / elapsed)) : 1;
                Vector3 pivot = Vector3.Lerp(previousRoot, root, blend);
                for (int i = 0; i < bones.Length; i++)
                {
                    Vector3 direction = Vector3.Lerp(previousDirection[i], targetDirection[i], blend).normalized;
                    float length = Mathf.Lerp(previousLength[i], targetLength[i], blend);
                    float omega = NaturalFrequency[i];
                    Vector3 goal = pivot + length * direction;
                    Vector3 lastPosition = positions[i];
                    velocities[i] += (omega * omega * (goal - lastPosition) -
                        2 * DampingRatio * omega * velocities[i]) * Step;
                    Vector3 desired = lastPosition + velocities[i] * Step - pivot;
                    positions[i] = pivot + length * LimitDirection(direction, desired, BendLimitDegrees[i]);
                    velocities[i] = (positions[i] - lastPosition) / Step;
                    pivot = positions[i];
                }

                nextTick += Step;
                accumulator -= Step;
            }
            accumulator = Math.Max(0, accumulator);
            RememberTarget(root);

            for (int i = 0; i < bones.Length; i++)
            {
                Transform bone = bones[i];
                Vector3 desired = positions[i] + velocities[i] * (float)accumulator - bone.position;
                Vector3 direction = LimitDirection(targetDirection[i], desired, BendLimitDegrees[i]);
                bone.rotation = Quaternion.FromToRotation(targetDirection[i], direction) * rawWorldRotations[i];
            }

            Vector3 simulatedTip = bones[bones.Length - 1].TransformPoint(terminalTipLocal);
            float maxLengthError = 0;
            float maxLimitExcess = 0;
            float maxAddedBend = 0;
            for (int i = 0; i < bones.Length; i++)
            {
                Vector3 end = i < bones.Length - 1 ? bones[i + 1].position : simulatedTip;
                Vector3 offset = end - bones[i].position;
                float addedBend = Vector3.Angle(targetDirection[i], offset);
                maxLengthError = Mathf.Max(maxLengthError, Mathf.Abs(offset.magnitude - targetLength[i]));
                maxLimitExcess = Mathf.Max(maxLimitExcess, addedBend - BendLimitDegrees[i]);
                maxAddedBend = Mathf.Max(maxAddedBend, addedBend);
            }

            posed = true;
            return new TailPoseMeasurement(animatedTip, simulatedTip, maxLengthError, maxLimitExcess, maxAddedBend);
        }

        private void RememberTarget(Vector3 root)
        {
            previousRoot = root;
            Array.Copy(targetDirection, previousDirection, bones.Length);
            Array.Copy(targetLength, previousLength, bones.Length);
        }

        private static Vector3 LimitDirection(Vector3 target, Vector3 offset, float limitDegrees)
        {
            if (offset.sqrMagnitude < 1e-10f)
                return target;
            return Vector3.RotateTowards(target, offset.normalized, limitDegrees * Mathf.Deg2Rad, 0f);
        }
    }
}
