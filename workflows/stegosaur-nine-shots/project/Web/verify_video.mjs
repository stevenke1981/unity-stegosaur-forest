import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { startServer } from './server.mjs';

const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    chrome: { type: 'string' }, playwright: { type: 'string' }, engine: { type: 'string', default: 'three' },
} });
if (positionals.length !== 1) throw new Error('Specify the completed capture directory.');
if (!['unity', 'three'].includes(values.engine)) throw new Error('Expected --engine unity or --engine three.');
const folder = resolve(positionals[0]);
const video = JSON.parse(await readFile(join(folder, values.engine + '-video-report.json'), 'utf8'));
const { chromium } = createRequire(import.meta.url)(values.playwright ?? 'playwright-core');
const { server, url } = await startServer(0, new Map([['/playback.mp4', join(folder, video.file)]]));
let browser;
try {
    browser = await chromium.launch({ headless: true, executablePath: values.chrome });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
        if (response.status() >= 400) errors.push(response.status() + ' ' + new URL(response.url()).pathname);
    });
    await page.route(url + 'video-check', route => route.fulfill({ contentType: 'text/html', body:
        '<!doctype html><link rel="icon" href="data:,"><video controls muted playsinline preload="auto" src="/playback.mp4"></video>',
    }));
    await page.goto(url + 'video-check');
    const player = page.locator('video');
    await page.waitForFunction(() => document.querySelector('video').readyState >= 2 || document.querySelector('video').error);
    const opened = await player.evaluate(media => {
        if (media.error) throw new Error(media.error.message);
        return { width: media.videoWidth, height: media.videoHeight, duration: media.duration };
    });
    assert.deepEqual([opened.width, opened.height], video.video.dimensions);
    assert(Math.abs(opened.duration - video.video.duration) < .001);
    await player.evaluate(async (media, seek) => {
        const atFrame = new Promise(accept => media.addEventListener('seeked', accept, { once: true }));
        media.currentTime = seek;
        await atFrame;
    }, video.sound.jawPeakAt);
    assert(Math.abs(await player.evaluate(media => media.currentTime) - video.sound.jawPeakAt) < .08);
    await player.evaluate(async media => {
        const atStart = new Promise(accept => media.addEventListener('seeked', accept, { once: true }));
        media.currentTime = 0;
        await atStart;
        media.playbackRate = 2;
        await media.play();
    });
    await page.waitForFunction(() => document.querySelector('video').ended || document.querySelector('video').error,
        null, { timeout: 25000 });
    const ended = await player.evaluate(media => ({ ended: media.ended, at: media.currentTime, error: media.error?.message }));
    assert(ended.ended && !ended.error && Math.abs(ended.at - opened.duration) < .08);
    assert.equal(errors.length, 0, errors.join('\n'));
    const report = { browserVersion: browser.version(), videoSha256: video.sha256, ...opened, ...ended,
        seekToJawPeak: 'PASS', mutedPlaybackAt2x: 'PASS', errors };
    await writeFile(join(folder, values.engine + '-video-playback.json'), JSON.stringify(report, null, 2) + '\n');
    console.log('Chrome loaded, sought and played all', opened.duration.toFixed(3), 'seconds.');
} finally {
    if (browser) await browser.close();
    server.close();
}
