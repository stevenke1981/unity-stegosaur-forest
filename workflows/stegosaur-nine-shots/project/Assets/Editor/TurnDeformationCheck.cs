using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace Dinosaug
{
    [Serializable]
    internal sealed class TurnDeformationReport
    {
        public int measuredFrames, trianglesPerFrame, trianglesExcludedPerFrame;
        public long samples, over2x, over4x, belowQuarter;
        public float worstAreaRatio, smallestAreaRatio = 1f;
        public float over4xRestAreaCm2Frames, restAreaCutoffSquareM;
        public string worstPart;
        public int worstFrame;
        public PartDeformationReport[] parts;
    }

    [Serializable]
    internal sealed class PartDeformationReport
    {
        public string part;
        public int trianglesPerFrame, trianglesExcludedPerFrame;
        public long samples, over2x, over4x, belowQuarter;
        public float worstAreaRatio, smallestAreaRatio = 1f;
        public float over4xRestAreaCm2Frames;
        public DeformationLocation worst;
        public DeformationLocation[] outliers;
    }

    [Serializable]
    internal sealed class DeformationLocation
    {
        public int frame, triangle;
        public int[] vertexIndices;
        public float ratio, restAreaSquareM;
        public Vector3 neutralMeshCentroid, neutralWorldCentroid, posedWorldCentroid;
    }

    internal sealed class TurnDeformationCheck
    {
        internal const float RestAreaCutoffSquareM = 1e-7f; // 0.1 mm².
        private readonly (int a, int b, int c, int index, float squaredDoubleArea)[] rest;
        private readonly List<Vector3> vertices = new List<Vector3>();
        private readonly Transform renderTransform;
        private readonly Matrix4x4 neutralToWorld;
        private readonly Vector3[] neutralVertices;
        private readonly Dictionary<int, DeformationLocation> outliers = new Dictionary<int, DeformationLocation>();
        public int TriangleCount => rest.Length;
        public int Excluded { get; }

        public TurnDeformationCheck(Transform renderTransform, Mesh neutral, Mesh original)
        {
            this.renderTransform = renderTransform;
            neutralToWorld = renderTransform.localToWorldMatrix;
            var triangles = new List<(int a, int b, int c, int index, float squaredDoubleArea)>();
            neutral.GetVertices(vertices);
            neutralVertices = vertices.ToArray();
            ScaleVertices();
            int index = 0;
            for (int submesh = 0; submesh < original.subMeshCount; submesh++)
            {
                int[] indices = original.GetTriangles(submesh);
                for (int i = 0; i < indices.Length; i += 3, index++)
                {
                    int a = indices[i], b = indices[i + 1], c = indices[i + 2];
                    float squaredDoubleArea = Area(vertices, a, b, c);
                    if (squaredDoubleArea < 4f * RestAreaCutoffSquareM * RestAreaCutoffSquareM)
                    {
                        Excluded++;
                        continue;
                    }
                    triangles.Add((a, b, c, index, squaredDoubleArea));
                }
            }
            rest = triangles.ToArray();
        }

        public PartDeformationReport BeginShot()
        {
            outliers.Clear();
            return new PartDeformationReport
            {
                part = renderTransform.name,
                trianglesPerFrame = TriangleCount,
                trianglesExcludedPerFrame = Excluded
            };
        }

        public void FinishShot(PartDeformationReport details) =>
            details.outliers = outliers.Values.OrderByDescending(sample => sample.ratio).ThenBy(sample => sample.triangle).Take(12).ToArray();

        public void Measure(Mesh deformed, int frame, TurnDeformationReport totals, PartDeformationReport details)
        {
            deformed.GetVertices(vertices);
            ScaleVertices();
            var matrix = renderTransform.localToWorldMatrix;
            totals.samples += rest.Length;
            details.samples += rest.Length;
            foreach (var (a, b, c, index, squaredDoubleArea) in rest)
            {
                float squaredRatio = Area(vertices, a, b, c) / squaredDoubleArea;
                if (squaredRatio > 4f)
                {
                    totals.over2x++;
                    details.over2x++;
                }
                if (squaredRatio > 16f)
                {
                    totals.over4x++;
                    details.over4x++;
                    float restAreaCm2 = 5000 * Mathf.Sqrt(squaredDoubleArea);
                    totals.over4xRestAreaCm2Frames += restAreaCm2;
                    details.over4xRestAreaCm2Frames += restAreaCm2;
                }
                if (squaredRatio < .0625f)
                {
                    totals.belowQuarter++;
                    details.belowQuarter++;
                }

                bool partWorst = squaredRatio > details.worstAreaRatio * details.worstAreaRatio;
                bool severePeak = squaredRatio > 16f &&
                    (!outliers.TryGetValue(index, out DeformationLocation last) || squaredRatio > last.ratio * last.ratio);
                if (partWorst || severePeak)
                {
                    float ratio = Mathf.Sqrt(squaredRatio);
                    Vector3 center = (neutralVertices[a] + neutralVertices[b] + neutralVertices[c]) / 3f;
                    var location = new DeformationLocation
                    {
                        frame = frame, triangle = index, vertexIndices = new[] { a, b, c },
                        ratio = ratio, restAreaSquareM = .5f * Mathf.Sqrt(squaredDoubleArea),
                        neutralMeshCentroid = center,
                        neutralWorldCentroid = neutralToWorld.MultiplyPoint3x4(center),
                        posedWorldCentroid = (vertices[a] + vertices[b] + vertices[c]) / 3f + (Vector3)matrix.GetColumn(3)
                    };
                    if (partWorst)
                    {
                        details.worstAreaRatio = ratio;
                        details.worst = location;
                    }
                    if (severePeak)
                        outliers[index] = location;
                }
                if (squaredRatio > totals.worstAreaRatio * totals.worstAreaRatio)
                {
                    totals.worstAreaRatio = Mathf.Sqrt(squaredRatio);
                    totals.worstPart = renderTransform.name;
                    totals.worstFrame = frame;
                }
                if (squaredRatio < details.smallestAreaRatio * details.smallestAreaRatio)
                    details.smallestAreaRatio = Mathf.Sqrt(squaredRatio);
                if (squaredRatio < totals.smallestAreaRatio * totals.smallestAreaRatio)
                    totals.smallestAreaRatio = Mathf.Sqrt(squaredRatio);
            }
        }

        private void ScaleVertices()
        {
            Matrix4x4 localToWorld = renderTransform.localToWorldMatrix;
            for (int i = 0; i < vertices.Count; i++)
                vertices[i] = localToWorld.MultiplyVector(vertices[i]);
        }

        private static float Area(List<Vector3> positions, int a, int b, int c) =>
            Vector3.Cross(positions[b] - positions[a], positions[c] - positions[a]).sqrMagnitude;
    }
}
