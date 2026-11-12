using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace Dinosaug
{
    public static class ForestSceneBuilder
    {
        public const string ScenePath = "Assets/Scenes/StegosaurForest.unity";
        private const string ManifestPath = "Assets/Source/asset-manifest.json";
        private const string Generated = "Assets/Generated";
        private const float GroundExtent = 48f;
        private const float CharacterLength = 6.5f;
        private static readonly string[] ShrubIds =
            { "shrub_02_a", "shrub_02_b", "shrub_03_a", "shrub_03_b" };

        [Serializable]
        private sealed class SourceManifest
        {
            public ModelSource[] models;
            public SurfaceSource ground;
            public string sky;
        }

        [Serializable]
        private sealed class ModelSource
        {
            public string id;
            public string fbx;
            public SurfaceSource[] materials;
            public float yawDegrees;
        }

        [Serializable]
        private sealed class SurfaceSource
        {
            public string slot;
            public string albedo;
            public string normal;
            public string packedMetallicGloss;
            public bool cutout;
            public float cutoff;
            public float normalScale = 1f;
        }

        [MenuItem("Dinosaug/Build Forest Scene")]
        public static void Build()
        {
            if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo())
                return;

            var manifestAsset = RequireAsset<TextAsset>(ManifestPath);
            var manifest = JsonUtility.FromJson<SourceManifest>(manifestAsset.text);
            if (manifest?.models == null || manifest.ground == null)
                throw new InvalidDataException($"Incomplete asset manifest at {ManifestPath}.");

            Directory.CreateDirectory("Assets/Scenes");
            Directory.CreateDirectory($"{Generated}/DinosaurParts");
            Directory.CreateDirectory($"{Generated}/Materials");
            Directory.CreateDirectory($"{Generated}/Meshes");
            Directory.CreateDirectory($"{Generated}/Prefabs");
            AssetDatabase.Refresh();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var prefabs = manifest.models.ToDictionary(model => model.id, BuildModelPrefab);
            var environment = new GameObject("Environment").transform;
            BuildGround(manifest.ground, environment);
            PopulateForest(environment, prefabs);

            var dinosaur = (GameObject)PrefabUtility.InstantiatePrefab(prefabs["stegosaur"], scene);
            dinosaur.name = "Stegosaur";
            dinosaur.transform.position = new Vector3(0f, 0f, -2.1f);
            PrefabUtility.RecordPrefabInstancePropertyModifications(dinosaur.transform);

            BuildLighting(manifest.sky);
            var camera = new GameObject("Main Camera").AddComponent<Camera>();
            camera.tag = "MainCamera";
            camera.clearFlags = CameraClearFlags.Skybox;
            camera.renderingPath = RenderingPath.Forward;
            camera.nearClipPlane = .08f;
            camera.farClipPlane = 125f;
            camera.fieldOfView = 42f;
            camera.allowHDR = true;
            camera.allowMSAA = true;
            camera.useOcclusionCulling = false;
            camera.transform.SetPositionAndRotation(new Vector3(5.1f, 2.05f, 3.5f),
                Quaternion.LookRotation(new Vector3(-5.15f, -.73f, -5.7f)));

            if (!EditorSceneManager.SaveScene(scene, ScenePath))
                throw new IOException($"Could not save {ScenePath}.");
            AssetDatabase.SaveAssets();
            Debug.Log($"[Dinosaug] Built {ScenePath}; {prefabs.Count} independent model prefabs. " +
                      $"Stegosaur has {dinosaur.GetComponentsInChildren<SkinnedMeshRenderer>(true).Length} skinned parts.");
        }

        private static GameObject BuildModelPrefab(ModelSource model)
        {
            var importer = AssetImporter.GetAtPath(model.fbx) as ModelImporter;
            if (importer == null || model.materials == null || model.materials.Length == 0)
                throw new InvalidDataException($"Missing FBX importer or material slots for {model.id} ({model.fbx}).");

            bool character = model.id == "stegosaur";
            importer.animationType = character ? ModelImporterAnimationType.Generic : ModelImporterAnimationType.None;
            importer.importAnimation = character;
            if (character)
            {
                importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                importer.animationCompression = ModelImporterAnimationCompression.Off;
                importer.optimizeGameObjects = false;
                importer.optimizeBones = false;
                importer.preserveHierarchy = true;
            }
            importer.bakeAxisConversion = true;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importTangents = ModelImporterTangents.CalculateMikk;
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
            importer.materialName = ModelImporterMaterialName.BasedOnMaterialName;

            var expected = new HashSet<Material>();
            foreach (var slot in model.materials)
            {
                if (string.IsNullOrWhiteSpace(slot.slot))
                    throw new InvalidDataException($"Missing FBX material slot name for {model.id}.");

                var material = BuildSurface($"{model.id}_{slot.slot}", slot);
                importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), slot.slot), material);
                expected.Add(material);
            }

            importer.SaveAndReimport();
            var prefabRoot = new GameObject(character ? "Stegosaur" : model.id);
            var modelObject = (GameObject)PrefabUtility.InstantiatePrefab(RequireAsset<GameObject>(model.fbx));
            try
            {
                modelObject.transform.SetParent(prefabRoot.transform, false);
                var renderers = modelObject.GetComponentsInChildren<Renderer>(true);
                if (renderers.Length == 0)
                    throw new InvalidDataException($"No rendered geometry in {model.fbx}.");

                foreach (var renderer in renderers)
                {
                    foreach (var material in renderer.sharedMaterials)
                    {
                        if (!expected.Contains(material))
                            throw new InvalidDataException($"Unmapped material '{(material ? material.name : "<empty>")}' " +
                                $"on '{renderer.name}' in {model.fbx}. Check the manifest slot names.");
                    }
                    renderer.shadowCastingMode = ShadowCastingMode.On;
                    renderer.receiveShadows = true;
                    PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
                }

                if (character)
                {
                    var parts = modelObject.GetComponentsInChildren<SkinnedMeshRenderer>(true);
                    if (parts.Length < 7 || parts.Select(part => part.sharedMesh).Distinct().Count() != parts.Length
                        || parts.Any(part => part.sharedMesh == null || part.bones.Length == 0
                            || part.bones.Any(bone => !bone || !bone.IsChildOf(modelObject.transform))))
                        throw new InvalidDataException($"{model.fbx} needs separate skinned parts on its imported rig.");
                    foreach (var part in parts)
                    {
                        SaveSkinnedPart(part);
                        part.updateWhenOffscreen = true;
                        PrefabUtility.RecordPrefabInstancePropertyModifications(part);
                    }

                    var head = parts.FirstOrDefault(part =>
                        part.name.IndexOf("HeadNeck", StringComparison.OrdinalIgnoreCase) >= 0);
                    var tail = parts.FirstOrDefault(part => part.name.EndsWith("Tail", StringComparison.OrdinalIgnoreCase));
                    float yaw = model.yawDegrees;
                    if (head && tail)
                    {
                        var heading = GeometryBounds(new Renderer[] { head }, true).center -
                                      GeometryBounds(new Renderer[] { tail }, true).center;
                        heading.y = 0f;
                        if (heading.sqrMagnitude > .001f)
                            yaw = Mathf.Abs(heading.z) >= Mathf.Abs(heading.x)
                                ? (heading.z >= 0f ? 0f : 180f)
                                : (heading.x >= 0f ? -90f : 90f);
                    }
                    modelObject.transform.Rotate(0f, yaw, 0f, Space.World);
                    var bounds = GeometryBounds(renderers, true);
                    if (bounds.size.z < .001f)
                        throw new InvalidDataException($"Could not measure longitudinal size of {model.fbx}.");
                    float scale = CharacterLength / bounds.size.z;
                    modelObject.transform.localScale *= scale;
                    modelObject.transform.localPosition = new Vector3(-bounds.center.x, -bounds.min.y,
                        -bounds.center.z) * scale;
                }
                else
                {
                    var bounds = GeometryBounds(renderers, false);
                    float sink = model.id == "tree_stump_01" ? .10f : .025f;
                    modelObject.transform.localPosition += Vector3.up * (-bounds.min.y - sink);
                }

                PrefabUtility.RecordPrefabInstancePropertyModifications(modelObject.transform);
                var path = $"{Generated}/Prefabs/{prefabRoot.name}.prefab";
                var prefab = PrefabUtility.SaveAsPrefabAsset(prefabRoot, path)
                    ?? throw new IOException($"Could not save {path}.");
                if (character && prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(part =>
                        !AssetDatabase.GetAssetPath(part.sharedMesh).StartsWith($"{Generated}/DinosaurParts/", StringComparison.Ordinal)))
                    throw new IOException($"Independent mesh references were not retained in {path}.");
                return prefab;
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(prefabRoot);
            }
        }

        private static void SaveSkinnedPart(SkinnedMeshRenderer renderer)
        {
            string name = SafeAssetName(renderer.name);
            var path = $"{Generated}/DinosaurParts/{name}.asset";
            var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (!mesh)
            {
                mesh = UnityEngine.Object.Instantiate(renderer.sharedMesh);
                mesh.name = name;
                AssetDatabase.CreateAsset(mesh, path);
            }
            else
            {
                EditorUtility.CopySerialized(renderer.sharedMesh, mesh);
                mesh.name = name;
                EditorUtility.SetDirty(mesh);
            }
            renderer.sharedMesh = mesh;
        }

        private static Bounds GeometryBounds(IEnumerable<Renderer> renderers, bool bakeSkin)
        {
            var result = new Bounds();
            bool started = false;
            foreach (var renderer in renderers)
            {
                var localBounds = renderer.localBounds;
                if (bakeSkin && renderer is SkinnedMeshRenderer skinned)
                {
                    var sampled = new Mesh();
                    skinned.BakeMesh(sampled);
                    if (sampled.vertexCount != 0)
                    {
                        sampled.RecalculateBounds();
                        localBounds = sampled.bounds;
                    }
                    UnityEngine.Object.DestroyImmediate(sampled);
                }
                for (int corner = 0; corner < 8; corner++)
                {
                    var extents = localBounds.extents;
                    var point = localBounds.center + new Vector3(
                        (corner & 1) == 0 ? -extents.x : extents.x,
                        (corner & 2) == 0 ? -extents.y : extents.y,
                        (corner & 4) == 0 ? -extents.z : extents.z);
                    point = renderer.transform.TransformPoint(point);
                    if (started)
                        result.Encapsulate(point);
                    else
                    {
                        result = new Bounds(point, Vector3.zero);
                        started = true;
                    }
                }
            }
            return result;
        }

        private static Material BuildSurface(string name, SurfaceSource source)
        {
            var material = CreateMaterial(name, source.cutout ? "Dinosaug/Vegetation Cutout" : "Standard");
            float cutoff = source.cutoff > 0f ? source.cutoff : .45f;
            material.SetTexture("_MainTex", ImportTexture(source.albedo, false, true, source.cutout, cutoff));
            var normal = ImportTexture(source.normal, true, false, false, cutoff);
            material.SetTexture("_BumpMap", normal);
            material.SetFloat("_BumpScale", source.normalScale > 0f ? source.normalScale : 1f);
            SetKeyword(material, "_NORMALMAP", normal != null);
            var packed = ImportTexture(source.packedMetallicGloss, false, false, false, cutoff);
            material.SetTexture("_MetallicGlossMap", packed);
            material.SetFloat("_Metallic", 0f);
            material.SetFloat("_Glossiness", .23f);
            material.SetFloat("_GlossMapScale", 1f);
            SetKeyword(material, "_METALLICGLOSSMAP", packed != null);
            material.SetColor("_Color", Color.white);
            if (source.cutout)
                material.SetFloat("_Cutoff", cutoff);
            material.enableInstancing = true;
            EditorUtility.SetDirty(material);
            return material;
        }

        private static Texture2D ImportTexture(string path, bool normal, bool srgb, bool cutout, float cutoff)
        {
            if (string.IsNullOrEmpty(path))
                return null;

            var importer = AssetImporter.GetAtPath(path) as TextureImporter
                ?? throw new FileNotFoundException("Texture importer could not find the manifest asset.", path);
            var settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            var type = normal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            bool preserveAlpha = cutout || (!normal && !srgb);
            bool changed = importer.textureType != type || importer.sRGBTexture != srgb
                || importer.alphaIsTransparency != cutout || !importer.mipmapEnabled || importer.anisoLevel != 8
                || importer.wrapMode != TextureWrapMode.Repeat
                || (preserveAlpha && importer.alphaSource != TextureImporterAlphaSource.FromInput)
                || settings.mipMapsPreserveCoverage != cutout
                || (cutout && !Mathf.Approximately(settings.alphaTestReferenceValue, cutoff));
            if (changed)
            {
                importer.textureType = type;
                importer.sRGBTexture = srgb;
                importer.alphaIsTransparency = cutout;
                importer.mipmapEnabled = true;
                importer.wrapMode = TextureWrapMode.Repeat;
                if (preserveAlpha)
                    importer.alphaSource = TextureImporterAlphaSource.FromInput;
                importer.anisoLevel = 8;
                importer.ReadTextureSettings(settings);
                settings.mipMapsPreserveCoverage = cutout;
                if (cutout)
                    settings.alphaTestReferenceValue = cutoff;
                importer.SetTextureSettings(settings);
                importer.SaveAndReimport();
            }
            return RequireAsset<Texture2D>(path);
        }

        private static Material CreateMaterial(string name, string shaderName)
        {
            var shader = Shader.Find(shaderName);
            if (!shader)
                throw new InvalidOperationException($"Shader unavailable: {shaderName}.");
            name = SafeAssetName(name);
            var path = $"{Generated}/Materials/{name}.mat";
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (!material)
            {
                material = new Material(shader) { name = name };
                AssetDatabase.CreateAsset(material, path);
            }
            else if (material.shader != shader)
                material.shader = shader;
            return material;
        }

        private static string SafeAssetName(string name)
        {
            foreach (char character in Path.GetInvalidFileNameChars())
                name = name.Replace(character, '_');
            return name;
        }

        private static void SetKeyword(Material material, string name, bool enabled)
        {
            if (enabled)
                material.EnableKeyword(name);
            else
                material.DisableKeyword(name);
        }

        private static T RequireAsset<T>(string path) where T : UnityEngine.Object
        {
            if (string.IsNullOrEmpty(path))
                throw new InvalidDataException($"Manifest contains an empty required {typeof(T).Name} path.");
            var asset = AssetDatabase.LoadAssetAtPath<T>(path);
            if (!asset)
                throw new FileNotFoundException($"Required Unity {typeof(T).Name} asset is missing.", path);
            return asset;
        }

        private static void BuildGround(SurfaceSource source, Transform parent)
        {
            const int cells = 96;
            const int stride = cells + 1;
            var points = new Vector3[stride * stride];
            var uv = new Vector2[points.Length];
            var triangles = new int[cells * cells * 6];
            int index = 0;
            for (int z = 0; z <= cells; z++)
            {
                for (int x = 0; x <= cells; x++)
                {
                    int vertex = z * stride + x;
                    float worldX = (float)x / cells * GroundExtent * 2f - GroundExtent;
                    float worldZ = (float)z / cells * GroundExtent * 2f - GroundExtent;
                    points[vertex] = new Vector3(worldX, GroundHeight(worldX, worldZ), worldZ);
                    uv[vertex] = new Vector2(worldX / 2.8f, worldZ / 2.8f);
                    if (z == cells || x == cells)
                        continue;

                    triangles[index++] = vertex;
                    triangles[index++] = vertex + stride;
                    triangles[index++] = vertex + 1;
                    triangles[index++] = vertex + 1;
                    triangles[index++] = vertex + stride;
                    triangles[index++] = vertex + stride + 1;
                }
            }

            var path = $"{Generated}/Meshes/ForestFloor.asset";
            var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (!mesh)
            {
                mesh = new Mesh { name = "Forest Floor" };
                AssetDatabase.CreateAsset(mesh, path);
            }
            mesh.Clear();
            mesh.vertices = points;
            mesh.uv = uv;
            mesh.triangles = triangles;
            mesh.RecalculateNormals();
            mesh.RecalculateTangents();
            mesh.RecalculateBounds();
            EditorUtility.SetDirty(mesh);

            var floor = new GameObject("Leaf-litter Ground", typeof(MeshFilter), typeof(MeshRenderer));
            floor.transform.SetParent(parent, false);
            floor.GetComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = floor.GetComponent<MeshRenderer>();
            renderer.sharedMaterial = BuildSurface("LeafLitter", source);
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = true;
        }

        private static float GroundHeight(float x, float z)
        {
            float pathEdge = Mathf.Max(Mathf.Abs(x) - 3.1f, Mathf.Abs(z) - 10.5f);
            float slope = Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(0f, 8f, pathEdge));
            float hill = Mathf.PerlinNoise(4.1f + x * .080f, 2.6f + z * .080f);
            float detail = Mathf.PerlinNoise(1.9f + x * .23f, 6.2f + z * .23f);
            return slope * ((hill - .46f) * 1.7f + (detail - .5f) * .35f);
        }

        private static void PopulateForest(Transform parent, IReadOnlyDictionary<string, GameObject> prefabs)
        {
            var canopy = new GameObject("Canopy Trees").transform;
            var understory = new GameObject("Saplings").transform;
            var shrubs = new GameObject("Low Shrubs").transform;
            var sorrel = new GameObject("Sorrel Patches").transform;
            var stumps = new GameObject("Weathered Stumps").transform;
            foreach (var group in new[] { canopy, understory, shrubs, sorrel, stumps })
                group.SetParent(parent, false);

            var random = new System.Random(117);
            float Range(float minimum, float maximum) =>
                minimum + (float)random.NextDouble() * (maximum - minimum);

            foreach (int side in new[] { -1, 1 })
            {
                foreach (float z in new[] { -14f, -8f, -2f, 5f, 12f, 18f, 25f })
                    Place(prefabs["tree_small_02"], canopy, side * Range(8.2f, 10.9f), z + Range(-1.7f, 1.7f),
                        Range(1.55f, 2.45f), Range(0f, 360f));
                foreach (float z in new[] { -21f, -11f, -3f, 6f, 15f, 27f, 34f })
                    Place(prefabs["tree_small_02"], canopy, side * Range(14.3f, 21.8f), z + Range(-2.6f, 2.6f),
                        Range(1.8f, 3.2f), Range(0f, 360f));

                for (int i = 0; i < 13; i++)
                    Place(prefabs["pine_sapling_small"], understory, side * Range(6.3f, 21f), Range(-25f, 30f),
                        Range(.85f, 2.25f), Range(0f, 360f));

                for (int cluster = 0; cluster < 17; cluster++)
                {
                    float x = side * Range(5.3f, 16f);
                    float z = Range(-22f, 27f);
                    for (int index = 0; index < 3; index++)
                    {
                        string id = ShrubIds[random.Next(ShrubIds.Length)];
                        float scale = id.StartsWith("shrub_02", StringComparison.Ordinal)
                            ? Range(.44f, .88f) : Range(1.35f, 2.75f);
                        Place(prefabs[id], shrubs, x + Range(-.42f, .42f), z + Range(-.85f, .85f),
                            scale, Range(0f, 360f));
                    }
                    if (cluster % 2 == 0)
                    {
                        for (int index = 0; index < 4; index++)
                            Place(prefabs["shrub_sorrel_01"], sorrel, x + Range(-.38f, .38f), z + Range(-.8f, .8f),
                                Range(1.0f, 2.2f), Range(0f, 360f));
                    }
                }

                Place(prefabs["tree_stump_01"], stumps, side * 5.7f, side * 6.7f, 1.13f, Range(0f, 360f));
                Place(prefabs["tree_stump_01"], stumps, side * 9.3f, -side * 10.4f, .82f, Range(0f, 360f));
            }

            for (int i = -2; i <= 2; i++)
            {
                Place(prefabs["tree_small_02"], canopy, i * 5.9f + Range(-1f, 1f), Range(20f, 25f),
                    Range(1.6f, 2.6f), Range(0f, 360f));
                Place(prefabs["tree_small_02"], canopy, i * 5.9f + Range(-1f, 1f), Range(-26f, -20f),
                    Range(1.6f, 2.6f), Range(0f, 360f));
            }
        }

        private static void Place(GameObject prefab, Transform parent, float x, float z, float scale, float yaw)
        {
            var item = (GameObject)PrefabUtility.InstantiatePrefab(prefab, parent);
            item.name = $"{prefab.name} {parent.childCount:00}";
            item.transform.SetPositionAndRotation(new Vector3(x, GroundHeight(x, z), z),
                Quaternion.Euler(0f, yaw, 0f));
            item.transform.localScale = Vector3.one * scale;
            PrefabUtility.RecordPrefabInstancePropertyModifications(item);
            PrefabUtility.RecordPrefabInstancePropertyModifications(item.transform);
        }

        private static void BuildLighting(string skyPath)
        {
            var sky = CreateMaterial("ForestSky", "Skybox/Panoramic");
            sky.SetTexture("_MainTex", RequireAsset<Texture2D>(skyPath));
            sky.SetFloat("_Mapping", 1f);
            sky.SetFloat("_ImageType", 0f);
            sky.SetFloat("_Rotation", 143f);
            sky.SetFloat("_Exposure", .58f);
            EditorUtility.SetDirty(sky);

            var lighting = new GameObject("Lighting").transform;
            var sun = new GameObject("Sunlight").AddComponent<Light>();
            sun.transform.SetParent(lighting, false);
            sun.transform.rotation = Quaternion.Euler(46f, -37f, 0f);
            sun.type = LightType.Directional;
            sun.color = new Color(1f, .89f, .73f);
            sun.intensity = 1.28f;
            sun.shadows = LightShadows.Soft;
            sun.shadowStrength = .83f;
            sun.shadowBias = .035f;
            sun.shadowNormalBias = .18f;

            RenderSettings.skybox = sky;
            RenderSettings.sun = sun;
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(.63f, .72f, .67f);
            RenderSettings.ambientEquatorColor = new Color(.42f, .48f, .41f);
            RenderSettings.ambientGroundColor = new Color(.28f, .23f, .17f);
            RenderSettings.reflectionIntensity = .35f;
            RenderSettings.fog = true;
            RenderSettings.fogMode = FogMode.ExponentialSquared;
            RenderSettings.fogColor = new Color(.48f, .57f, .52f);
            RenderSettings.fogDensity = .032f;

            QualitySettings.shadows = ShadowQuality.All;
            QualitySettings.shadowResolution = ShadowResolution.High;
            QualitySettings.shadowDistance = 44f;
            QualitySettings.shadowCascades = 4;
            QualitySettings.anisotropicFiltering = AnisotropicFiltering.ForceEnable;
        }
    }
}
