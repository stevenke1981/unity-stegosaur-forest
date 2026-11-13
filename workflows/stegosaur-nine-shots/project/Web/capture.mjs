import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { startServer } from './server.mjs';

const require = createRequire(import.meta.url);
const { values: args } = parseArgs({ options: {
    output: { type: 'string' }, width: { type: 'string', default: '1280' },
    height: { type: 'string', default: '720' }, smoke: { type: 'boolean', default: false },
    chrome: { type: 'string' }, playwright: { type: 'string' },
    software: { type: 'boolean', default: false }, prototype: { type: 'string' },
    shots: { type: 'string' }, 'measure-turns': { type: 'boolean', default: false },
} });
if (!args.output) throw new Error('Specify a new capture directory with --output.');
const output = resolve(args.output);
const width = Number(args.width);
const height = Number(args.height);
const startedAt = new Date();
const shotSelection = args.shots?.split(',').map(id => id.trim());
assert(!shotSelection || (shotSelection.every(Boolean) && new Set(shotSelection).size === shotSelection.length),
    '--shots must be a comma-separated list of unique shot IDs.');
assert(width > 0 && height > 0 && width % 2 === 0 && height % 2 === 0, 'Capture size must be positive and even.');
await mkdir(output);
await mkdir(join(output, 'frames'));
await mkdir(join(output, 'review'));

const { chromium } = require(args.playwright ?? 'playwright-core');
const playbackSource = args.prototype ? resolve(args.prototype) : fileURLToPath(new URL('./assets/stegosaur-quad30k.glb', import.meta.url));
const hash = createHash('sha256');
for await (const part of createReadStream(playbackSource)) hash.update(part);
const sourceSha256 = hash.digest('hex');
const fixtures = args.prototype ? new Map([['/assets/prototype.glb', playbackSource]]) : undefined;
const { server, url } = await startServer(0, fixtures);
let browser;
try {
    const launchArgs = args.software
        ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
        : ['--enable-webgl', '--ignore-gpu-blocklist', ...(process.platform === 'win32' ? ['--use-angle=d3d11'] : [])];
    browser = await chromium.launch({ headless: true, executablePath: args.chrome, args: launchArgs });
    const modelParameter = args.prototype ? 'prototype' : '';
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    const errors = [];
    const watch = context => {
        context.on('pageerror', error => errors.push(error.message));
        context.on('console', message => {
            if (message.type() === 'error') errors.push(message.text());
        });
        context.on('response', response => {
            if (response.status() >= 400) errors.push(response.status() + ' ' + new URL(response.url()).pathname);
        });
    };
    watch(page);
    const captureOptions = new URLSearchParams({ capture: '' });
    if (modelParameter) captureOptions.set(modelParameter, '');
    if (args['measure-turns']) captureOptions.set('turn-metrics', '');
    await page.goto(url + '?' + captureOptions, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => window.stegosaurRender || window.stegosaurError, null, { timeout: 90000 });
    assert.equal(await page.evaluate(() => window.stegosaurError), undefined, errors.join('\n'));
    const info = await page.evaluate(() => window.stegosaurRender.info);
    assert.deepEqual(info.dimensions, [width, height]);
    assert.equal(info.bones, 36);
    assert.equal(info.skinned.skinnedParts, args.prototype ? 25 : 26);
    assert(info.authoredForward > 0, 'Head must face +Z in the Blender glTF coordinate frame.');
    assert.equal(info.clipCount, 9);
    assert.equal(info.fps, 24);
    assert.equal(info.frames, 676);
    assert(info.skinned.maxInfluences > 4, 'Expected the secondary glTF skin weight set.');
    if (!args.prototype) {
        assert.equal(info.skinned.triangles, 60000, 'Expected 30,000 Blender quads triangulated on export.');
        assert.deepEqual(info.shapes.slice().sort(),
            ['Blink', 'Breath', 'EyeTrack', 'NostrilFlare', 'RoarBlink', 'RoarTension', 'Throat'],
            'Expected the transferred facial and body morph targets.');
    }

    const draw = (index, frame, enabled = true) => page.evaluate(
        ([shot, localFrame, physics]) => window.stegosaurRender.draw(shot, localFrame, physics), [index, frame, enabled]);
    const png = async name => page.screenshot({ path: join(output, name), type: 'png' });
    const sha256 = data => createHash('sha256').update(data).digest('hex');
    let frameOut = 0;
    const startedFrames = performance.now();
    const shots = [];
    const reviewFrames = { run: [38, 43, 48, 54, 61], left: [44, 83], right: [63, 68], roar: [8, 11, 18, 33, 45, 54, 63, 68] };
    const selected = info.shots.map((shot, index) => ({ shot, index }))
        .filter(({ shot }) => !shotSelection || shotSelection.includes(shot.id));
    assert(!shotSelection || selected.length === shotSelection.length, 'Unknown --shots ID.');
    for (const { shot, index } of selected) {
        const mid = Math.floor(shot.frames * (shot.clip === 'Bite' ? .32 : shot.clip === 'Roar' ? .58 : .5));
        const marks = new Set([0, mid, shot.frames - 1]);
        const isTurn = shot.clip.startsWith('Turn');
        if (!args.smoke && isTurn) {
            marks.add(Math.floor(shot.frames * .25));
            marks.add(Math.floor(shot.frames * .75));
        }
        if (shotSelection) {
            for (const frame of reviewFrames[shot.id] ?? []) {
                if (frame < shot.frames) marks.add(frame);
            }
        }
        const measureTurn = args['measure-turns'] && isTurn;
        const frames = args.smoke && !measureTurn
            ? [...marks].sort((a, b) => a - b)
            : Array.from({ length: shot.frames }, (_, i) => i);
        const samples = [];
        const result = { ...shot, firstFrame: shotSelection ? shot.start : frameOut,
            renderedFrames: 0, samples,
            tailPeakMeters: 0, tailMaxAngleDegrees: 0, tailLengthErrorMeters: 0, jawPeakDegrees: 0, jawPeakFrame: 0,
            skinWrinklePeak: 0, morphPeak: {} };
        if (measureTurn) {
            result.hindLegDeformation = { measuredFrames: 0, restAreaCutoffSquareMeters: 1e-7, parts: {} };
        }
        for (const frame of frames) {
            const state = await draw(index, frame);
            if (state.jawDegrees > result.jawPeakDegrees) {
                result.jawPeakDegrees = state.jawDegrees;
                result.jawPeakFrame = frame;
            }
            result.tailPeakMeters = Math.max(result.tailPeakMeters, state.tail.offsetMeters);
            result.tailMaxAngleDegrees = Math.max(result.tailMaxAngleDegrees, state.tail.maxAngleDegrees);
            result.tailLengthErrorMeters = Math.max(result.tailLengthErrorMeters, state.tail.lengthErrorMeters);
            result.skinWrinklePeak = Math.max(result.skinWrinklePeak, state.skinWrinkleWeight);
            for (const [shape, value] of Object.entries(state.morphs)) {
                result.morphPeak[shape] = Math.max(result.morphPeak[shape] ?? 0, value);
            }
            if (marks.has(frame)) samples.push(state);
            assert.equal(state.tail.substeps, frame > 0 ? 5 : 0, 'A capture frame must advance five substeps after reset.');
            assert(state.drawCalls > 30 && state.triangles > 100000, 'The 3D forest and model must both be drawn.');
            if (measureTurn) {
                const results = await page.evaluate(() => window.stegosaurRender.measureHindLegs());
                result.hindLegDeformation.measuredFrames++;
                for (const [name, metric] of Object.entries(results)) {
                    const part = result.hindLegDeformation.parts[name] ??= {
                        trianglesPerFrame: metric.trianglesPerFrame,
                        excludedTrianglesPerFrame: metric.excludedTrianglesPerFrame,
                        samples: 0, over2x: 0, over4x: 0, belowQuarter: 0, over4xRestAreaCm2Frames: 0,
                        worst: { ratio: 0 },
                    };
                    for (const count of ['samples', 'over2x', 'over4x', 'belowQuarter', 'over4xRestAreaCm2Frames']) {
                        part[count] += metric[count];
                    }
                    if (metric.worst.ratio > part.worst.ratio) {
                        part.worst = { ...metric.worst, localFrame: frame, timelineFrame: shot.start + frame };
                    }
                }
            }
            if (args.smoke && !marks.has(frame)) continue;
            const number = shotSelection ? shot.start + frame : frameOut;
            await png(join('frames', 'frame_' + String(number).padStart(5, '0') + '.png'));
            result.renderedFrames++;
            frameOut++;
            if (frameOut % 72 === 0) console.log('Frames written', frameOut);
        }
        assert.equal(samples[0].tail.offsetMeters, 0, 'Spring must reset at each shot cut.');
        assert(result.tailMaxAngleDegrees <= 18.001 && result.tailLengthErrorMeters < .0001);
        if (shot.clip === 'Roar') {
            assert(result.jawPeakDegrees > 25 && samples.at(-1).jawDegrees < 1, 'The jaw must open and close in the roar.');
            if (!args.prototype) {
                assert(result.skinWrinklePeak > .9 && samples[0].skinWrinkleWeight === 0
                    && samples.at(-1).skinWrinkleWeight === 0, 'The jaw should drive localized wrinkles.');
                for (const shape of ['NostrilFlare', 'RoarTension', 'Throat']) {
                    assert(result.morphPeak[shape] > .1, 'Expected facial morph ' + shape + ' during Roar.');
                }
                if (!args.smoke || shotSelection) {
                    assert(result.morphPeak.RoarBlink > .85 && result.morphPeak.EyeTrack > .70,
                        'Expected both eye movement and blink during Roar.');
                }
            }
        }
        if (shot.clip === 'TailWhip' || shot.clip.startsWith('Turn')) {
            assert(result.tailPeakMeters > .02, 'Expected visible physical tail displacement in ' + shot.clip);
        }
        if (shot.clip === 'Walk' || shot.clip === 'Run') {
            const advance = samples.at(-1).position[2] - samples[0].position[2];
            assert(advance > 2, 'Expected forward travel in ' + shot.clip);
            result.forwardMeters = advance;
        }
        shots.push(result);
        console.log(shot.clip, result.renderedFrames + ' frames', 'tail ' + result.tailPeakMeters.toFixed(4) + ' m');
    }
    const frameCaptureWallSeconds = (performance.now() - startedFrames) / 1000;

    const turn = info.shots.findIndex(shot => shot.clip === 'TurnLeft');
    const stateOff = await draw(turn, 41, false);
    const off = await png('review/tail-disabled.png');
    const stateOn = await draw(turn, 41);
    const first = await png('review/tail-enabled.png');
    await draw(shotSelection ? selected.find(({ index }) => index !== turn)?.index ?? turn : 0, 0);
    const repeated = await draw(turn, 41);
    const second = await page.screenshot({ type: 'png' });
    assert.deepEqual(stateOn.tailTip, repeated.tailTip, 'Tail replay diverged.');
    assert.equal(sha256(first), sha256(second), 'Replay did not render identical pixels.');
    assert.notEqual(sha256(first), sha256(off), 'Tail toggling produced identical pixels.');
    assert.equal(stateOff.tail.offsetMeters, 0);
    assert(stateOn.tail.offsetMeters > .02);

    const interactive = await browser.newPage({ viewport: { width: 920, height: 690 }, deviceScaleFactor: 1 });
    watch(interactive);
    const localOrigin = new URL(url).origin;
    const nonLocal = [];
    interactive.on('request', request => {
        const target = new URL(request.url());
        if (target.protocol.startsWith('http') && target.origin !== localOrigin) nonLocal.push(target.origin);
    });
    await interactive.goto(url + '?' + modelParameter, { waitUntil: 'domcontentloaded' });
    await interactive.waitForFunction(() => window.stegosaurRender || window.stegosaurError, null, { timeout: 90000 });
    assert.equal(await interactive.evaluate(() => window.stegosaurError), undefined);
    assert.equal(await interactive.locator('#shots button').count(), 9);
    await interactive.locator('[data-clip="Roar"]').click();
    assert.equal(await interactive.locator('#shot-name').textContent(), '吼叫特寫');
    const roarStart = await interactive.locator('#seek').inputValue();
    await interactive.locator('#play').click();
    await interactive.waitForFunction(value => Number(document.querySelector('#seek').value) > value, Number(roarStart));
    await interactive.locator('#play').click();
    await interactive.locator('#seek').evaluate(element => {
        element.value = '531';
        element.dispatchEvent(new Event('input', { bubbles: true }));
    });
    assert.equal(await interactive.locator('#shot-name').textContent(), '試咬');
    await interactive.locator('#spring').uncheck();
    assert(!(await interactive.locator('#spring').isChecked()));
    await interactive.setViewportSize({ width: 490, height: 750 });
    await interactive.waitForFunction(() => document.querySelector('canvas').clientWidth === 490);
    await interactive.screenshot({ path: join(output, 'review/mobile-view.png') });
    await interactive.close();
    assert.deepEqual(nonLocal, [], 'The viewer should run offline.');

    const results = {
        ...info, browserVersion: browser.version(), smoke: args.smoke,
        sourceModel: args.prototype ? 'Prototype baseline' : 'stegosaur-quad30k.glb', sourceSha256,
        captureStartedUtc: startedAt.toISOString(), frameCaptureWallSeconds,
        authoredUnitsToMeters: 1.92, outputFrames: frameOut, shots,
        frameNumbering: shotSelection ? 'timeline' : 'sequence',
        selectedShots: selected.map(({ shot }) => shot.id),
        deterministicReplay: 'PASS',
        toggleChangedPixels: 'PASS', springToggleTailMeters: stateOn.tail.offsetMeters,
        requestsOutsideLoopback: nonLocal,
        checkedUI: ['nine shot buttons', 'play and pause', 'seek', 'physics checkbox', 'responsive canvas'],
        errors,
    };
    await writeFile(join(output, 'three-render-report.json'), JSON.stringify(results, null, 2) + '\n');
    assert.equal(errors.length, 0, errors.join('\n'));
    console.log('Three.js capture verified', frameOut, 'frames.');
} finally {
    if (browser) await browser.close();
    server.close();
}
