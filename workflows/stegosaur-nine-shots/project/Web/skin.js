import * as THREE from 'three';

const set2 = [
    'mat4 boneMatX1 = getBoneMatrix( joints_1.x );',
    'mat4 boneMatY1 = getBoneMatrix( joints_1.y );',
    'mat4 boneMatZ1 = getBoneMatrix( joints_1.z );',
    'mat4 boneMatW1 = getBoneMatrix( joints_1.w );',
].join('\n');

const patched = new WeakSet();
function patchMaterial(material) {
    if (patched.has(material)) return;
    patched.add(material);
    const previousCompile = material.onBeforeCompile;
    const previousKey = material.customProgramCacheKey();
    material.customProgramCacheKey = () => previousKey + ':stegosaur-weight-sets-2';
    material.onBeforeCompile = shader => {
        previousCompile(shader);
        shader.vertexShader = 'attribute vec4 joints_1;\nattribute vec4 weights_1;\n'
            + shader.vertexShader
                .replace('#include <skinbase_vertex>', '#include <skinbase_vertex>\n' + set2)
                .replace('#include <skinning_vertex>', THREE.ShaderChunk.skinning_vertex.replace(
                    'transformed =', [
                        'skinned += boneMatX1 * skinVertex * weights_1.x;',
                        'skinned += boneMatY1 * skinVertex * weights_1.y;',
                        'skinned += boneMatZ1 * skinVertex * weights_1.z;',
                        'skinned += boneMatW1 * skinVertex * weights_1.w;',
                        'transformed =',
                    ].join('\n'),
                ))
                .replace('#include <skinnormal_vertex>', THREE.ShaderChunk.skinnormal_vertex.replace(
                    'skinMatrix = bindMatrixInverse', [
                        'skinMatrix += weights_1.x * boneMatX1;',
                        'skinMatrix += weights_1.y * boneMatY1;',
                        'skinMatrix += weights_1.z * boneMatZ1;',
                        'skinMatrix += weights_1.w * boneMatW1;',
                        'skinMatrix = bindMatrixInverse',
                    ].join('\n'),
                ));
    };
    material.needsUpdate = true;
}

export function installSkinning(model) {
    const extended = new Map();
    const stats = { skinnedParts: 0, triangles: 0, extendedParts: 0, maxInfluences: 0, maxWeightSumError: 0 };
    const depth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
    patchMaterial(depth);
    model.traverse(mesh => {
        if (!mesh.isSkinnedMesh) return;
        stats.skinnedParts++;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        // The rest-pose bounding sphere can clip a deformed tail at a frame edge.
        mesh.frustumCulled = false;
        const { geometry } = mesh;
        const primary = geometry.getAttribute('skinWeight');
        stats.triangles += (geometry.index?.count ?? primary.count) / 3;
        const secondary = geometry.getAttribute('weights_1');
        if (geometry.hasAttribute('weights_2')) throw new Error('This viewer supports eight skin influences.');
        if (secondary) {
            if (!geometry.hasAttribute('joints_1')) throw new Error('The second weight set needs JOINTS_1.');
            stats.extendedParts++;
            const materials = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).map(original => {
                if (!extended.has(original)) {
                    const material = original.clone();
                    material.onBeforeCompile = original.onBeforeCompile;
                    material.customProgramCacheKey = original.customProgramCacheKey;
                    patchMaterial(material);
                    extended.set(original, material);
                }
                return extended.get(original);
            });
            mesh.material = Array.isArray(mesh.material) ? materials : materials[0];
            mesh.customDepthMaterial = depth;
        }
        for (let i = 0; i < primary.count; i++) {
            let sum = 0;
            let influences = 0;
            for (const set of [primary, secondary]) {
                if (!set) continue;
                for (let j = 0; j < 4; j++) {
                    const weight = set.getComponent(i, j);
                    sum += weight;
                    influences += Number(weight > 1e-6);
                }
            }
            stats.maxWeightSumError = Math.max(stats.maxWeightSumError, Math.abs(1 - sum));
            stats.maxInfluences = Math.max(stats.maxInfluences, influences);
        }
    });
    return stats;
}


export function createHindLegAreaProbe(model) {
    const restAreaCutoff = 1e-7;
    const parts = ['HindLeftLeg', 'HindRightLeg'].map(name => {
        const mesh = model.getObjectByName(name);
        if (!mesh?.isSkinnedMesh) throw new Error('Missing skinned hind leg ' + name);
        const { geometry } = mesh;
        const position = geometry.getAttribute('position');
        const ids = new Uint16Array(position.count * 8);
        const weights = new Float32Array(ids.length);
        const sets = [
            [geometry.getAttribute('skinIndex'), geometry.getAttribute('skinWeight')],
            [geometry.getAttribute('joints_1'), geometry.getAttribute('weights_1')],
        ];
        for (const [set, [joints, influences]] of sets.entries()) {
            if (!influences) continue;
            for (let vertex = 0; vertex < position.count; vertex++) {
                for (let j = 0; j < 4; j++) {
                    const offset = vertex * 8 + set * 4 + j;
                    ids[offset] = joints.getComponent(vertex, j);
                    weights[offset] = influences.getComponent(vertex, j);
                }
            }
        }

        const local = new THREE.Vector3();
        const point = new THREE.Vector3();
        const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
        const edge = new THREE.Vector3();
        const world = new Float64Array(position.count * 3);
        const matrix = mesh.skeleton.bones.map(() => new THREE.Matrix4());
        const triangles = geometry.index?.count ?? position.count;
        const vertexIndex = index => geometry.index?.getX(index) ?? index;
        const area = (i, j, k) => {
            a.fromArray(world, i * 3);
            b.fromArray(world, j * 3).sub(a);
            c.fromArray(world, k * 3).sub(a);
            return edge.crossVectors(b, c).length() * .5;
        };

        for (let vertex = 0; vertex < position.count; vertex++) {
            point.fromBufferAttribute(position, vertex).applyMatrix4(mesh.matrixWorld).toArray(world, vertex * 3);
        }
        const neutral = [];
        for (let index = 0; index < triangles; index += 3) {
            const i = vertexIndex(index), j = vertexIndex(index + 1), k = vertexIndex(index + 2);
            const rest = area(i, j, k);
            if (rest >= restAreaCutoff) neutral.push({ i, j, k, rest });
        }

        return [name, () => {
            // Match the WebGL vertex shader's morph-first, two-weight-set linear blend.
            for (const [index, bone] of mesh.skeleton.bones.entries()) {
                matrix[index].multiplyMatrices(bone.matrixWorld, mesh.skeleton.boneInverses[index])
                    .premultiply(mesh.bindMatrixInverse).multiply(mesh.bindMatrix);
            }
            const skinned = new THREE.Vector3();
            for (let vertex = 0; vertex < position.count; vertex++) {
                THREE.Mesh.prototype.getVertexPosition.call(mesh, vertex, local);
                skinned.set(0, 0, 0);
                for (let offset = vertex * 8; offset < vertex * 8 + 8; offset++) {
                    if (weights[offset] === 0) continue;
                    point.copy(local).applyMatrix4(matrix[ids[offset]]);
                    skinned.addScaledVector(point, weights[offset]);
                }
                skinned.applyMatrix4(mesh.matrixWorld).toArray(world, vertex * 3);
            }
            const counts = { trianglesPerFrame: neutral.length, excludedTrianglesPerFrame: triangles / 3 - neutral.length,
                samples: neutral.length, over2x: 0, over4x: 0, belowQuarter: 0, over4xRestAreaCm2Frames: 0 };
            let worst = { ratio: 0 };
            for (const { i, j, k, rest } of neutral) {
                const ratio = area(i, j, k) / rest;
                if (ratio > 2) counts.over2x++;
                if (ratio > 4) {
                    counts.over4x++;
                    counts.over4xRestAreaCm2Frames += rest * 1e4;
                }
                if (ratio < .25) counts.belowQuarter++;
                if (ratio > worst.ratio) {
                    const centerLocalGltf = a.fromBufferAttribute(position, i)
                        .add(b.fromBufferAttribute(position, j)).add(c.fromBufferAttribute(position, k))
                        .multiplyScalar(1 / 3).toArray();
                    worst = { ratio, restAreaSquareMeters: rest, centerLocalGltf };
                }
            }
            return { ...counts, worst };
        }];
    });
    return () => Object.fromEntries(parts.map(([name, measure]) => [name, measure()]));
}
