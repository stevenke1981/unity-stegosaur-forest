import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';

const canopy = 'tree_small_02_textured.glb';
const sapling = 'pine_sapling_small_textured.glb';
const stump = 'tree_stump_01_textured.glb';
const sorrel = 'shrub_sorrel_01_textured.glb';
const shrubs = ['shrub_02_a_textured.glb', 'shrub_02_b_textured.glb',
    'shrub_03_a_textured.glb', 'shrub_03_b_textured.glb'];

function terrainHeight(x, z) {
    const edge = Math.max(Math.abs(x) - 3.1, Math.abs(z) - 10.5);
    const blend = THREE.MathUtils.smoothstep(edge, 0, 8);
    const hill = 1.04 * Math.sin(x * .23 + z * .19) * Math.cos(z * .21 - x * .15);
    const distance = Math.hypot(x, z);
    const forestRidge = 3.4 * THREE.MathUtils.smoothstep(distance, 16, 38)
        * (.72 + .28 * Math.sin(Math.atan2(z, x) * 4.5 + .6));
    return blend * (hill + .17 * Math.cos(z * .55 + x * .41) + forestRidge);
}

export async function createForest(scene, renderer) {
    const image = new THREE.TextureLoader();
    const gltf = new GLTFLoader();
    const asset = name => new URL('./assets/' + name, import.meta.url).href;
    const placements = new Map([canopy, sapling, stump, ...shrubs, sorrel].map(name => [name, []]));
    const distant = new Map([canopy, sapling].map(name => [name, []]));
    let seed = 117;
    const range = (a, b) => {
        seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
        return a + (b - a) * seed / 4294967296;
    };
    const add = (name, x, z, scale) => placements.get(name).push([x, z, scale, range(0, Math.PI * 2)]);
    const addDistant = (name, x, z, scale) => distant.get(name).push([x, z, scale, range(0, Math.PI * 2)]);

    for (const side of [-1, 1]) {
        for (const z of [-16, -9, -2, 5, 12, 19, 26])
            add(canopy, side * range(8.7, 11.5), z + range(-1.2, 1.2), range(1.55, 2.45));
        for (const z of [-23, -15, -7, 1, 9, 17, 25, 33])
            add(canopy, side * range(14.7, 23.8), z + range(-2.5, 2.5), range(1.8, 3.2));
        for (let i = 0; i < 12; i++) {
            add(sapling, side * range(7.3, 20), range(-26, 30), range(.85, 2));
        }
        for (let cluster = 0; cluster < 17; cluster++) {
            const x = side * range(5.2, 15.5);
            const z = range(-21, 27);
            for (let i = 0; i < 3; i++) {
                const id = Math.floor(range(0, shrubs.length));
                add(shrubs[id], x + range(-.62, .62), z + range(-.95, .95),
                    id < 2 ? range(.46, .84) : range(1.28, 2.2));
            }
            if (cluster % 2 === 0) {
                for (let i = 0; i < 4; i++) {
                    add(sorrel, x + range(-.46, .46), z + range(-.8, .8), range(.9, 1.75));
                }
            }
        }
        add(stump, side * 6.3, side * 7, 1.03);
        add(stump, side * 9.5, -side * 10.8, .84);
        for (const z of [-12, -7.6, -3.8, .8, 5.5, 10.1, 14.4]) {
            const x = side * range(5.8, 7.6);
            add(shrubs[0], x, z, range(.55, .83));
            add(shrubs[1], x + side * .55, z + range(-.8, .8), range(.55, .94));
            add(shrubs[2], x - side * .62, z + range(-.7, .7), range(1.42, 2.21));
        }
        for (const z of [-11.4, -8.2, -4.5, -1.8, 1.6, 4.7, 7.8, 10.8]) {
            const x = side * range(3.75, 5.25);
            add(shrubs[1], x, z, range(.46, .67));
            add(shrubs[2], x - side * .33, z + range(-.55, .55), range(1.20, 1.59));
            add(sorrel, x - side * .48, z + range(-.75, .75), range(2, 2.85));
        }
    }
    for (let i = -2; i <= 2; i++) {
        add(canopy, i * 5.8 + range(-.9, .9), range(23, 28), range(1.6, 2.6));
        add(canopy, i * 5.8 + range(-.9, .9), range(-28, -21), range(1.6, 2.6));
    }
    for (let i = 0; i < 38; i++) {
        const theta = i * 2 * Math.PI / 38 + range(-.07, .07);
        const radius = range(24, 36);
        addDistant(canopy, radius * Math.cos(theta), radius * Math.sin(theta), range(2.25, 3.8));
        const pineRadius = range(16, 29);
        addDistant(sapling, pineRadius * Math.cos(theta + .08), pineRadius * Math.sin(theta + .08), range(1.6, 3.35));
    }

    const texture = async (name, color = false) => {
        const result = await image.loadAsync(asset(name));
        result.wrapS = result.wrapT = THREE.RepeatWrapping;
        result.repeat.set(38, 38);
        result.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
        if (color) result.colorSpace = THREE.SRGBColorSpace;
        return result;
    };

    const position = new THREE.Object3D();
    const vegetation = [...placements.entries()].map(async ([name, instances]) => {
        const { scene: source } = await gltf.loadAsync(asset(name));
        source.updateMatrixWorld(true);
        const far = distant.get(name) ?? [];
        const batches = [];
        for (const [group, castsShadow] of [[instances, true], [far, false]]) {
            const tiles = new Map();
            for (const instance of group) {
                const tile = Math.floor(instance[0] / 16) + ':' + Math.floor(instance[1] / 16);
                if (!tiles.has(tile)) tiles.set(tile, []);
                tiles.get(tile).push(instance);
            }
            for (const placements of tiles.values()) batches.push({ placements, castsShadow });
        }
        source.traverse(obj => {
            if (!obj.isMesh) return;
            const geometry = obj.geometry.clone().applyMatrix4(obj.matrixWorld);
            const material = obj.material;
            if (material.transparent) {
                // The source alpha channel describes leaf coverage. Cutouts write
                // depth so overlapping instances and their shadows use that mask.
                material.transparent = false;
                material.depthWrite = true;
                material.alphaTest = .36;
            }
            if (material.alphaTest > 0) material.alphaToCoverage = true;
            for (const { placements, castsShadow } of batches) {
                const mesh = new THREE.InstancedMesh(geometry, material, placements.length);
                mesh.name = name;
                mesh.receiveShadow = castsShadow;
                mesh.castShadow = castsShadow && (name === canopy || name === sapling || name === stump);
                for (const [i, [x, z, scale, yaw]] of placements.entries()) {
                    position.position.set(x, terrainHeight(x, z), z);
                    position.rotation.set(0, yaw, 0);
                    position.scale.setScalar(scale);
                    position.updateMatrix();
                    mesh.setMatrixAt(i, position.matrix);
                }
                mesh.instanceMatrix.needsUpdate = true;
                mesh.computeBoundingSphere();
                scene.add(mesh);
            }
        });
        return instances.length + far.length;
    });

    const [base, normal, roughness, environment, ...counts] = await Promise.all([
        texture('ground-diff.jpg', true), texture('ground-nor_gl.jpg'), texture('ground-rough.jpg'),
        new HDRLoader().loadAsync(asset('forest.hdr')), ...vegetation,
    ]);
    environment.mapping = THREE.EquirectangularReflectionMapping;
    scene.background = new THREE.Color(0xa4bdbe);
    scene.environment = environment;
    scene.environmentRotation.y = 2.5;
    scene.environmentIntensity = .34;
    scene.fog = new THREE.FogExp2(0xa4bdbe, .035);

    const groundGeometry = new THREE.PlaneGeometry(98, 98, 128, 128).rotateX(-Math.PI / 2);
    const verts = groundGeometry.getAttribute('position');
    for (let i = 0; i < verts.count; i++) {
        verts.setY(i, terrainHeight(verts.getX(i), verts.getZ(i)));
    }
    groundGeometry.computeVertexNormals();
    const groundMaterial = new THREE.MeshStandardMaterial({
        map: base, normalMap: normal, roughnessMap: roughness, roughness: 1, metalness: 0,
    });
    groundMaterial.onBeforeCompile = shader => {
        shader.vertexShader = 'varying vec2 forestGround;\n' + shader.vertexShader.replace(
            '#include <begin_vertex>', '#include <begin_vertex>\nforestGround = position.xz;');
        shader.fragmentShader = 'varying vec2 forestGround;\n' + shader.fragmentShader.replace(
            '#include <map_fragment>', `#include <map_fragment>
                float dampLeaves = sin( forestGround.x * .52 + forestGround.y * .76 )
                    * cos( forestGround.x * .38 - forestGround.y * .93 )
                    + .39 * sin( forestGround.x * 1.81 + forestGround.y * 1.27 );
                diffuseColor.rgb *= mix( vec3( 1.03, 1.0, .94 ), vec3( .64, .76, .57 ),
                    smoothstep( -.32, .89, dampLeaves ) );`);
    };
    const ground = new THREE.Mesh(groundGeometry, groundMaterial);
    ground.receiveShadow = true;
    scene.add(ground);

    const ambient = new THREE.HemisphereLight(0xdcebd6, 0x6f5138, 1.29);
    scene.add(ambient);
    const sun = new THREE.DirectionalLight(0xffe4b6, 2.28);
    sun.position.set(-7.2, 14.1, 8.5);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.normalBias = .035;
    sun.shadow.bias = -.00012;
    Object.assign(sun.shadow.camera, { left: -12, right: 12, top: 13, bottom: -13, near: 1, far: 45 });
    scene.add(sun, sun.target);
    return { sun, stats: { assetCount: placements.size, instances: counts.reduce((a, b) => a + b, 0) } };
}
