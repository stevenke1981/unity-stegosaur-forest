Shader "Dinosaug/Vegetation Cutout"
{
    Properties
    {
        _Color ("Tint", Color) = (1, 1, 1, 1)
        _MainTex ("Base color and alpha", 2D) = "white" {}
        _Cutoff ("Alpha cutoff", Range(0, 1)) = 0.45
        [Normal] _BumpMap ("Normal map", 2D) = "bump" {}
        _BumpScale ("Normal strength", Range(0, 2)) = 1
        _MetallicGlossMap ("Metallic (R), smoothness (A)", 2D) = "black" {}
        _Metallic ("Metallic", Range(0, 1)) = 0
        _Glossiness ("Smoothness", Range(0, 1)) = 0.23
        _GlossMapScale ("Smoothness scale", Range(0, 1)) = 1
    }

    SubShader
    {
        Tags { "Queue"="AlphaTest" "RenderType"="TransparentCutout" }
        LOD 300
        Cull Off

        CGPROGRAM
        #pragma target 3.0
        #pragma surface surf Standard fullforwardshadows addshadow alphatest:_Cutoff
        #pragma shader_feature_local _NORMALMAP
        #pragma shader_feature_local _METALLICGLOSSMAP
        #include "UnityStandardUtils.cginc"

        sampler2D _MainTex, _BumpMap, _MetallicGlossMap;
        fixed4 _Color;
        half _BumpScale, _Metallic, _Glossiness, _GlossMapScale;

        struct Input
        {
            float2 uv_MainTex;
            float facing : VFACE;
        };

        void surf(Input input, inout SurfaceOutputStandard output)
        {
            fixed4 surface = tex2D(_MainTex, input.uv_MainTex) * _Color;
            output.Albedo = surface.rgb;
            output.Alpha = surface.a;

            #if defined(_NORMALMAP)
                output.Normal = UnpackScaleNormal(tex2D(_BumpMap, input.uv_MainTex), _BumpScale);
            #else
                output.Normal = half3(0, 0, 1);
            #endif
            output.Normal.z *= input.facing > 0 ? 1 : -1;

            #if defined(_METALLICGLOSSMAP)
                half4 packed = tex2D(_MetallicGlossMap, input.uv_MainTex);
                output.Metallic = packed.r;
                output.Smoothness = packed.a * _GlossMapScale;
            #else
                output.Metallic = _Metallic;
                output.Smoothness = _Glossiness;
            #endif
        }
        ENDCG
    }

    FallBack "Legacy Shaders/Transparent/Cutout/VertexLit"
}
