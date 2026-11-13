using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Rendering;
using UnityEngine.Rendering.HighDefinition;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace Dinosaug
{
    public static class CreatureRigStudio
    {
        private const string Folder = "Assets/Generated/CreatureActions";
        private const string ControllerPath = Folder + "/Stegosaur.controller";
        private const string CreatureScene = "Assets/Scenes/StegosaurCreatureForest.unity";
        private const string Fbx = "Assets/Source/Dinosaur/CreatureStegosaur.fbx";
        private const string MotionManifest = "Assets/Source/Dinosaur/CreatureMotion.json";
        private static readonly string[] TailBones = { "Bone_021", "Bone_020", "Bone_019", "Bone_018", "Bone_017" };

        [Serializable] private sealed class MotionData
        {
            public int fps;
            public float assetLength;
            public ClipSpec[] clips;
            public Shot[] shots;
            public FootSpec[] feet;
        }

        [Serializable] private sealed class ClipSpec
        {
            public string name;
            public int intervals;
            public float speed;
            public float turnDegreesPerSecond;
            public bool cyclic;
            public Shape[] shapes;
        }

        [Serializable] private sealed class Shape
        {
            public string name;
            public float[] values;
        }

        [Serializable] private sealed class Shot
        {
            public string id, clip, camera;
            public int frames;
            public float x, z;
        }

        [Serializable] private sealed class FootSpec
        {
            public string id, bone;
            public float x, z;
        }

        [Serializable] private sealed class Report
        {
            public string unityVersion, graphicsDevice, graphicsApi, sourceFbxSha256;
            public int width, height, fps, frames, skinnedParts, deformBones, maxSkinInfluences, importedTriangles;
            public string skinQuality;
            public string renderPipeline, skinShader, diffusionProfile;
            public bool nativeSubsurface;
            public bool smoke, partial;
            public int timelineFrames;
            public ClipCheck[] clips;
            public ShotCheck[] shots;
            public GroundCheck[] ground;
            public string[] statesExercised;
            public TailCheck tailPhysics;
        }

        [Serializable] private sealed class ClipCheck
        {
            public string name;
            public float seconds;
            public int boneCurves, blendShapeCurves;
            public float jawPeakDegrees, jawEndDegrees, torsoPeakDegrees, tailTipPeakDegrees;
            public float blinkPeakPercent, eyeTrackPeakPercent;
        }

        [Serializable] private sealed class ShotCheck
        {
            public string id, clip, camera;
            public int firstFrame, frames, sourceFrames;
            public int[] capturedTimelineFrames;
            public float pathDistanceM, headingDegrees, cameraDistanceM, jawPeakDegrees;
            public int jawPeakFrame;
            public float skinWrinkleFirst, skinWrinklePeak, skinWrinkleLast;
            public float maxTailTipDisplacementM, maxTailLengthErrorM, maxTailBendLimitExcessDegrees, maxTailAddedBendDegrees;
            public float minTailVirtualTipHeightM = float.PositiveInfinity;
            public int peakTailDisplacementFrame;
            public TurnDeformationReport turnDeformation;
        }

        [Serializable] private sealed class TailCheck
        {
            public string status;
            public string[] bones;
            public int solverHz;
            public float dampingRatio, tipLengthFraction;
            public float[] naturalFrequenciesRadiansPerSecond, bendLimitsDegrees;
            public float appliedStepDegrees, initialLagDegrees, initialTipOffsetM, settledTipOffsetM, deterministicReplayErrorM;
        }

        [Serializable] private sealed class GroundCheck
        {
            public string foot;
            public float slopeHeight, errorBeforeM, errorAfterM, swingHeight, swingErrorAfterM;
        }

        [MenuItem("Dinosaug/Creature Rig/Build Playable Forest")]
        public static void BuildFromMenu()
        {
            var data = LoadData();
            Prepare(data, true);
        }

        [MenuItem("Dinosaug/Creature Rig/Render Nine Motions")]
        public static void RenderFromMenu() => Capture(NewOutput(), 1280, 720, false, false);

        public static void RenderBatch()
        {
            string[] args = Environment.GetCommandLineArgs();
            string Value(string key, string fallback)
            {
                int index = Array.IndexOf(args, key);
                return index >= 0 ? args[index + 1] : fallback;
            }

            Capture(Value("-creature-output", NewOutput()),
                int.Parse(Value("-creature-width", "1280"), CultureInfo.InvariantCulture),
                int.Parse(Value("-creature-height", "720"), CultureInfo.InvariantCulture),
                args.Contains("-creature-smoke"), args.Contains("-creature-rebuild"),
                Value("-creature-shots", null)?.Split(','));
        }

        private static string NewOutput() => Path.GetFullPath(Path.Combine("Render", "creature-" + DateTime.Now.ToString("yyyyMMdd-HHmmss")));

        private static MotionData LoadData()
        {
            var asset = AssetDatabase.LoadAssetAtPath<TextAsset>(MotionManifest);
            var data = asset ? JsonUtility.FromJson<MotionData>(asset.text) : null;
            if (data == null || data.fps != 24 || data.clips.Length != 9 || data.feet.Length != 4 || data.shots.Length != 9)
                throw new InvalidDataException($"Incomplete motion manifest at {MotionManifest}");
            return data;
        }

        private static (GameObject model, StegosaurMotion driver, Dictionary<string, AnimationClip> clips) Prepare(
            MotionData data, bool rebuild)
        {
            if (rebuild || !AssetDatabase.LoadAssetAtPath<SceneAsset>(CreatureScene))
            {
                ForestSceneBuilder.Build();
                if (SceneManager.GetActiveScene().path != ForestSceneBuilder.ScenePath)
                    throw new OperationCanceledException("The forest scene was not rebuilt.");
                if (!EditorSceneManager.SaveScene(SceneManager.GetActiveScene(), CreatureScene))
                    throw new IOException($"Could not create {CreatureScene}.");
            }
            else if (SceneManager.GetActiveScene().path != CreatureScene)
            {
                if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo())
                    throw new OperationCanceledException();
                EditorSceneManager.OpenScene(CreatureScene, OpenSceneMode.Single);
            }

            Scene scene = SceneManager.GetActiveScene();
            GameObject anchor = scene.GetRootGameObjects().Single(item => item.name == "Stegosaur");
            GameObject model = anchor.transform.GetChild(0).gameObject;
            var animator = model.GetComponent<Animator>();
            if (!animator)
                throw new InvalidDataException("The imported creature must use a Generic Animator.");
            QualitySettings.skinWeights = SkinWeights.Unlimited;
            var pieces = model.GetComponentsInChildren<SkinnedMeshRenderer>();
            foreach (var piece in pieces)
            {
                piece.quality = SkinQuality.Auto;
                PrefabUtility.RecordPrefabInstancePropertyModifications(piece);
            }
            var skeleton = model.GetComponentsInChildren<Transform>().ToDictionary(node => node.name);
            var ground = scene.GetRootGameObjects().Single(item => item.name == "Environment")
                .transform.Find("Leaf-litter Ground").gameObject;
            if (!ground.TryGetComponent(out MeshCollider collider))
                collider = ground.AddComponent<MeshCollider>();
            collider.sharedMesh = ground.GetComponent<MeshFilter>().sharedMesh;
            if (!anchor.TryGetComponent(out StegosaurMotion driver))
                driver = anchor.AddComponent<StegosaurMotion>();

            Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
            var imported = AssetDatabase.LoadAllAssetRepresentationsAtPath(Fbx).OfType<AnimationClip>().ToArray();
            var bindings = new List<(string name, EditorCurveBinding curve)>();
            foreach (SkinnedMeshRenderer part in pieces)
            {
                Mesh mesh = part.sharedMesh;
                string path = AnimationUtility.CalculateTransformPath(part.transform, model.transform);
                for (int index = 0; index < mesh.blendShapeCount; index++)
                {
                    string unityName = mesh.GetBlendShapeName(index);
                    string shortName = unityName.Substring(unityName.LastIndexOf('.') + 1);
                    bindings.Add((shortName, EditorCurveBinding.FloatCurve(path, typeof(SkinnedMeshRenderer), "blendShape." + unityName)));
                }
            }

            foreach (var shape in data.clips[0].shapes)
                if (!bindings.Any(binding => binding.name == shape.name))
                    throw new InvalidDataException($"Unity did not import the {shape.name} blend shape. Seen: {string.Join(", ", bindings.Select(b => b.name))}");

            var clips = new Dictionary<string, AnimationClip>();
            foreach (ClipSpec spec in data.clips)
            {
                AnimationClip[] matches = imported.Where(clip => clip.name == spec.name ||
                    clip.name.EndsWith("|" + spec.name, StringComparison.Ordinal)).ToArray();
                if (matches.Length != 1 || Mathf.Abs(matches[0].length - spec.intervals / (float)data.fps) > .045f)
                    throw new InvalidDataException($"Expected {spec.name} ({spec.intervals} intervals) in {Fbx}. " +
                        $"Found {string.Join(", ", imported.Select(clip => clip.name + " " + clip.length))}.");
                AnimationClip source = Object.Instantiate(matches[0]);
                source.name = spec.name;
                var settings = AnimationUtility.GetAnimationClipSettings(source);
                settings.loopTime = spec.cyclic;
                AnimationUtility.SetAnimationClipSettings(source, settings);
                foreach (var (name, binding) in bindings)
                {
                    Shape shape = spec.shapes.SingleOrDefault(item => item.name == name);
                    if (shape == null)
                        continue;
                    if (shape.values.Length != spec.intervals + 1)
                        throw new InvalidDataException($"Wrong number of shape samples in {spec.name}/{name}.");
                    var keys = new Keyframe[shape.values.Length];
                    for (int index = 0; index < keys.Length; index++)
                        keys[index] = new Keyframe(index / (float)data.fps, shape.values[index]);
                    var curve = new AnimationCurve(keys);
                    for (int index = 0; index < keys.Length; index++)
                    {
                        AnimationUtility.SetKeyLeftTangentMode(curve, index, AnimationUtility.TangentMode.Linear);
                        AnimationUtility.SetKeyRightTangentMode(curve, index, AnimationUtility.TangentMode.Linear);
                    }
                    AnimationUtility.SetEditorCurve(source, binding, curve);
                }

                string path = $"{Folder}/{spec.name}.anim";
                var existing = AssetDatabase.LoadAssetAtPath<AnimationClip>(path);
                if (existing)
                {
                    EditorUtility.CopySerialized(source, existing);
                    Object.DestroyImmediate(source);
                    EditorUtility.SetDirty(existing);
                }
                else
                {
                    AssetDatabase.CreateAsset(source, path);
                    existing = source;
                }
                clips.Add(spec.name, existing);
            }

            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(ControllerPath);
            if (!controller)
                controller = AnimatorController.CreateAnimatorControllerAtPath(ControllerPath);
            var machine = controller.layers[0].stateMachine;
            foreach (var oldState in machine.states)
                machine.RemoveState(oldState.state);
            foreach (var oldTransition in machine.anyStateTransitions)
                machine.RemoveAnyStateTransition(oldTransition);
            controller.parameters = Array.Empty<AnimatorControllerParameter>();
            controller.AddParameter("Motion", AnimatorControllerParameterType.Int);
            var states = new Dictionary<string, AnimatorState>();
            for (int i = 0; i < data.clips.Length; i++)
            {
                ClipSpec spec = data.clips[i];
                var node = machine.AddState(spec.name, new Vector3(230 + 240 * (i % 3), 80 + 90 * (i / 3), 0));
                node.motion = clips[spec.name];
                states.Add(spec.name, node);
            }
            machine.defaultState = states["Idle"];
            var gaits = Enum.GetNames(typeof(StegosaurMotion.Gait));
            foreach (string from in gaits)
                for (int index = 0; index < gaits.Length; index++)
                    if (gaits[index] != from)
                    {
                        var transition = states[from].AddTransition(states[gaits[index]]);
                        transition.hasExitTime = false;
                        transition.hasFixedDuration = true;
                        transition.duration = .16f;
                        transition.AddCondition(AnimatorConditionMode.Equals, index, "Motion");
                    }

            foreach (string action in Enum.GetNames(typeof(StegosaurMotion.Action)))
            {
                controller.AddParameter(action, AnimatorControllerParameterType.Trigger);
                var enter = machine.AddAnyStateTransition(states[action]);
                enter.hasExitTime = false;
                enter.hasFixedDuration = true;
                enter.duration = .11f;
                enter.canTransitionToSelf = false;
                enter.AddCondition(AnimatorConditionMode.If, 0f, action);
                var exit = states[action].AddTransition(states["Idle"]);
                exit.hasExitTime = true;
                exit.exitTime = .97f;
                exit.hasFixedDuration = true;
                exit.duration = .15f;
            }

            var legRoots = new Dictionary<string, (string thigh, string shin, string endpoint)>
            {
                ["FL"] = ("Bone_034", "Bone_033", "Bone_032"),
                ["FR"] = ("Bone_029", "Bone_028", "Bone_027"),
                ["HL"] = ("Bone_010", "Bone_009", "Bone_008"),
                ["HR"] = ("Bone_016", "Bone_015", "Bone_014")
            };
            var feet = data.feet.Select(spec =>
            {
                var chain = legRoots[spec.id];
                Transform pad = skeleton[spec.bone];
                return new StegosaurMotion.Foot
                {
                    id = spec.id,
                    thigh = skeleton[chain.thigh], shin = skeleton[chain.shin], endpoint = skeleton[chain.endpoint], pad = pad,
                    soleInPad = pad.InverseTransformPoint(anchor.transform.TransformPoint(new Vector3(spec.x, 0, spec.z)))
                };
            }).ToArray();

            animator.runtimeAnimatorController = controller;
            animator.applyRootMotion = false;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            Transform[] tail = TailBones.Select(name => skeleton[name]).ToArray();
            for (int i = 1; i < tail.Length; i++)
                if (tail[i].parent != tail[i - 1] || Vector3.Distance(tail[i].position, tail[i - 1].position) < .001f)
                    throw new InvalidDataException($"Invalid physical tail chain at {tail[i].name}.");
            Vector3 tipExtension = (tail[4].position - tail[3].position) * TailSpring.TipLengthFraction;
            driver.Configure(animator, data.clips.Select(spec => new StegosaurMotion.PathMotion
            {
                name = spec.name, speed = spec.speed, turnDegreesPerSecond = spec.turnDegreesPerSecond
            }).ToArray(), feet, collider, Camera.main.transform, tail, tail[4].InverseTransformVector(tipExtension),
                skeleton["LowerJaw"], pieces.Where(piece => piece.name == "HeadNeck" || piece.name == "MouthInterior")
                    .Cast<Renderer>().ToArray());
            PrefabUtility.RecordPrefabInstancePropertyModifications(animator);
            EditorUtility.SetDirty(controller);
            EditorSceneManager.MarkSceneDirty(scene);
            if (!EditorSceneManager.SaveScene(scene))
                throw new IOException("Could not save the playable forest scene.");
            AssetDatabase.SaveAssets();
            return (model, driver, clips);
        }

        private static string[] VerifyController(Animator animator, RuntimeAnimatorController controller)
        {
            var states = new List<string>();
            PlayableGraph graph = PlayableGraph.Create("Creature Animator smoke test");
            try
            {
                var player = AnimatorControllerPlayable.Create(graph, controller);
                AnimationPlayableOutput.Create(graph, "Creature Animator", animator).SetSourcePlayable(player);
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                graph.Play();
                void Expect(string name)
                {
                    if (!player.GetCurrentAnimatorStateInfo(0).IsName(name))
                        throw new InvalidDataException($"Animator did not enter {name} (state hash {player.GetCurrentAnimatorStateInfo(0).shortNameHash}).");
                    states.Add(name);
                }

                void Advance(float seconds)
                {
                    for (int i = 0; i < Mathf.CeilToInt(seconds * 60f); i++)
                        graph.Evaluate(1f / 60f);
                }

                Advance(.12f);
                Expect("Idle");
                player.SetInteger("Motion", 1);
                Advance(.28f);
                Expect("Walk");
                player.SetInteger("Motion", 2);
                Advance(.28f);
                Expect("Run");
                player.SetInteger("Motion", 0);
                Advance(.28f);
                Expect("Idle");
                foreach (string action in Enum.GetNames(typeof(StegosaurMotion.Action)))
                {
                    player.SetTrigger(action);
                    Advance(.25f);
                    Expect(action);
                    Advance(4f);
                    Expect("Idle");
                }
            }
            finally
            {
                graph.Destroy();
            }
            return states.Distinct().ToArray();
        }

        private static TailCheck VerifyTail(StegosaurMotion driver, Transform tailBase)
        {
            const float turn = 30;
            const float dt = 1f / 24;
            const int settleFrames = 48;
            var root = driver.transform;
            Quaternion start = root.rotation;
            Vector3[] Replay(out float initialLag, out float initialOffset, out float settledOffset)
            {
                driver.ResetTailMotion();
                root.rotation = start;
                driver.SampleTailMotion(0);
                var positions = new Vector3[settleFrames];
                initialLag = initialOffset = settledOffset = 0;
                for (int frame = 0; frame < settleFrames; frame++)
                {
                    driver.RestoreAnimatedTail();
                    root.rotation = Quaternion.AngleAxis(turn, Vector3.up) * start;
                    TailPoseMeasurement sample = driver.SampleTailMotion(dt);
                    if (sample.MaxLengthErrorM > .001f || sample.MaxBendLimitExcessDegrees > .05f)
                        throw new InvalidDataException("Physical tail lost its segment length or exceeded a bend limit.");
                    positions[frame] = sample.SimulatedTip;
                    if (frame == 0)
                    {
                        Vector3 animated = Vector3.ProjectOnPlane(sample.AnimatedTip - tailBase.position, Vector3.up);
                        Vector3 simulated = Vector3.ProjectOnPlane(sample.SimulatedTip - tailBase.position, Vector3.up);
                        initialLag = Vector3.SignedAngle(animated, simulated, Vector3.up);
                        initialOffset = sample.TipDisplacementM;
                    }
                    settledOffset = sample.TipDisplacementM;
                }
                return positions;
            }

            Vector3[] first = Replay(out float lag, out float offset, out float settled);
            Vector3[] repeat = Replay(out _, out _, out _);
            driver.ResetTailMotion();
            root.rotation = start;
            float replayError = first.Zip(repeat, Vector3.Distance).Max();
            if (lag >= -1f || offset < .03f || settled > .003f || replayError > .00001f)
                throw new InvalidDataException($"Physical tail response failed: lag {lag:F2}°, offset {offset:F4} m, " +
                    $"settled {settled:F6} m, replay error {replayError:F8} m.");
            return new TailCheck
            {
                status = "PASS", bones = TailBones, solverHz = TailSpring.TicksPerSecond,
                dampingRatio = TailSpring.DampingRatio, tipLengthFraction = TailSpring.TipLengthFraction,
                naturalFrequenciesRadiansPerSecond = TailSpring.GetNaturalFrequencies(),
                bendLimitsDegrees = TailSpring.GetBendLimits(),
                appliedStepDegrees = turn, initialLagDegrees = lag, initialTipOffsetM = offset,
                settledTipOffsetM = settled, deterministicReplayErrorM = replayError
            };
        }

        private static GroundCheck[] VerifyTerrain(StegosaurMotion driver, GameObject model, AnimationClip idle, AnimationClip walk)
        {
            var fixture = new GameObject("Ground verification plane", typeof(BoxCollider)) { hideFlags = HideFlags.HideAndDontSave };
            fixture.transform.SetPositionAndRotation(driver.transform.position + Vector3.up * .05f, Quaternion.Euler(3f, 0f, -3f));
            BoxCollider slope = fixture.GetComponent<BoxCollider>();
            slope.center = Vector3.down * .05f;
            slope.size = new Vector3(9f, .1f, 9f);
            Collider previous = driver.Terrain;
            float Height(Vector3 sole)
            {
                if (!slope.Raycast(new Ray(sole + 2f * Vector3.up, Vector3.down), out RaycastHit hit, 4f))
                    throw new InvalidDataException("The controlled slope did not cover every foot.");
                return hit.point.y;
            }

            try
            {
                driver.Terrain = slope;
                Physics.SyncTransforms();
                idle.SampleAnimation(model, .25f);
                var checks = driver.Feet.Select(foot => new GroundCheck
                {
                    foot = foot.id,
                    slopeHeight = Height(foot.Sole),
                    errorBeforeM = Mathf.Abs(foot.Sole.y - Height(foot.Sole))
                }).ToArray();
                driver.FitFeetToGround();
                for (int i = 0; i < driver.Feet.Length; i++)
                {
                    var foot = driver.Feet[i];
                    checks[i].errorAfterM = Mathf.Abs(foot.Sole.y - Height(foot.Sole));
                    if (checks[i].errorAfterM > .018f)
                        throw new InvalidDataException($"Terrain fit missed {foot.id} by {checks[i].errorAfterM:F3} m " +
                            $"(before {checks[i].errorBeforeM:F3} m).");
                }

                for (int i = 0; i < driver.Feet.Length; i++)
                {
                    var foot = driver.Feet[i];
                    float bestHeight = 0f, bestTime = 0f;
                    for (int frame = 0; frame < Mathf.RoundToInt(walk.length * 24f); frame++)
                    {
                        float time = frame / 24f;
                        walk.SampleAnimation(model, time);
                        float raised = foot.Sole.y - driver.transform.position.y;
                        if (raised > bestHeight)
                        {
                            bestTime = time;
                            bestHeight = raised;
                        }
                    }
                    walk.SampleAnimation(model, bestTime);
                    driver.FitFeetToGround();
                    checks[i].swingHeight = bestHeight;
                    checks[i].swingErrorAfterM = Mathf.Abs((foot.Sole.y - Height(foot.Sole)) - bestHeight);
                    if (bestHeight < .06f || checks[i].swingErrorAfterM > .025f)
                        throw new InvalidDataException($"Terrain fit changed {foot.id} swing clearance " +
                            $"by {checks[i].swingErrorAfterM:F3} m (lift {bestHeight:F3} m).");
                }
                return checks;
            }
            finally
            {
                driver.Terrain = previous;
                Object.DestroyImmediate(fixture);
            }
        }

        private static void Capture(string output, int width, int height, bool smoke, bool rebuild, string[] shotIds = null)
        {
            if (width < 64 || height < 64 || width % 2 != 0 || height % 2 != 0 ||
                SystemInfo.graphicsDeviceType == GraphicsDeviceType.Null || EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Exit Play mode, use even render dimensions, and launch Unity with a graphics device.");
            output = Path.GetFullPath(output);
            string frames = Path.Combine(output, "frames");
            if (Directory.Exists(frames))
                throw new IOException($"Capture frames already exist in {frames}.");

            MotionData data = LoadData();
            if (shotIds != null && (shotIds.Distinct(StringComparer.Ordinal).Count() != shotIds.Length ||
                shotIds.Any(id => !data.shots.Any(shot => shot.id == id))))
                throw new ArgumentException("Shot IDs must be distinct and present in the motion manifest.");
            var selected = data.shots.Where(shot => shotIds == null || shotIds.Contains(shot.id, StringComparer.Ordinal)).ToArray();
            var (model, driver, clips) = Prepare(data, rebuild);
            var animator = model.GetComponent<Animator>();
            var subject = driver.transform;
            var bones = model.GetComponentsInChildren<Transform>();
            var pieces = model.GetComponentsInChildren<SkinnedMeshRenderer>();
            Transform Bone(string name) => bones.Single(node => node.name == name);
            Transform jaw = Bone("LowerJaw");
            Transform head = Bone("Bone_023");
            Transform chest = Bone("Bone_002");
            Transform tip = Bone("Bone_017");
            if (jaw.parent.name != "Bone_022" ||
                new[] { "HeadNeck", "MouthInterior", "Tongue" }.Any(name => !pieces.Single(part => part.name == name).bones.Contains(jaw)))
                throw new InvalidDataException("The head skin, mouth lining and tongue must all use the independent jaw bone.");
            var face = pieces.Single(part => part.name == "HeadNeck");
            var shapeIndices = Enumerable.Range(0, face.sharedMesh.blendShapeCount).ToDictionary(index =>
            {
                string name = face.sharedMesh.GetBlendShapeName(index);
                return name.Substring(name.LastIndexOf('.') + 1);
            });
            int blinkIndex = shapeIndices["RoarBlink"], eyeTrackIndex = shapeIndices["EyeTrack"];

            var rest = bones.Select(node => (node, p: node.localPosition, r: node.localRotation, s: node.localScale)).ToArray();
            var origin = (subject.position, subject.rotation);
            void Restore()
            {
                driver.ResetTailMotion();
                subject.SetPositionAndRotation(origin.position, origin.rotation);
                foreach (var (node, p, r, s) in rest)
                {
                    node.localPosition = p;
                    node.localRotation = r;
                    node.localScale = s;
                }
                foreach (var piece in pieces)
                    for (int index = 0; index < piece.sharedMesh.blendShapeCount; index++)
                        piece.SetBlendShapeWeight(index, 0f);
            }

            var report = new Report
            {
                unityVersion = Application.unityVersion,
                graphicsDevice = SystemInfo.graphicsDeviceName,
                graphicsApi = SystemInfo.graphicsDeviceType.ToString(),
                width = width, height = height, fps = data.fps, smoke = smoke, skinnedParts = pieces.Length,
                deformBones = bones.Count(node => node.name.StartsWith("Bone_", StringComparison.Ordinal) || node == jaw),
                skinQuality = QualitySettings.skinWeights.ToString(),
                statesExercised = VerifyController(animator, animator.runtimeAnimatorController),
                timelineFrames = data.shots.Sum(shot => shot.frames), partial = shotIds != null,
                shots = new ShotCheck[selected.Length]
            };
            using (var hasher = SHA256.Create())
            using (var file = File.OpenRead(Fbx))
                report.sourceFbxSha256 = BitConverter.ToString(hasher.ComputeHash(file)).Replace("-", "").ToLowerInvariant();

            foreach (var piece in pieces)
            {
                foreach (byte count in piece.sharedMesh.GetBonesPerVertex())
                    report.maxSkinInfluences = Math.Max(count, report.maxSkinInfluences);
                for (int submesh = 0; submesh < piece.sharedMesh.subMeshCount; submesh++)
                    report.importedTriangles += (int)piece.sharedMesh.GetIndexCount(submesh) / 3;
            }
            if (report.maxSkinInfluences < 2)
                throw new InvalidDataException("The imported skin lost its multi-bone weights.");

            Restore();
            animator.enabled = false;
            var restEnabled = pieces.Select(part => part.enabled).ToArray();
            RenderTexture previousTexture = RenderTexture.active;
            RenderTexture target = null;
            Texture2D pixels = null;
            GameObject cameraObject = null;
            var proxy = new (Mesh mesh, MeshFilter filter, MeshRenderer renderer)[pieces.Length];
            var deformation = new TurnDeformationCheck[pieces.Length];
            try
            {
                report.ground = VerifyTerrain(driver, model, clips["Idle"], clips["Walk"]);
                Restore();
                report.tailPhysics = VerifyTail(driver, Bone(TailBones[0]));
                Restore();
                report.clips = data.clips.Select(spec =>
                {
                    AnimationClip clip = clips[spec.name];
                    Restore();
                    clip.SampleAnimation(model, 0f);
                    Quaternion closed = jaw.localRotation, startChest = chest.rotation, startTail = tip.rotation;
                    var curves = AnimationUtility.GetCurveBindings(clip);
                    var result = new ClipCheck
                    {
                        name = spec.name, seconds = clip.length,
                        boneCurves = curves.Count(curve => curve.type == typeof(Transform)),
                        blendShapeCurves = curves.Count(curve => curve.type == typeof(SkinnedMeshRenderer))
                    };
                    for (int frame = 0; frame <= spec.intervals; frame++)
                    {
                        clip.SampleAnimation(model, frame / (float)data.fps);
                        result.jawPeakDegrees = Mathf.Max(result.jawPeakDegrees, Quaternion.Angle(closed, jaw.localRotation));
                        result.torsoPeakDegrees = Mathf.Max(result.torsoPeakDegrees, Quaternion.Angle(startChest, chest.rotation));
                        result.tailTipPeakDegrees = Mathf.Max(result.tailTipPeakDegrees, Quaternion.Angle(startTail, tip.rotation));
                        result.blinkPeakPercent = Mathf.Max(result.blinkPeakPercent, face.GetBlendShapeWeight(blinkIndex));
                        result.eyeTrackPeakPercent = Mathf.Max(result.eyeTrackPeakPercent, face.GetBlendShapeWeight(eyeTrackIndex));
                    }
                    result.jawEndDegrees = Quaternion.Angle(closed, jaw.localRotation);
                    if (result.boneCurves == 0 || result.blendShapeCurves < 5 ||
                        (spec.name == "Roar" && (result.jawPeakDegrees < 25f || result.jawEndDegrees > 1f ||
                        result.blinkPeakPercent < 85f || result.eyeTrackPeakPercent < 70f)))
                        throw new InvalidDataException($"Animation verification failed for {spec.name}: {JsonUtility.ToJson(result)}");
                    return result;
                }).ToArray();
                Restore();

                Camera sceneCamera = Camera.main;
                cameraObject = new GameObject("Creature capture camera") { hideFlags = HideFlags.HideAndDontSave };
                var camera = cameraObject.AddComponent<Camera>();
                camera.CopyFrom(sceneCamera);
                camera.enabled = false;
                var hdCamera = StegosaurHdrp.ConfigureCamera(camera);
                camera.usePhysicalProperties = false;
                camera.aspect = width / (float)height;
                target = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
                camera.targetTexture = target;
                pixels = new Texture2D(width, height, TextureFormat.RGB24, false, false);
                var request = new RenderPipeline.StandardRequest { destination = target };
                byte[] RenderFrame()
                {
                    RenderPipeline.SubmitRenderRequest(camera, request);
                    RenderTexture.active = target;
                    pixels.ReadPixels(new Rect(0, 0, width, height), 0, 0, false);
                    return pixels.EncodeToPNG();
                }
                for (int i = 0; i < pieces.Length; i++)
                {
                    var source = pieces[i];
                    var obj = new GameObject(source.name + " Capture") { hideFlags = HideFlags.HideAndDontSave, layer = source.gameObject.layer };
                    obj.transform.SetParent(source.transform, false);
                    var mesh = new Mesh { hideFlags = HideFlags.HideAndDontSave };
                    mesh.MarkDynamic();
                    var renderer = obj.AddComponent<MeshRenderer>();
                    renderer.sharedMaterials = source.sharedMaterials;
                    renderer.receiveShadows = source.receiveShadows;
                    renderer.shadowCastingMode = source.shadowCastingMode;
                    renderer.reflectionProbeUsage = source.reflectionProbeUsage;
                    renderer.lightProbeUsage = source.lightProbeUsage;
                    renderer.enabled = false;
                    proxy[i] = (mesh, obj.AddComponent<MeshFilter>(), renderer);
                    source.BakeMesh(mesh, useScale: true);
                    deformation[i] = new TurnDeformationCheck(source.transform, mesh, source.sharedMesh);
                }

                Directory.CreateDirectory(frames);
                int frameOut = 0, timelineFrame = 0, resultIndex = 0;
                for (int shotIndex = 0; shotIndex < data.shots.Length; shotIndex++)
                {
                    Shot shot = data.shots[shotIndex];
                    int start = timelineFrame;
                    timelineFrame += shot.frames;
                    if (!selected.Contains(shot))
                        continue;
                    ClipSpec spec = data.clips.Single(clip => clip.name == shot.clip);
                    AnimationClip clip = clips[shot.clip];
                    Restore();
                    clip.SampleAnimation(model, 0);
                    Quaternion closed = jaw.localRotation;
                    var result = new ShotCheck { id = shot.id, clip = shot.clip, camera = shot.camera,
                        firstFrame = report.partial ? start : frameOut, sourceFrames = shot.frames };
                    int capturedBeforeShot = frameOut;
                    var capturedTimeline = new List<int>();
                    if (shot.clip == "TurnLeft" || shot.clip == "TurnRight")
                    {
                        result.turnDeformation = new TurnDeformationReport
                        {
                            trianglesPerFrame = deformation.Sum(check => check.TriangleCount),
                            trianglesExcludedPerFrame = deformation.Sum(check => check.Excluded),
                            restAreaCutoffSquareM = TurnDeformationCheck.RestAreaCutoffSquareM,
                            parts = deformation.Select(check => check.BeginShot()).ToArray()
                        };
                        if (result.turnDeformation.trianglesPerFrame < .99f * report.importedTriangles)
                            throw new InvalidDataException("Less than 99% of imported turn triangles exceeded the minimum rest area.");
                    }
                    Vector3 firstCamera = Vector3.zero;
                    int sampleMiddle = shot.clip == "Bite" ? Mathf.RoundToInt(.32f * shot.frames) : shot.frames / 2;
                    var sampleFrames = new HashSet<int> { 0, sampleMiddle, shot.frames - 1 };
                    if (smoke && report.partial)
                    {
                        // Keep the known late-turn outlier poses and sample the opening, peak and recovery of the roar.
                        switch (shot.clip)
                        {
                            case "Run": sampleFrames.UnionWith(new[] { 38, 43, 48, 54, 61 }); break;
                            case "TurnLeft": sampleFrames.UnionWith(new[] { 21, 63, 74, 82 }); break;
                            case "TurnRight": sampleFrames.UnionWith(new[] { 21, 63, 68 }); break;
                            case "Roar": sampleFrames.UnionWith(new[] { 8, 11, 18, 45, 54, 63, 68 }); break;
                        }
                    }
                    for (int frame = 0; frame < shot.frames; frame++)
                    {
                        if (!Application.isBatchMode && EditorUtility.DisplayCancelableProgressBar(
                                "Rendering creature motions", $"{shot.clip}  {frame + 1}/{shot.frames}", (shotIndex + frame / (float)shot.frames) / data.shots.Length))
                            throw new OperationCanceledException();
                        float seconds = frame / (float)data.fps;
                        float poseTime = spec.cyclic ? (frame % spec.intervals) / (float)data.fps : Mathf.Min(seconds, clip.length);
                        driver.RestoreAnimatedTail();
                        clip.SampleAnimation(model, poseTime);
                        // FBX channels are in-place. The calibrated trajectory lives on the parent.
                        model.transform.localPosition = rest[0].p;
                        model.transform.localRotation = rest[0].r;
                        model.transform.localScale = rest[0].s;
                        Vector3 path = StegosaurMotion.PathOffset(spec.speed, spec.turnDegreesPerSecond, seconds);
                        subject.SetPositionAndRotation(new Vector3(shot.x, origin.position.y, shot.z) + path,
                            Quaternion.Euler(0f, -spec.turnDegreesPerSecond * seconds, 0f));
                        driver.FitFeetToGround();
                        TailPoseMeasurement tail = driver.SampleTailMotion(frame == 0 ? 0 : 1f / data.fps);
                        if (tail.TipDisplacementM > result.maxTailTipDisplacementM)
                        {
                            result.maxTailTipDisplacementM = tail.TipDisplacementM;
                            result.peakTailDisplacementFrame = frame;
                        }
                        result.maxTailLengthErrorM = Mathf.Max(result.maxTailLengthErrorM, tail.MaxLengthErrorM);
                        result.maxTailBendLimitExcessDegrees = Mathf.Max(result.maxTailBendLimitExcessDegrees, tail.MaxBendLimitExcessDegrees);
                        result.maxTailAddedBendDegrees = Mathf.Max(result.maxTailAddedBendDegrees, tail.MaxAddedBendDegrees);
                        result.minTailVirtualTipHeightM = Mathf.Min(result.minTailVirtualTipHeightM, tail.SimulatedTip.y);
                        bool captureFrame = !smoke || sampleFrames.Contains(frame);
                        if (!captureFrame && result.turnDeformation == null)
                            continue;
                        if (result.turnDeformation != null)
                            result.turnDeformation.measuredFrames++;
                        for (int i = 0; i < pieces.Length; i++)
                        {
                            // Bake from the current pose before either deformation checks or Camera.Render.
                            pieces[i].BakeMesh(proxy[i].mesh, useScale: true);
                            if (result.turnDeformation != null)
                                deformation[i].Measure(proxy[i].mesh, frame, result.turnDeformation, result.turnDeformation.parts[i]);
                            if (!captureFrame)
                                continue;
                            proxy[i].mesh.RecalculateBounds();
                            if (frameOut == 0)
                            {
                                proxy[i].filter.sharedMesh = proxy[i].mesh;
                                proxy[i].renderer.enabled = restEnabled[i];
                                pieces[i].enabled = false;
                            }
                        }
                        if (!captureFrame)
                            continue;
                        Compose(shot.camera, frame / (float)(shot.frames - 1), subject, head, tip, camera);
                        float jawAngle = Quaternion.Angle(closed, jaw.localRotation);
                        if (jawAngle > result.jawPeakDegrees)
                        {
                            result.jawPeakDegrees = jawAngle;
                            result.jawPeakFrame = frame;
                        }
                        for (int i = 0; i < pieces.Length; i++)
                        {
                            if (pieces[i] != face && pieces[i].name != "MouthInterior")
                                continue;
                            float wrinkle = driver.ApplySkinPose(proxy[i].renderer, jawAngle);
                            result.skinWrinklePeak = Mathf.Max(result.skinWrinklePeak, wrinkle);
                            if (frame == 0)
                                result.skinWrinkleFirst = wrinkle;
                            if (frame == shot.frames - 1)
                                result.skinWrinkleLast = wrinkle;
                        }
                        if (frame == 0)
                            firstCamera = camera.transform.position;
                        if (frame == shot.frames - 1)
                        {
                            result.headingDegrees = -spec.turnDegreesPerSecond * seconds;
                            result.pathDistanceM = seconds * spec.speed;
                            result.cameraDistanceM = Vector3.Distance(firstCamera, camera.transform.position);
                        }
                        if (frameOut == 0)
                        {
                            for (int warm = 0; warm < 3; warm++)
                                RenderPipeline.SubmitRenderRequest(camera, request);
                            var skin = face.sharedMaterials.First(m => m.shader.name == StegosaurHdrp.SkinShader);
                            var profile = StegosaurHdrp.SkinProfile();
                            report.renderPipeline = RenderPipelineManager.currentPipeline?.GetType().Name;
                            report.skinShader = skin.shader.name;
                            report.diffusionProfile = profile.name;
                            report.nativeSubsurface = RenderPipelineManager.currentPipeline is HDRenderPipeline
                                && GraphicsSettings.currentRenderPipeline is HDRenderPipelineAsset asset
                                && asset.currentPlatformRenderPipelineSettings.supportSubsurfaceScattering
                                && skin.IsKeywordEnabled("_MATERIAL_FEATURE_SUBSURFACE_SCATTERING")
                                && skin.GetFloat("_DiffusionProfileHash") != 0f;
                            if (!report.nativeSubsurface)
                                throw new InvalidDataException("Native HDRP subsurface scattering was not initialized.");
                        }

                        var png = RenderFrame();
                        int number = report.partial ? start + frame : frameOut;
                        File.WriteAllBytes(Path.Combine(frames, $"frame_{number:D5}.png"), png);
                        if (shot.clip == "Roar" && frame == 45)
                        {
                            File.WriteAllBytes(Path.Combine(output, "sss-on.png"), png);
                            StegosaurHdrp.SetSubsurface(hdCamera, false);
                            try
                            {
                                File.WriteAllBytes(Path.Combine(output, "sss-off.png"), RenderFrame());
                            }
                            finally
                            {
                                StegosaurHdrp.SetSubsurface(hdCamera, true);
                            }
                        }
                        capturedTimeline.Add(start + frame);
                        frameOut++;
                    }

                    result.frames = frameOut - capturedBeforeShot;
                    result.capturedTimelineFrames = capturedTimeline.ToArray();
                    if (result.turnDeformation != null)
                        for (int i = 0; i < deformation.Length; i++)
                            deformation[i].FinishShot(result.turnDeformation.parts[i]);
                    if (result.maxTailLengthErrorM > .001f || result.maxTailBendLimitExcessDegrees > .05f ||
                        (shot.clip == "TailWhip" && result.maxTailTipDisplacementM < .08f))
                        throw new InvalidDataException($"Tail constraints or displacement failed in {shot.clip}.");
                    if (shot.clip == "Roar" && (result.skinWrinklePeak < .9f || result.skinWrinkleFirst != 0 || result.skinWrinkleLast != 0))
                        throw new InvalidDataException("The localized wrinkle weight should rise and fall with the roar.");
                    report.shots[resultIndex++] = result;
                    Debug.Log($"[Creature] {shot.clip}: {result.frames} frames, travelled {result.pathDistanceM:F2} m, turned {result.headingDegrees:F1} degrees.");
                }
                report.frames = frameOut;
                File.WriteAllText(Path.Combine(output, "creature-unity-report.json"), JsonUtility.ToJson(report, true) + Environment.NewLine);
                Debug.Log($"[Creature] Captured {frameOut} frames in {output}.");
            }
            finally
            {
                RenderTexture.active = previousTexture;
                if (cameraObject) Object.DestroyImmediate(cameraObject);
                if (target) RenderTexture.ReleaseTemporary(target);
                if (pixels) Object.DestroyImmediate(pixels);
                for (int i = 0; i < pieces.Length; i++)
                {
                    pieces[i].enabled = restEnabled[i];
                    if (proxy[i].filter) Object.DestroyImmediate(proxy[i].filter.gameObject);
                    if (proxy[i].mesh) Object.DestroyImmediate(proxy[i].mesh);
                }
                Restore();
                animator.enabled = true;
                if (!Application.isBatchMode)
                    EditorUtility.ClearProgressBar();
            }
        }

        private static void Compose(string view, float progress, Transform root, Transform head, Transform tip, Camera camera)
        {
            float t = Mathf.SmoothStep(0f, 1f, progress);
            Vector3 focus = root.position + new Vector3(0f, 1.37f, .13f);
            Vector3 position;
            float fov;
            switch (view)
            {
                case "Wide":
                    position = root.position + Vector3.Lerp(new Vector3(6.6f, 2.8f, 4.4f), new Vector3(5.9f, 2.5f, 4.0f), t);
                    fov = 45f;
                    break;
                case "Track":
                    position = root.position + Vector3.Lerp(new Vector3(-6.5f, 1.8f, 2.4f), new Vector3(-6.15f, 1.64f, 2.95f), t);
                    focus += Vector3.forward * .26f;
                    fov = 46f;
                    break;
                case "Low":
                    position = root.position + Vector3.Lerp(new Vector3(5.65f, 1.06f, -.55f), new Vector3(5.15f, 1.27f, 1.95f), t);
                    focus += Vector3.forward * Mathf.Lerp(-.1f, .48f, t) + Vector3.down * .31f;
                    fov = 51f;
                    break;
                case "High":
                    position = root.position + Vector3.Lerp(new Vector3(7.3f, 4.1f, 1.4f), new Vector3(6.5f, 4.6f, 2.35f), t);
                    fov = 47f;
                    break;
                case "Reverse":
                    position = root.position + Vector3.Lerp(new Vector3(-7.1f, 2.55f, 3.15f), new Vector3(-3.4f, 3f, 6.1f), t);
                    fov = 46f;
                    break;
                case "Close":
                    position = root.position + Vector3.Lerp(new Vector3(2.84f, 1.22f, 5.2f), new Vector3(2.25f, 1.52f, 4.93f), t);
                    focus = head.position + root.forward * .30f + Vector3.up * .13f;
                    fov = 28f;
                    break;
                case "Head":
                    position = root.position + Vector3.Lerp(new Vector3(-4.7f, 1.6f, 3.85f), new Vector3(-4.2f, 1.53f, 4.2f), t);
                    focus = Vector3.Lerp(focus, head.position, .78f) + root.forward * .3f;
                    fov = 36f;
                    break;
                case "Tail":
                    position = root.position + Vector3.Lerp(new Vector3(6.3f, 4.05f, -2.75f), new Vector3(5.75f, 3.74f, -2.65f), t);
                    focus = Vector3.Lerp(focus, tip.position, .26f);
                    fov = 49f;
                    break;
                default:
                    position = root.position + Vector3.Lerp(new Vector3(-6.45f, 2.04f, -.3f), new Vector3(-6.0f, 2.12f, 1.12f), t);
                    fov = 47f;
                    break;
            }
            camera.fieldOfView = fov;
            camera.transform.SetPositionAndRotation(position, Quaternion.LookRotation(focus - position, Vector3.up));
        }
    }
}
