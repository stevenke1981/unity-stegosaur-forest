using System;
using System.IO;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.HighDefinition;

namespace Dinosaug
{
    internal static class StegosaurHdrp
    {
        internal const string SkinShader = "Dinosaug/Stegosaur HDRP Skin";
        private const string Folder = "Assets/Generated/HDRP";
        private const string SkinAsset = Folder + "/ReptileSkin.asset";

        internal static void Ensure()
        {
            Directory.CreateDirectory(Folder);
            AssetDatabase.Refresh();
            var asset = AssetDatabase.LoadAssetAtPath<HDRenderPipelineAsset>(Folder + "/ForestHDRP.asset");
            if (!asset)
            {
                asset = ScriptableObject.CreateInstance<HDRenderPipelineAsset>();
                AssetDatabase.CreateAsset(asset, Folder + "/ForestHDRP.asset");
            }
            var settings = asset.currentPlatformRenderPipelineSettings;
            settings.supportSubsurfaceScattering = true;
            settings.supportVolumetrics = false;
            settings.supportSSR = false;
            settings.supportSSAO = true;
            settings.supportMotionVectors = false;
            settings.supportDistortion = false;
            settings.supportedLitShaderMode = RenderPipelineSettings.SupportedLitShaderMode.DeferredOnly;
            asset.currentPlatformRenderPipelineSettings = settings;
            EditorUtility.SetDirty(asset);
            GraphicsSettings.defaultRenderPipeline = asset;
            QualitySettings.renderPipeline = asset;
            PlayerSettings.colorSpace = ColorSpace.Linear;
            // HDRP 17.3's own editor initialization, including its package resources and defaults.
            var globalSettings = typeof(HDRenderPipeline).Assembly
                .GetType("UnityEngine.Rendering.HighDefinition.HDRenderPipelineGlobalSettings", throwOnError: true);
            var ensure = globalSettings.GetMethod("Ensure", BindingFlags.NonPublic | BindingFlags.Static);
            if (ensure?.Invoke(null, new object[] { true }) is not RenderPipelineGlobalSettings)
                throw new InvalidOperationException("HDRP global settings could not be initialized.");
            SkinProfile();
            AssetDatabase.SaveAssets();
        }

        internal static DiffusionProfileSettings SkinProfile()
        {
            var profile = AssetDatabase.LoadAssetAtPath<DiffusionProfileSettings>(SkinAsset);
            if (!profile)
            {
                profile = ScriptableObject.CreateInstance<DiffusionProfileSettings>();
                AssetDatabase.CreateAsset(profile, SkinAsset);
                AssetDatabase.ImportAsset(SkinAsset, ImportAssetOptions.ForceSynchronousImport);
                profile = AssetDatabase.LoadAssetAtPath<DiffusionProfileSettings>(SkinAsset);
                profile.scatteringDistance = new Color(1.36f, .56f, .23f, 1f);
                profile.indexOfRefraction = 1.4f;
                profile.transmissionTint = new Color(.91f, .55f, .29f, 1f);
                profile.worldScale = 1f;
                profile.borderAttenuationColor = new Color(.76f, .68f, .59f, 1f);
                EditorUtility.SetDirty(profile);
                AssetDatabase.SaveAssetIfDirty(profile);
            }
            return profile;
        }

        internal static void ValidateSkin(Material material)
        {
            // The project shader retains Lit's properties, passes and stencil layout.
            // The public validator uses an exact-name lookup and does not dispatch custom shaders.
            var api = typeof(HDMaterial).Assembly.GetType("UnityEngine.Rendering.HighDefinition.LitAPI", throwOnError: true);
            var validate = api.GetMethod("ValidateMaterial", BindingFlags.NonPublic | BindingFlags.Static)
                ?? throw new MissingMethodException("HDRP LitAPI.ValidateMaterial");
            validate.Invoke(null, new object[] { material });
        }

        internal static void AddLightingVolume(Transform lighting, string skyPath)
        {
            var importer = (TextureImporter)AssetImporter.GetAtPath(skyPath);
            if (importer.textureShape != TextureImporterShape.TextureCube)
            {
                importer.textureShape = TextureImporterShape.TextureCube;
                importer.generateCubemap = TextureImporterGenerateCubemap.AutoCubemap;
                importer.sRGBTexture = false;
                importer.mipmapEnabled = true;
                importer.SaveAndReimport();
            }
            var panorama = AssetDatabase.LoadAssetAtPath<Cubemap>(skyPath)
                ?? throw new FileNotFoundException("Forest HDRI did not import as a cubemap.", skyPath);
            string path = Folder + "/ForestVolume.asset";
            var profile = AssetDatabase.LoadAssetAtPath<VolumeProfile>(path);
            if (!profile)
            {
                profile = ScriptableObject.CreateInstance<VolumeProfile>();
                AssetDatabase.CreateAsset(profile, path);
            }
            T Component<T>() where T : VolumeComponent
            {
                if (profile.TryGet<T>(out var entry))
                    return entry;
                entry = profile.Add<T>(false);
                AssetDatabase.AddObjectToAsset(entry, profile);
                return entry;
            }

            var environment = Component<VisualEnvironment>();
            environment.skyType.Override((int)SkyType.HDRI);
            environment.skyAmbientMode.Override(SkyAmbientMode.Dynamic);
            var sky = Component<HDRISky>();
            sky.hdriSky.Override(panorama);
            sky.rotation.Override(143f);
            sky.skyIntensityMode.Override(SkyIntensityMode.Multiplier);
            sky.multiplier.Override(235f);
            var exposure = Component<Exposure>();
            exposure.mode.Override(ExposureMode.Fixed);
            exposure.fixedExposure.Override(8.25f);
            Component<Tonemapping>().mode.Override(TonemappingMode.ACES);
            var shadows = Component<HDShadowSettings>();
            shadows.maxShadowDistance.Override(46f);
            shadows.cascadeShadowSplitCount.Override(4);
            var diffusion = Component<DiffusionProfileList>();
            diffusion.diffusionProfiles.Override(new[] { SkinProfile() });
            foreach (var entry in profile.components)
                EditorUtility.SetDirty(entry);
            EditorUtility.SetDirty(profile);
            var volume = new GameObject("HDRP forest exposure and skin diffusion").AddComponent<Volume>();
            volume.transform.SetParent(lighting, false);
            volume.isGlobal = true;
            volume.priority = 10f;
            volume.sharedProfile = profile;
            AssetDatabase.SaveAssets();
        }

        internal static HDAdditionalCameraData ConfigureCamera(Camera camera)
        {
            var data = camera.GetComponent<HDAdditionalCameraData>() ?? camera.gameObject.AddComponent<HDAdditionalCameraData>();
            data.clearColorMode = HDAdditionalCameraData.ClearColorMode.Sky;
            data.antialiasing = HDAdditionalCameraData.AntialiasingMode.SubpixelMorphologicalAntiAliasing;
            data.customRenderingSettings = true;
            SetSubsurface(data, true);
            camera.allowHDR = true;
            camera.allowMSAA = false;
            return data;
        }

        internal static void SetSubsurface(HDAdditionalCameraData camera, bool enabled)
        {
            camera.renderingPathCustomFrameSettingsOverrideMask.mask[(uint)FrameSettingsField.SubsurfaceScattering] = true;
            camera.renderingPathCustomFrameSettings.SetEnabled(FrameSettingsField.SubsurfaceScattering, enabled);
        }
    }
}
