import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { createForest } from './forest.js';
import { createHindLegAreaProbe, installSkinning } from './skin.js';
import { installCreatureMaterials } from './creature-material.js';
import { TailSpring, tailNames } from './tail.js';
import { compose, placeSubject } from './camera.js';

const options = new URLSearchParams(location.search);
const capture = options.has('capture');
if (capture) document.body.classList.add('capture');
const modelUrl = options.has('prototype') ? './assets/prototype.glb' : './assets/stegosaur-quad30k.glb';
const asset = name => new URL(name, import.meta.url).href;
const labels = {
    Idle: '待機', Walk: '步行', Run: '四足快步', TurnLeft: '左轉', TurnRight: '右轉',
    Roar: '吼叫特寫', Bite: '試咬', TailWhip: '甩尾', HitReaction: '受擊反應',
};
const viewport = document.querySelector('#viewport');
const play = document.querySelector('#play');
const seek = document.querySelector('#seek');
const shotButtons = document.querySelector('#shots');
const status = document.querySelector('#status');
const spring = document.querySelector('#spring');
const sound = document.querySelector('#sound');
const audio = document.querySelector('#roar');
const timecode = document.querySelector('#time');
const activeShotLabel = document.querySelector('#shot-name');
const activeShotTime = document.querySelector('#shot-time');

try {
    const renderer = new THREE.WebGLRenderer({
        antialias: true, preserveDrawingBuffer: capture, powerPreference: 'high-performance',
    });
    renderer.setPixelRatio(capture ? 1 : Math.min(devicePixelRatio, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.14;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    viewport.append(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 16 / 9, .055, 150);
    const subject = new THREE.Group();
    scene.add(subject);
    const [motion, character, forest] = await Promise.all([
        fetch(asset('./motion.json')).then(response => {
            if (!response.ok) throw new Error('無法載入九鏡資料');
            return response.json();
        }),
        new GLTFLoader().loadAsync(asset(modelUrl)),
        createForest(scene, renderer),
    ]);

    const model = character.scene;
    model.scale.setScalar(1.92);
    subject.add(model);
    subject.updateMatrixWorld(true);
    const skeleton = name => {
        const node = model.getObjectByName(name);
        if (!node?.isBone) throw new Error('找不到骨骼 ' + name);
        return node;
    };
    const head = skeleton('Bone_023');
    const jaw = skeleton('LowerJaw');
    const tailBones = tailNames.map(skeleton);
    const tail = new TailSpring(tailBones);
    const rawLocal = tailBones.map(bone => bone.quaternion.clone());
    const appearance = options.has('prototype') ? null : await installCreatureMaterials(model);
    if (appearance && appearance.count !== 5) throw new Error('劍龍的皮膚、骨板或口腔材質未載入');
    const skin = installSkinning(model);
    const expectedPrimitives = options.has('prototype') ? 25 : 26;
    if (skin.skinnedParts !== expectedPrimitives || skin.maxWeightSumError > .001) {
        throw new Error('蒙皮分件或權重總和與交付模型不一致');
    }
    const measureHindLegs = capture && options.has('turn-metrics') ? createHindLegAreaProbe(model) : undefined;
    const meshBounds = new THREE.Box3().setFromObject(model);
    const headRest = head.getWorldPosition(new THREE.Vector3());
    const authoredForward = headRest.z - tailBones.at(-1).getWorldPosition(new THREE.Vector3()).z;
    const rest = [];
    model.traverse(node => {
        rest.push({
            node, position: node.position.clone(), quaternion: node.quaternion.clone(), scale: node.scale.clone(),
            shapes: node.morphTargetInfluences?.slice(),
        });
    });

    const modelBones = rest.filter(item => item.node.isBone).length;
    const mixer = new THREE.AnimationMixer(model);
    const animations = new Map(character.animations.map(clip => [clip.name, clip]));
    const specs = new Map(motion.clips.map(clip => [clip.name, clip]));
    const fps = motion.fps;
    let end = 0;
    const shots = motion.shots.map(shot => {
        const clip = animations.get(shot.clip);
        const spec = specs.get(shot.clip);
        if (!clip || !spec || !labels[shot.clip]) throw new Error('找不到動作 ' + shot.clip);
        const minimum = Math.min(...clip.tracks.map(track => track.times[0]));
        const expected = spec.intervals / fps;
        if (minimum < 0 || minimum > 1.01 / fps || Math.abs(clip.duration - minimum - expected) > .05)
            throw new Error('動作時間與鏡頭資料不符 ' + shot.clip);
        const action = mixer.clipAction(clip).setLoop(THREE.LoopOnce, 1);
        action.clampWhenFinished = true;
        const data = { ...shot, spec, action, minimum, start: end };
        end += shot.frames;
        return data;
    });

    const currentPose = [];
    const shapes = new Set();
    let shapeCurves = 0;
    model.traverse(node => {
        if (!node.isMesh || !node.morphTargetDictionary) return;
        currentPose.push(node);
        for (const name of Object.keys(node.morphTargetDictionary)) shapes.add(name);
    });
    for (const clip of character.animations) {
        shapeCurves += clip.tracks.filter(track => track.name.endsWith('.morphTargetInfluences')).length;
    }
    // Retain Blender-baked morph channels when present. Use the shared authored values
    // only for clips that exported without a morph animation track.
    for (const shot of shots) {
        shot.fallbackShapes = !shot.action.getClip().tracks.some(track => track.name.endsWith('.morphTargetInfluences'));
    }

    const tip = skeleton(tailNames.at(-1));
    const gl = renderer.getContext();
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    const rendererName = gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER);
    const frameTime = new Intl.NumberFormat('en', { minimumIntegerDigits: 2, useGrouping: false });
    const clock = frame => {
        const seconds = Math.floor(frame / fps);
        return frameTime.format(Math.floor(seconds / 60)) + ':' + frameTime.format(seconds % 60);
    };
    const closed = new THREE.Quaternion();
    let currentShot = null;
    let currentFrame = -1;
    let physicsEnabled = true;
    let metrics;
    let playing = false;
    let started = 0;
    let startedFrame = 0;

    function setPose(shot, frame) {
        if (frame > 0) {
            // AnimationMixer can skip assigning unchanged keys. Restore the
            // authored local pose before asking it to evaluate the next frame.
            for (const [index, bone] of tailBones.entries()) bone.quaternion.copy(rawLocal[index]);
        }
        const sample = shot.spec.cyclic ? frame % shot.spec.intervals : Math.min(frame, shot.spec.intervals);
        shot.action.time = shot.minimum + sample / fps;
        mixer.update(0);
        for (const [index, bone] of tailBones.entries()) rawLocal[index].copy(bone.quaternion);
        if (shot.fallbackShapes) {
            for (const part of currentPose) {
                for (const shape of shot.spec.shapes) {
                    const index = part.morphTargetDictionary[shape.name];
                    if (index !== undefined) part.morphTargetInfluences[index] = shape.values[sample] / 100;
                }
            }
        }
        placeSubject(subject, shot, shot.spec, frame / fps);
        if (frame === 0) closed.copy(jaw.quaternion);
        const tailStats = tail.apply(frame === 0 ? 0 : 1 / fps, physicsEnabled);
        const morphs = {};
        for (const part of currentPose) {
            for (const [name, index] of Object.entries(part.morphTargetDictionary)) {
                morphs[name] = Math.max(morphs[name] ?? 0, part.morphTargetInfluences[index]);
            }
        }
        const jawDegrees = THREE.MathUtils.radToDeg(closed.angleTo(jaw.quaternion));
        metrics = {
            jawDegrees,
            skinWrinkleWeight: appearance?.setJaw(jawDegrees) ?? 0,
            morphs,
            tailTip: tail.tip.toArray(),
            rawTailTip: tail.rawTip.toArray(),
            tailTipOffset: tail.tipOffset.toArray(),
            position: subject.position.toArray(),
            headingDegrees: THREE.MathUtils.radToDeg(subject.rotation.y),
            tail: { ...tailStats },
        };
    }

    function updateControls(shot, frame, index) {
        const global = shot.start + frame;
        seek.value = global;
        timecode.value = clock(global) + ' / ' + clock(end);
        activeShotLabel.textContent = labels[shot.clip];
        activeShotTime.value = clock(global);
        for (const [i, button] of [...shotButtons.children].entries()) {
            button.setAttribute('aria-pressed', String(i === index));
        }
    }

    function draw(index, frame, enablePhysics = true) {
        const shot = shots[index];
        if (!shot) throw new RangeError('Invalid shot.');
        const target = THREE.MathUtils.clamp(Math.floor(frame), 0, shot.frames - 1);
        if (currentShot !== shot || target < currentFrame || enablePhysics !== physicsEnabled) {
            mixer.stopAllAction();
            for (const { node, position, quaternion, scale, shapes } of rest) {
                node.position.copy(position);
                node.quaternion.copy(quaternion);
                node.scale.copy(scale);
                if (shapes) node.morphTargetInfluences.splice(0, shapes.length, ...shapes);
            }
            shot.action.reset().play();
            tail.reset();
            physicsEnabled = enablePhysics;
            currentShot = shot;
            currentFrame = -1;
        }
        for (let i = currentFrame + 1; i <= target; i++) setPose(shot, i);
        currentFrame = target;
        const focus = compose(camera, shot.camera, target, shot.frames - 1, subject, head, tip);
        forest.sun.target.position.copy(subject.position);
        forest.sun.position.copy(subject.position).add(sunOffset);
        renderer.render(scene, camera);
        if (!capture) updateControls(shot, target, index);
        return { ...metrics, shot: shot.id, clip: shot.clip, frame: target,
            cameraPosition: camera.position.toArray(), cameraFocus: focus.toArray(),
            triangles: renderer.info.render.triangles, drawCalls: renderer.info.render.calls };
    }

    function show(global) {
        const clamped = THREE.MathUtils.clamp(global, 0, end - 1);
        const index = shots.findLastIndex(shot => shot.start <= clamped);
        return draw(index, clamped - shots[index].start, spring.checked);
    }

    function syncAudio(frame, seekNow) {
        if (!sound.checked || !playing) {
            audio.pause();
            return;
        }
        if (seekNow || Math.abs(audio.currentTime - frame / fps) > .19) audio.currentTime = frame / fps;
        if (audio.paused) audio.play().catch(() => { sound.checked = false; });
    }

    function setPlaying(value) {
        playing = value;
        if (value && +seek.value === end - 1) show(0);
        started = performance.now();
        startedFrame = +seek.value;
        play.textContent = playing ? '暫停' : '播放';
        syncAudio(startedFrame, true);
    }

    const sunOffset = new THREE.Vector3(-7.2, 14.1, 8.5);
    function resize() {
        const { width, height } = viewport.getBoundingClientRect();
        renderer.setSize(Math.round(width), Math.round(height), false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        if (capture) draw(0, 0);
        else show(+seek.value);
    }
    seek.max = end - 1;
    play.disabled = seek.disabled = spring.disabled = sound.disabled = false;
    for (const shot of shots) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = labels[shot.clip];
        button.dataset.clip = shot.clip;
        button.setAttribute('aria-pressed', 'false');
        button.addEventListener('click', () => {
            show(shot.start);
            setPlaying(playing);
        });
        shotButtons.append(button);
    }
    play.addEventListener('click', () => setPlaying(!playing));
    seek.addEventListener('input', () => {
        show(+seek.value);
        setPlaying(playing);
    });
    spring.addEventListener('change', () => show(+seek.value));
    sound.addEventListener('change', () => syncAudio(+seek.value, true));
    resize();
    new ResizeObserver(() => {
        // A capture never resizes after setup, and is driven by its explicit API.
        if (!capture) resize();
    }).observe(viewport);
    status.textContent = '';
    if (!capture) {
        let displayed = -1;
        const animate = now => {
            if (playing) {
                const frame = Math.min(end - 1, startedFrame + Math.floor((now - started) * fps / 1000));
                if (frame !== displayed) {
                    show(frame);
                    syncAudio(frame, false);
                    displayed = frame;
                }
                if (frame === end - 1) setPlaying(false);
            } else {
                displayed = -1;
            }
            requestAnimationFrame(animate);
        };
        requestAnimationFrame(animate);
    }
    window.stegosaurRender = {
        draw, measureHindLegs,
        info: {
            rendererName, threeRevision: THREE.REVISION, dimensions: renderer.getDrawingBufferSize(new THREE.Vector2()).toArray(),
            skinned: skin, bones: modelBones, shapes: [...shapes], shapeCurves,
            clipCount: shots.length, fps, frames: end,
            shots: shots.map(({ id, clip, frames, start, minimum, fallbackShapes }) => ({
                id, clip, frames, start, minimum, fallbackShapes,
            })),
            modelBounds: [meshBounds.min.toArray(), meshBounds.max.toArray()],
            headRest: headRest.toArray(), authoredForward, forest: forest.stats,
        },
    };
} catch (error) {
    status.textContent = '載入失敗。' + error.message;
    window.stegosaurError = error.message;
    console.error(error);
}
