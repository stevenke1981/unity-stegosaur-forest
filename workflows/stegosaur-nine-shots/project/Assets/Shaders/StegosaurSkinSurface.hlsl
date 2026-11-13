#ifndef DINOSAUG_STEGOSAUR_SKIN_SURFACE
#define DINOSAUG_STEGOSAUR_SKIN_SURFACE

void ApplyStegosaurSkin(FragInputs input, float2 uv, inout float3 normalTS, inout SurfaceData surfaceData)
{
    float3 face = SAMPLE_TEXTURE2D(_SkinRegions, sampler_SkinRegions, uv).rgb * _SkinSurface.y;
    float wet = max(face.r, _SkinSurface.z);
    float3 scales = SAMPLE_TEXTURE2D(_SkinDetail, sampler_SkinDetail, uv * float2(48.0, 24.0)).rgb;
    float cavity = scales.b * _SkinSurface.x * (1.0 - .82 * wet) * lerp(1.0, .60, _PlateFinish);
    float2 detail = (scales.rg * 2.0 - 1.0) * (.58 * _SkinSurface.x * _SkinSurface.w)
        * lerp(1.0, .28, _PlateFinish) * (1.0 - .83 * wet);
    float phase = (uv.y + .29 * uv.x) * 565.0;
    float wave = cos(phase) + .19 * cos(phase * 1.71 + uv.x * 76.0);
    detail += face.b * _JawWrinkleWeight * float2(.034, .115) * wave;
    float3 detailNormal = normalize(float3(detail, 1.0));
    #ifdef SURFACE_GRADIENT
        // HDRP's raster Lit passes combine gradients in the existing MikkTSpace basis.
        normalTS += SurfaceGradientFromTangentSpaceNormalAndFromTBN(
            detailNormal, input.tangentToWorld[0], input.tangentToWorld[1]);
    #else
        normalTS = BlendNormalRNM(normalTS, detailNormal);
    #endif

    float rough = lerp(lerp(.51, .84, cavity), 1.0 - surfaceData.perceptualSmoothness, .24);
    surfaceData.perceptualSmoothness = 1.0 - lerp(rough, .17, wet * .86);
    surfaceData.baseColor *= 1.0 - .055 * cavity;
    surfaceData.ambientOcclusion *= 1.0 - .14 * cavity;
    surfaceData.coatMask *= wet;
    // The original atlas G records thin areas. The Lit BSDF consumes thickness in the opposite sense.
    float thin = saturate(1.8 * max(face.g, _SkinSurface.z * .38));
    surfaceData.thickness = lerp(1.0, .02, thin);
    surfaceData.transmissionMask *= lerp(.05, .74, thin);
    surfaceData.subsurfaceMask *= lerp(.70, 1.0, thin) * (1.0 - .45 * face.r);
}

#endif
