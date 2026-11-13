"""Generate the project skin variant from the installed Unity 6000.3 HDRP Lit files."""
import argparse
import json
from pathlib import Path


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Unsupported HDRP source layout at {old}")
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    opt = parser.parse_args()
    package = json.loads((opt.package / "package.json").read_text(encoding="utf-8"))
    if package["version"] != "17.3.0":
        raise ValueError(f"Expected HDRP 17.3.0, received {package['version']}")
    source = opt.package / "Runtime" / "Material" / "Lit"
    out = Path(__file__).resolve().parents[1] / "Assets" / "Shaders"
    header = ("// Generated from the installed Unity HDRP 17.3.0 Lit source by Tools/build_hdrp_skin_shader.py.\n"
              "// Portions copyright 2020 Unity Technologies ApS, under the Unity Companion License.\n"
              "// See Assets/Shaders/Unity-HDRP-LICENSE.md.\n")
    shader = (source / "Lit.shader").read_text(encoding="utf-8-sig")
    shader = replace_once(shader, 'Shader "HDRP/Lit"', 'Shader "Dinosaug/Stegosaur HDRP Skin"')
    shader = replace_once(shader, '        // Reminder. Color', '''        [NoScaleOffset] _SkinDetail ("Micro scale normal XY and cavity", 2D) = "gray" {}
        [NoScaleOffset] _SkinRegions ("Wet R, thin G, wrinkle B", 2D) = "black" {}
        _SkinSurface ("Detail, facial atlas, mucosa, detail strength", Vector) = (1,1,0,1)
        _PlateFinish ("Keratin plates", Float) = 0
        [PerRendererData] _JawWrinkleWeight ("Jaw wrinkle weight", Range(0,1)) = 0

        // Reminder. Color''')
    for name in ("LitProperties", "LitData"):
        shader = shader.replace(f"Packages/com.unity.render-pipelines.high-definition/Runtime/Material/Lit/{name}.hlsl",
                                f"Assets/Shaders/StegosaurHDRP{name}.hlsl")
    properties = (source / "LitProperties.hlsl").read_text(encoding="utf-8-sig")
    properties = replace_once(properties, "CBUFFER_START(UnityPerMaterial)", """TEXTURE2D(_SkinDetail);
SAMPLER(sampler_SkinDetail);
TEXTURE2D(_SkinRegions);
SAMPLER(sampler_SkinRegions);

CBUFFER_START(UnityPerMaterial)
float4 _SkinSurface;
float _PlateFinish;
float _JawWrinkleWeight;""")
    data = (source / "LitData.hlsl").read_text(encoding="utf-8-sig")
    include = '#include "Packages/com.unity.render-pipelines.high-definition/Runtime/Material/Lit/LitDataIndividualLayer.hlsl"'
    data = replace_once(data, include, include + '\n#include "Assets/Shaders/StegosaurSkinSurface.hlsl"')
    sample = "    float alpha = GetSurfaceData(input, layerTexCoord, surfaceData, normalTS, bentNormalTS);"
    data = replace_once(data, sample, sample + "\n    ApplyStegosaurSkin(input, layerTexCoord.base.uv, normalTS, surfaceData);")
    for name, contents in (("StegosaurHDRPSkin.shader", shader), ("StegosaurHDRPLitProperties.hlsl", properties),
                           ("StegosaurHDRPLitData.hlsl", data)):
        (out / name).write_text(header + contents, encoding="utf-8")
    (out / "Unity-HDRP-LICENSE.md").write_bytes((opt.package / "LICENSE.md").read_bytes())


if __name__ == "__main__":
    main()
