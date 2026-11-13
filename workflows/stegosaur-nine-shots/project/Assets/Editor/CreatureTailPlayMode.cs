using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Dinosaug
{
    [InitializeOnLoad]
    public static class CreatureTailPlayMode
    {
        private const string Pending = "Dinosaug.CreatureTailPlayMode.Output";
        private const string ExitStatus = "Dinosaug.CreatureTailPlayMode.ExitStatus";
        private const string ScenePath = "Assets/Scenes/StegosaurCreatureForest.unity";

        [Serializable]
        private sealed class Report
        {
            public string status, error, unityVersion, scene;
            public float turnHeadingDegrees, turnTailTipOffsetM, whipTailTipOffsetM, disabledTailTipOffsetM;
            public float maxLengthErrorM, maxBendLimitExcessDegrees;
            public int turnSamples, whipSamples, disabledSamples;
        }

        private static readonly Report Result = new Report { scene = ScenePath };
        private static StegosaurMotion driver;
        private static Animator animator;
        private static int phase;
        private static int lastFrame = -1;
        private static float startYaw;

        static CreatureTailPlayMode()
        {
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += state =>
            {
                int code = SessionState.GetInt(ExitStatus, -1);
                if (state == PlayModeStateChange.EnteredEditMode && code >= 0)
                {
                    SessionState.EraseInt(ExitStatus);
                    EditorApplication.delayCall += () => EditorApplication.Exit(code);
                }
            };
        }

        public static void Run()
        {
            if (!Application.isBatchMode)
                throw new InvalidOperationException("Run this Play Mode probe from Unity batch mode.");

            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "-tail-playmode-output");
            if (index < 0 || index + 1 == args.Length)
                throw new ArgumentException("Set -tail-playmode-output to the report path.");

            string output = Path.GetFullPath(args[index + 1]);
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            SessionState.SetString(Pending, output);
            EditorApplication.EnterPlaymode();
        }

        private static void Tick()
        {
            string output = SessionState.GetString(Pending, "");
            if (output.Length == 0 || !EditorApplication.isPlaying || lastFrame == Time.frameCount)
                return;

            lastFrame = Time.frameCount;
            try
            {
                float elapsed = Time.timeSinceLevelLoad;
                if (elapsed > 20f)
                    throw new TimeoutException("The turn and both tail-whip passes did not complete.");

                if (phase == 0)
                {
                    Time.captureFramerate = 60;
                    driver = UnityEngine.Object.FindFirstObjectByType<StegosaurMotion>();
                    if (!driver)
                        throw new InvalidDataException("The saved scene has no motion driver.");

                    animator = driver.GetComponentInChildren<Animator>();
                    if (!animator || !driver.TailPhysicsEnabled)
                        throw new InvalidDataException("The saved scene did not activate the Animator and physical tail.");
                    startYaw = driver.transform.eulerAngles.y;
                    driver.PlayAction(StegosaurMotion.Action.TurnLeft);
                    phase = 1;
                    return;
                }

                var state = animator.GetCurrentAnimatorStateInfo(0);
                bool idle = state.IsName("Idle") && !animator.IsInTransition(0);
                TailPoseMeasurement sample = driver.CurrentTailPose;
                Result.maxLengthErrorM = Mathf.Max(Result.maxLengthErrorM, sample.MaxLengthErrorM);
                Result.maxBendLimitExcessDegrees = Mathf.Max(Result.maxBendLimitExcessDegrees, sample.MaxBendLimitExcessDegrees);
                if (phase == 1)
                {
                    if (state.IsName("TurnLeft"))
                    {
                        Result.turnSamples++;
                        Result.turnTailTipOffsetM = Mathf.Max(Result.turnTailTipOffsetM, sample.TipDisplacementM);
                    }
                    if (idle && Result.turnSamples > 0)
                    {
                        Result.turnHeadingDegrees = Mathf.DeltaAngle(startYaw, driver.transform.eulerAngles.y);
                        driver.PlayAction(StegosaurMotion.Action.TailWhip);
                        phase = 2;
                    }
                    return;
                }
                if (phase == 2)
                {
                    if (state.IsName("TailWhip"))
                    {
                        Result.whipSamples++;
                        Result.whipTailTipOffsetM = Mathf.Max(Result.whipTailTipOffsetM, sample.TipDisplacementM);
                    }
                    if (idle && Result.whipSamples > 0)
                    {
                        driver.TailPhysicsEnabled = false;
                        driver.PlayAction(StegosaurMotion.Action.TailWhip);
                        phase = 3;
                    }
                    return;
                }
                if (state.IsName("TailWhip"))
                {
                    Result.disabledSamples++;
                    Result.disabledTailTipOffsetM = Mathf.Max(Result.disabledTailTipOffsetM, sample.TipDisplacementM);
                }
                if (!idle || Result.disabledSamples == 0)
                    return;

                if (Result.turnHeadingDegrees > -45 || Result.turnTailTipOffsetM < .08f ||
                    Result.whipTailTipOffsetM < .08f || Result.disabledTailTipOffsetM > .00001f ||
                    Result.maxLengthErrorM > .001f || Result.maxBendLimitExcessDegrees > .05f)
                    throw new InvalidDataException("Play Mode failed turn, tail toggle, or tail projection criteria.");

                Finish(output, null);
            }
            catch (Exception error)
            {
                Finish(output, error);
            }
        }

        private static void Finish(string output, Exception error)
        {
            Time.captureFramerate = 0;
            Result.status = error == null ? "PASS" : "FAIL";
            Result.error = error?.ToString();
            Result.unityVersion = Application.unityVersion;
            File.WriteAllText(output, JsonUtility.ToJson(Result, true) + Environment.NewLine);
            SessionState.EraseString(Pending);
            SessionState.SetInt(ExitStatus, error == null ? 0 : 1);
            EditorApplication.ExitPlaymode();
        }
    }
}
