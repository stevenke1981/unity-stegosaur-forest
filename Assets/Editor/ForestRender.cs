using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace Dinosaug
{
    public static class ForestRender
    {
        private const string DinosaurFbx = "Assets/Source/Dinosaur/Stegosaur.fbx";
        private const string ReportFile = "render-report.json";
        private const float ShotSeconds = 2.5f;
        private const float WalkStart = -7.2f;
        private const float RunStart = -4.7f;
        private const float StopPosition = 2.2f;

        private static readonly string[] ShotNames = { "Walk", "Run", "HeadShake", "Roar_Pose", "Orbit" };

        [MenuItem("Dinosaug/Render Forest Film")]
        public static void RenderFromMenu()
        {
            Capture(NewOutputDirectory(), 1280, 720, 24, false);
        }

        [MenuItem("Dinosaug/Render Forest Smoke Test")]
        public static void SmokeFromMenu()
        {
            Capture(NewOutputDirectory(), 1280, 720, 24, true);
        }

        public static void RenderBatch()
        {
            string[] args = Environment.GetCommandLineArgs();
            string output = GetArgument(args, "-forest-output", null) ?? NewOutputDirectory();
            int width = int.Parse(GetArgument(args, "-forest-width", "1280"), CultureInfo.InvariantCulture);
            int height = int.Parse(GetArgument(args, "-forest-height", "720"), CultureInfo.InvariantCulture);
            int fps = int.Parse(GetArgument(args, "-forest-fps", "24"), CultureInfo.InvariantCulture);
            Capture(output, width, height, fps, args.Contains("-forest-smoke"));
        }

        private static void Capture(string output, int width, int height, int fps, bool smoke)
        {
            if (width < 64 || width > 3840 || height < 64 || height > 2160 ||
                width % 2 != 0 || height % 2 != 0 || fps < 2 || fps > 60)
                throw new ArgumentOutOfRangeException(nameof(width), "Use even dimensions from 64×64 to 3840×2160 and 2–60 fps.");
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Exit Play mode before capturing the forest.");
            if (SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null)
                throw new InvalidOperationException("Capture requires Unity to start with a graphics device.");

            string outputPath = Path.GetFullPath(output);
            string framesPath = Path.Combine(outputPath, "frames");
            string reportPath = Path.Combine(outputPath, ReportFile);
            if (Directory.Exists(framesPath) || File.Exists(reportPath))
                throw new IOException($"A capture already exists at {outputPath}. Choose a new output directory.");

            Scene scene = SceneManager.GetActiveScene();
            if (scene.path != ForestSceneBuilder.ScenePath)
            {
                if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo())
                    return;
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(ForestSceneBuilder.ScenePath) != null)
                    EditorSceneManager.OpenScene(ForestSceneBuilder.ScenePath, OpenSceneMode.Single);
                else
                    ForestSceneBuilder.Build();
                scene = SceneManager.GetActiveScene();
            }

            GameObject anchor = scene.GetRootGameObjects().SingleOrDefault(root => root.name == "Stegosaur");
            Camera sceneCamera = Camera.main;
            if (anchor == null || anchor.transform.childCount != 1 ||
                sceneCamera == null || sceneCamera.gameObject.scene != scene)
                throw new InvalidOperationException("The forest needs the Stegosaur anchor, its rig child, and a MainCamera in the active scene.");

            Transform subject = anchor.transform;
            GameObject model = subject.GetChild(0).gameObject;
            SkinnedMeshRenderer[] pieces = model.GetComponentsInChildren<SkinnedMeshRenderer>();
            if (pieces.Length == 0)
                throw new InvalidOperationException("The assembled Stegosaur has no visible skinned mesh parts.");
            Transform[] skeleton = model.GetComponentsInChildren<Transform>(true);
            Transform face = skeleton.Single(node => node.name == "Bone_023");
            Transform head = skeleton.Single(node => node.name == "Bone_024");
            Transform leg = skeleton.Single(node => node.name == "Bone_029");

            Bounds bounds = pieces[0].bounds;
            foreach (SkinnedMeshRenderer piece in pieces.Skip(1))
                bounds.Encapsulate(piece.bounds);
            Vector3 center = subject.InverseTransformPoint(bounds.center);
            TransformPose anchorPose = new TransformPose(subject);
            TransformPose[] rigPose = skeleton.Select(node => new TransformPose(node)).ToArray();
            TransformPose modelPose = new TransformPose(model.transform);
            bool[] updateWhenOffscreen = pieces.Select(piece => piece.updateWhenOffscreen).ToArray();

            AnimationClip[] imported = AssetDatabase.LoadAllAssetRepresentationsAtPath(DinosaurFbx).OfType<AnimationClip>().ToArray();
            AnimationClip[] clips = ShotNames.Take(4).Select(name => FindClip(imported, name)).ToArray();
            int shotFrames = Mathf.RoundToInt(ShotSeconds * fps);
            int totalFrames = smoke ? ShotNames.Length * 3 : ShotNames.Length * shotFrames;
            var report = new CaptureReport
            {
                unityVersion = Application.unityVersion,
                graphicsDevice = SystemInfo.graphicsDeviceName,
                graphicsApi = SystemInfo.graphicsDeviceType.ToString(),
                colorSpace = QualitySettings.activeColorSpace.ToString(),
                width = width,
                height = height,
                frameRate = fps,
                smoke = smoke,
                skinnedParts = pieces.Select(piece => piece.name).OrderBy(name => name, StringComparer.Ordinal).ToArray(),
                shots = new ShotReport[ShotNames.Length]
            };

            GameObject cameraObject = null;
            RenderTexture target = null;
            Texture2D pixels = null;
            RenderTexture previousTarget = RenderTexture.active;
            int outputFrame = 0;
            try
            {
                foreach (SkinnedMeshRenderer piece in pieces)
                    piece.updateWhenOffscreen = true;

                cameraObject = new GameObject("Forest Capture Camera") { hideFlags = HideFlags.HideAndDontSave };
                Camera camera = cameraObject.AddComponent<Camera>();
                camera.CopyFrom(sceneCamera);
                camera.enabled = false;
                camera.usePhysicalProperties = false;
                camera.allowHDR = false;
                camera.nearClipPlane = 0.08f;
                camera.aspect = width / (float)height;

                target = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
                camera.targetTexture = target;
                pixels = new Texture2D(width, height, TextureFormat.RGB24, false, false);
                Directory.CreateDirectory(framesPath);

                for (int shot = 0; shot < ShotNames.Length; shot++)
                {
                    foreach (TransformPose rest in rigPose)
                        rest.Restore();

                    AnimationClip clip = clips[shot == 4 ? 0 : shot];
                    Transform drivenBone = (shot == 2 || shot == 3) ? head : leg;
                    float rotation = VerifyBoneMotion(model, clip, drivenBone);
                    var detail = new ShotReport
                    {
                        name = ShotNames[shot],
                        clip = clip.name,
                        clipSeconds = clip.length,
                        measuredBone = drivenBone.name,
                        boneRotationDegrees = rotation,
                        firstFrame = outputFrame,
                        sourceFrames = shotFrames
                    };
                    IEnumerable<int> sampleFrames = smoke
                        ? new[] { 0, shotFrames / 2, shotFrames - 1 }
                        : Enumerable.Range(0, shotFrames);

                    foreach (int frame in sampleFrames)
                    {
                        if (!Application.isBatchMode && EditorUtility.DisplayCancelableProgressBar(
                                "Rendering Stegosaur Forest", $"{detail.name}  {outputFrame + 1}/{totalFrames}",
                                outputFrame / (float)totalFrames))
                            throw new OperationCanceledException("Forest capture was canceled. Partial frames were kept.");

                        float progress = frame / (float)(shotFrames - 1);
                        float seconds = frame / (float)fps;
                        float poseTime = (shot == 2 || shot == 3)
                            ? clip.length * progress
                            : Mathf.Repeat(seconds * (shot == 4 ? 0.75f : 1f), clip.length);
                        clip.SampleAnimation(model, poseTime);
                        modelPose.Restore();
                        ComposeShot(shot, progress, subject, anchorPose.LocalPosition, center, face, camera);

                        if (frame == 0)
                        {
                            detail.anchorStart = subject.position;
                            detail.cameraStart = camera.transform.position;
                        }
                        if (frame == shotFrames - 1)
                        {
                            detail.anchorEnd = subject.position;
                            detail.cameraEnd = camera.transform.position;
                        }

                        camera.Render();
                        RenderTexture.active = target;
                        pixels.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
                        File.WriteAllBytes(Path.Combine(framesPath, $"frame_{outputFrame:D5}.png"), pixels.EncodeToPNG());
                        outputFrame++;
                    }

                    detail.frameCount = outputFrame - detail.firstFrame;
                    report.shots[shot] = detail;
                    Debug.Log($"[Dinosaug] {detail.name}: {detail.frameCount} frames, {detail.measuredBone} moved {rotation:F2}°.");
                }

                report.frameCount = outputFrame;
                File.WriteAllText(reportPath, JsonUtility.ToJson(report, true) + Environment.NewLine);
                Debug.Log($"[Dinosaug] Captured {outputFrame} frames to {outputPath}");
            }
            finally
            {
                RenderTexture.active = previousTarget;
                if (cameraObject != null)
                    Object.DestroyImmediate(cameraObject);
                if (target != null)
                    RenderTexture.ReleaseTemporary(target);
                if (pixels != null)
                    Object.DestroyImmediate(pixels);
                foreach (TransformPose rest in rigPose)
                    rest.Restore();
                anchorPose.Restore();
                for (int i = 0; i < pieces.Length; i++)
                    pieces[i].updateWhenOffscreen = updateWhenOffscreen[i];
                if (!Application.isBatchMode)
                    EditorUtility.ClearProgressBar();
            }
        }

        private static void ComposeShot(int shot, float progress, Transform subject, Vector3 origin,
            Vector3 center, Transform face, Camera camera)
        {
            float curve = Mathf.SmoothStep(0f, 1f, progress);
            float yaw = 0f;
            Vector3 offset;
            switch (shot)
            {
                case 0:
                    offset = new Vector3(0f, 0f, Mathf.Lerp(WalkStart, RunStart, progress));
                    break;
                case 1:
                    offset = new Vector3(0f, 0f, Mathf.Lerp(RunStart, StopPosition, progress));
                    break;
                case 4:
                    float arc = 1.16f * progress;
                    yaw = -arc * Mathf.Rad2Deg;
                    offset = new Vector3(-1.65f * (1f - Mathf.Cos(arc)), 0f, StopPosition + 1.65f * Mathf.Sin(arc));
                    break;
                default:
                    offset = new Vector3(0f, 0f, StopPosition);
                    break;
            }

            subject.localPosition = origin + offset;
            subject.localRotation = Quaternion.Euler(0f, yaw, 0f);
            Vector3 position;
            Vector3 focus = subject.TransformPoint(center);
            float fieldOfView;
            switch (shot)
            {
                case 0:
                    position = subject.position + new Vector3(Mathf.Lerp(-4.0f, -3.65f, curve),
                        Mathf.Lerp(1.55f, 1.8f, curve), Mathf.Lerp(1.7f, 2.35f, curve));
                    focus += subject.forward * 0.62f + Vector3.down * 0.38f;
                    fieldOfView = 58f;
                    break;
                case 1:
                    position = subject.position + new Vector3(4.18f, Mathf.Lerp(0.86f, 1.13f, curve),
                        Mathf.Lerp(-0.85f, 1.05f, curve));
                    focus += Vector3.down * 0.18f + subject.forward * Mathf.Lerp(-0.20f, 0.50f, curve);
                    fieldOfView = 59f;
                    break;
                case 2:
                    position = subject.position + new Vector3(Mathf.Lerp(-3.0f, -2.55f, curve),
                        Mathf.Lerp(1.86f, 1.74f, curve), 4.88f);
                    focus = face.position + Vector3.up * 0.13f;
                    fieldOfView = 32f;
                    break;
                case 3:
                    position = subject.position + new Vector3(Mathf.Lerp(3.1f, 2.72f, curve),
                        Mathf.Lerp(1.1f, 1.43f, curve), Mathf.Lerp(5.2f, 4.95f, curve));
                    focus = face.position + Vector3.up * 0.15f;
                    fieldOfView = 36f;
                    break;
                default:
                    float angle = Mathf.Lerp(57f, 168f, curve) * Mathf.Deg2Rad;
                    position = new Vector3(origin.x + 4.12f * Mathf.Sin(angle),
                        subject.position.y + Mathf.Lerp(2.38f, 2.65f, curve),
                        subject.position.z + 5.7f * Mathf.Cos(angle));
                    focus += Vector3.down * 0.25f;
                    fieldOfView = 61f;
                    break;
            }

            camera.fieldOfView = fieldOfView;
            camera.transform.SetPositionAndRotation(position, Quaternion.LookRotation(focus - position, Vector3.up));
        }

        private static float VerifyBoneMotion(GameObject model, AnimationClip clip, Transform bone)
        {
            clip.SampleAnimation(model, 0f);
            Quaternion start = bone.localRotation;
            float rotation = 0f;
            for (int i = 1; i < 8; i++)
            {
                clip.SampleAnimation(model, clip.length * i / 8f);
                rotation = Mathf.Max(rotation, Quaternion.Angle(start, bone.localRotation));
            }

            if (rotation < 0.1f)
                throw new InvalidOperationException($"Clip {clip.name} did not animate {bone.name} on {model.name}.");
            return rotation;
        }

        private static AnimationClip FindClip(AnimationClip[] clips, string name)
        {
            AnimationClip[] matches = clips.Where(clip => clip.name == name ||
                clip.name.EndsWith("|" + name, StringComparison.Ordinal) ||
                clip.name.EndsWith(":" + name, StringComparison.Ordinal)).ToArray();
            if (matches.Length != 1 || matches[0].length <= 0f)
                throw new InvalidOperationException($"Expected one non-empty {name} clip in {DinosaurFbx}. Found: {string.Join(", ", clips.Select(clip => clip.name))}.");
            return matches[0];
        }

        private static string GetArgument(string[] args, string name, string fallback)
        {
            int index = Array.IndexOf(args, name);
            if (index < 0)
                return fallback;
            if (index + 1 == args.Length || args[index + 1].StartsWith("-", StringComparison.Ordinal))
                throw new ArgumentException($"{name} requires a value.");
            return args[index + 1];
        }

        private static string NewOutputDirectory()
        {
            string project = Directory.GetParent(Application.dataPath).FullName;
            return Path.Combine(project, "Render", $"unity-{DateTime.Now:yyyyMMdd-HHmmss}-{Guid.NewGuid().ToString("N").Substring(0, 8)}");
        }

        private readonly struct TransformPose
        {
            private readonly Transform node;
            private readonly Quaternion rotation;
            private readonly Vector3 scale;
            public Vector3 LocalPosition { get; }

            public TransformPose(Transform node)
            {
                this.node = node;
                LocalPosition = node.localPosition;
                rotation = node.localRotation;
                scale = node.localScale;
            }

            public void Restore()
            {
                node.localPosition = LocalPosition;
                node.localRotation = rotation;
                node.localScale = scale;
            }
        }

        [Serializable]
        private sealed class CaptureReport
        {
            public string unityVersion;
            public string graphicsDevice;
            public string graphicsApi;
            public string colorSpace;
            public int width;
            public int height;
            public int frameRate;
            public int frameCount;
            public bool smoke;
            public string[] skinnedParts;
            public ShotReport[] shots;
        }

        [Serializable]
        private sealed class ShotReport
        {
            public string name;
            public string clip;
            public string measuredBone;
            public float clipSeconds;
            public float boneRotationDegrees;
            public int firstFrame;
            public int frameCount;
            public int sourceFrames;
            public Vector3 anchorStart;
            public Vector3 anchorEnd;
            public Vector3 cameraStart;
            public Vector3 cameraEnd;
        }
    }
}
