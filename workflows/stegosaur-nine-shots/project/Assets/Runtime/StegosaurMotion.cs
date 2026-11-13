using System;
using UnityEngine;

namespace Dinosaug
{
    [DisallowMultipleComponent]
    public sealed class StegosaurMotion : MonoBehaviour
    {
        public enum Gait { Idle, Walk, Run }
        public enum Action { TurnLeft, TurnRight, Roar, Bite, TailWhip, HitReaction }

        [Serializable]
        public sealed class PathMotion
        {
            public string name;
            public float speed;
            public float turnDegreesPerSecond;
        }

        [Serializable]
        public sealed class Foot
        {
            public string id;
            public Transform thigh;
            public Transform shin;
            public Transform endpoint;
            public Transform pad;
            public Vector3 soleInPad;
            public Vector3 Sole => pad.TransformPoint(soleInPad);
        }

        [SerializeField] private Animator animator;
        [SerializeField] private PathMotion[] paths;
        [SerializeField] private Foot[] feet;
        [SerializeField] private Collider terrain;
        [SerializeField] private Transform followCamera;
        [SerializeField] private Transform[] tailBones;
        [SerializeField] private Vector3 tailTipLocal;
        [SerializeField] private Transform jaw;
        [SerializeField] private Quaternion closedJaw;
        [SerializeField] private Renderer[] skinSurfaces;
        [SerializeField] private bool tailPhysicsEnabled = true;
        [SerializeField] private bool keyboardDemo = true;
        [SerializeField, Range(0.05f, 0.6f)] private float maxGroundOffset = 0.26f;
        private static readonly int Motion = Animator.StringToHash("Motion");
        private static readonly int JawWrinkleWeight = Shader.PropertyToID("_JawWrinkleWeight");
        private TailSpring tailSpring;
        private MaterialPropertyBlock skinProperties;

        public Foot[] Feet => feet;
        public Collider Terrain { get => terrain; set => terrain = value; }
        public bool TailPhysicsEnabled { get => tailPhysicsEnabled; set => tailPhysicsEnabled = value; }
        public TailPoseMeasurement CurrentTailPose { get; private set; }

        public void Configure(Animator source, PathMotion[] motionPaths, Foot[] footBindings,
            Collider ground, Transform camera, Transform[] tail, Vector3 tipLocal, Transform jawBone,
            Renderer[] skinRenderers)
        {
            animator = source;
            paths = motionPaths;
            feet = footBindings;
            terrain = ground;
            followCamera = camera;
            tailBones = tail;
            tailTipLocal = tipLocal;
            jaw = jawBone;
            closedJaw = jawBone.localRotation;
            skinSurfaces = skinRenderers;
            ResetTailMotion();
        }

        public void SetGait(Gait gait) => animator.SetInteger(Motion, (int)gait);

        public void PlayAction(Action action)
        {
            foreach (Action candidate in Enum.GetValues(typeof(Action)))
                animator.ResetTrigger(candidate.ToString());
            animator.SetTrigger(action.ToString());
        }

        private void Awake()
        {
            QualitySettings.skinWeights = SkinWeights.Unlimited;
        }

        private void Update()
        {
            RestoreAnimatedTail();
#if ENABLE_LEGACY_INPUT_MANAGER
            if (keyboardDemo)
            {
                if (Input.GetKeyDown(KeyCode.Alpha1)) SetGait(Gait.Idle);
                if (Input.GetKeyDown(KeyCode.Alpha2)) SetGait(Gait.Walk);
                if (Input.GetKeyDown(KeyCode.Alpha3)) SetGait(Gait.Run);
                if (Input.GetKeyDown(KeyCode.Q)) PlayAction(Action.TurnLeft);
                if (Input.GetKeyDown(KeyCode.E)) PlayAction(Action.TurnRight);
                if (Input.GetKeyDown(KeyCode.R)) PlayAction(Action.Roar);
                if (Input.GetKeyDown(KeyCode.B)) PlayAction(Action.Bite);
                if (Input.GetKeyDown(KeyCode.T)) PlayAction(Action.TailWhip);
                if (Input.GetKeyDown(KeyCode.H)) PlayAction(Action.HitReaction);
            }
#endif
            var state = animator.GetCurrentAnimatorStateInfo(0);
            foreach (PathMotion path in paths)
            {
                if (!state.IsName(path.name))
                    continue;
                float time = Time.deltaTime;
                Vector3 offset = PathOffset(path.speed, path.turnDegreesPerSecond, time);
                transform.position += transform.rotation * offset;
                transform.Rotate(0f, -path.turnDegreesPerSecond * time, 0f, Space.Self);
                break;
            }
        }

        private void LateUpdate()
        {
            FitFeetToGround();
            SampleTailMotion(Time.deltaTime);
            if (jaw && skinSurfaces != null)
            {
                foreach (var surface in skinSurfaces)
                    ApplySkinPose(surface, Quaternion.Angle(closedJaw, jaw.localRotation));
            }
            if (followCamera != null)
            {
                Vector3 position = transform.position + new Vector3(6.1f, 2.5f, 3.9f);
                followCamera.position = Vector3.Lerp(followCamera.position, position, 1f - Mathf.Exp(-5f * Time.deltaTime));
                followCamera.LookAt(transform.position + Vector3.up * 1.4f);
            }
        }

        private void OnDisable() => ResetTailMotion();

        public float ApplySkinPose(Renderer surface, float jawDegrees)
        {
            float weight = Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(4f, 34f, jawDegrees));
            skinProperties ??= new MaterialPropertyBlock();
            surface.GetPropertyBlock(skinProperties);
            skinProperties.SetFloat(JawWrinkleWeight, weight);
            surface.SetPropertyBlock(skinProperties);
            return weight;
        }

        public void RestoreAnimatedTail() => tailSpring?.RestoreAnimationPose();

        public void ResetTailMotion()
        {
            RestoreAnimatedTail();
            tailSpring = null;
            CurrentTailPose = default;
        }

        public TailPoseMeasurement SampleTailMotion(float deltaTime)
        {
            // Older scenes without a bound chain still open and play their existing clips.
            if (tailBones == null || tailBones.Length == 0)
                return default;
            tailSpring ??= new TailSpring(tailBones, tailTipLocal);
            CurrentTailPose = tailSpring.Sample(deltaTime, tailPhysicsEnabled);
            return CurrentTailPose;
        }

        public static Vector3 PathOffset(float speed, float turnDegreesPerSecond, float seconds)
        {
            if (Mathf.Abs(turnDegreesPerSecond) < 0.0001f)
                return new Vector3(0f, 0f, speed * seconds);
            float angularSpeed = turnDegreesPerSecond * Mathf.Deg2Rad;
            float angle = angularSpeed * seconds;
            float radius = speed / angularSpeed;
            return new Vector3(-radius * (1f - Mathf.Cos(angle)), 0f, radius * Mathf.Sin(angle));
        }

        public void FitFeetToGround()
        {
            if (!terrain)
                return;
            foreach (Foot foot in feet)
            {
                Vector3 sole = foot.Sole;
                var ray = new Ray(sole + Vector3.up * 1.5f, Vector3.down);
                if (!terrain.Raycast(ray, out RaycastHit hit, 3f))
                    continue;
                float delta = Mathf.Clamp(hit.point.y - transform.position.y, -maxGroundOffset, maxGroundOffset);
                if (Mathf.Abs(delta) < 0.0003f)
                    continue;

                Vector3 hip = foot.thigh.position;
                Vector3 knee = foot.shin.position;
                Vector3 end = foot.endpoint.position;
                Vector3 toward = end + Vector3.up * delta - hip;
                float upper = Vector3.Distance(hip, knee);
                float lower = Vector3.Distance(knee, end);
                Vector3 axis = toward.normalized;
                Vector3 bend = Vector3.ProjectOnPlane(knee - hip, axis).normalized;
                if (bend.sqrMagnitude < 0.5f)
                    continue;
                float distance = Mathf.Clamp(toward.magnitude, Mathf.Abs(upper - lower) + .001f, upper + lower - .001f);
                float along = (upper * upper + distance * distance - lower * lower) / (2f * distance);
                float side = Mathf.Sqrt(Mathf.Max(0f, upper * upper - along * along));
                Vector3 solvedKnee = hip + axis * along + bend * side;
                Vector3 solvedEnd = hip + axis * distance;
                Quaternion endpointRotation = foot.endpoint.rotation;
                foot.thigh.rotation = Quaternion.FromToRotation(knee - hip, solvedKnee - hip) * foot.thigh.rotation;
                foot.shin.rotation = Quaternion.FromToRotation(foot.endpoint.position - foot.shin.position,
                    solvedEnd - solvedKnee) * foot.shin.rotation;
                foot.endpoint.rotation = endpointRotation;
            }
        }
    }
}
