import * as THREE from 'three';

export async function installCreatureMaterials(model) {
    const loader = new THREE.TextureLoader();
    const textures = await Promise.all(['SkinDetail.png', 'SkinRegions.png'].map(name =>
        loader.loadAsync(new URL('./assets/' + name, import.meta.url).href)));
    const [detail, regions] = textures;
    for (const texture of textures) {
        texture.colorSpace = THREE.NoColorSpace;
        texture.flipY = false;
        texture.anisotropy = 8;
    }
    detail.wrapS = detail.wrapT = THREE.RepeatWrapping;
    const jaw = { value: 0 };
    const converted = new Map();
    const materialModes = new Map([
        ['BakedMaterial', [1, 0, 1, 1, .82]], ['LipTransition', [1, 0, .46, 1, .82]],
        ['MouthLining', [0, .06, 0, .24, .03]], ['Tongue', [0, .36, 0, .52, .30]],
    ]);

    model.traverse(mesh => {
        if (!mesh.isSkinnedMesh) return;
        const original = mesh.material;
        const name = original.name;
        const mode = materialModes.get(name);
        if (!mode) return;
        const plate = mesh.name.startsWith('BackPlate_') || mesh.name.startsWith('TailSpike_');
        const key = plate ? 'KeratinPlate' : name;
        if (!converted.has(key)) {
            const [useAtlas, mucosa, strength, specular, coat] = mode;
            const skin = new THREE.MeshPhysicalMaterial({
                name: key,
                color: original.color,
                side: original.side,
                map: original.map,
                normalMap: original.normalMap,
                normalScale: original.normalScale,
                roughness: 1,
                roughnessMap: original.roughnessMap,
                metalness: 0,
                vertexColors: original.vertexColors,
                ior: 1.40,
                specularIntensity: specular,
                envMapIntensity: useAtlas ? 1.60 : 1.0,
                clearcoat: coat,
                clearcoatMap: useAtlas ? regions : null,
                clearcoatRoughness: useAtlas ? .16 : .28,
                anisotropy: plate ? .34 : 0,
                anisotropyRotation: Math.PI / 2,
                sheen: plate ? .075 : useAtlas ? .17 : 0,
                sheenRoughness: .65,
                sheenColor: new THREE.Color(0x968773),
            });
            skin.customProgramCacheKey = () => 'stegosaur-physical-skin-v1';
            skin.onBeforeCompile = shader => {
                shader.uniforms.skinDetail = { value: detail };
                shader.uniforms.skinRegions = { value: regions };
                shader.uniforms.skinMode = { value: new THREE.Vector4(useAtlas, mucosa, strength * (plate ? .28 : 1), plate ? .60 : 1) };
                shader.uniforms.jawWrinkleWeight = jaw;
                shader.fragmentShader = shader.fragmentShader
                    .replace('#include <common>', `#include <common>
uniform sampler2D skinDetail, skinRegions;
uniform vec4 skinMode;
uniform float jawWrinkleWeight;
vec3 orientSkinNormal(vec3 base, vec3 extra) {
    vec3 t = base + vec3(0.0,0.0,1.0);
    vec3 u = extra * vec3(-1.0,-1.0,1.0);
    return normalize(t * dot(t,u) / max(t.z,.001) - u);
}`)
                    .replace('#include <color_fragment>', `#include <color_fragment>
vec3 skinMask = texture2D(skinRegions,vMapUv).rgb * skinMode.x;
float skinWet = max(skinMask.r, skinMode.y);
vec3 scaleLayer = texture2D(skinDetail,vMapUv * vec2(48.0,24.0)).rgb;
float scaleCavity = scaleLayer.b * skinMode.x * (1.0-.82*skinWet) * skinMode.w;
diffuseColor.rgb *= 1.0-.055*scaleCavity;`)
                    .replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
roughnessFactor = mix(mix(mix(.51,.84,scaleCavity), roughnessFactor, .24), .17, .86*skinWet);`)
                    .replace('#include <normal_fragment_maps>', THREE.ShaderChunk.normal_fragment_maps.replace(
                        'normal = normalize( tbn * mapN );', `
vec2 extra = (scaleLayer.rg*2.0-1.0) * (.58*skinMode.x*skinMode.z) * (1.0-.83*skinWet);
float phase = (vMapUv.y + .29*vMapUv.x)*565.0;
float wave = cos(phase) + .19*cos(phase*1.71 + vMapUv.x*76.0);
extra += skinMask.b * jawWrinkleWeight * vec2(.034,.115) * wave;
normal = normalize(tbn * orientSkinNormal(normalize(mapN),normalize(vec3(extra,1.0))));
`))
                    .replace('#include <clearcoat_normal_fragment_begin>', `#include <clearcoat_normal_fragment_begin>
#ifdef USE_CLEARCOAT
    clearcoatNormal = normalize(mix(nonPerturbedNormal, normal, .23));
#endif`)
                    .replace('#include <lights_physical_pars_fragment>', THREE.ShaderChunk.lights_physical_pars_fragment
                        .replace('inout ReflectedLight reflectedLight ) {\n\tfloat dotNL', `inout ReflectedLight reflectedLight ) {
    float thin = max(texture2D(skinRegions,vMapUv).g * skinMode.x, skinMode.y * .20);
    float facing = dot(geometryNormal,directLight.direction);
    float wrap = saturate((facing+.20)/1.20) - saturate(facing);
    float back = pow(saturate(dot(-directLight.direction,geometryViewDir)),3.0) * saturate(.18-facing);
    reflectedLight.directDiffuse += directLight.color * material.diffuseContribution * vec3(1.0,.48,.22)
        * thin * (.30*wrap + .21*back);
    float dotNL`))
                    .replace('#include <aomap_fragment>', `#include <aomap_fragment>
reflectedLight.indirectDiffuse *= 1.0-.14*scaleCavity;
reflectedLight.indirectSpecular *= 1.0-.10*scaleCavity;`);
            };
            converted.set(key, skin);
        }
        mesh.material = converted.get(key);
    });

    return { count: converted.size, setJaw(degrees) {
        jaw.value = THREE.MathUtils.smoothstep(degrees, 4, 34);
        return jaw.value;
    } };
}
