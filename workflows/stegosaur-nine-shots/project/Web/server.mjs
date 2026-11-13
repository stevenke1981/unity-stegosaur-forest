import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { extname, isAbsolute, relative, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const directory = fileURLToPath(new URL('.', import.meta.url));
const types = new Map(Object.entries({
    '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
    '.json': 'application/json', '.jpg': 'image/jpeg', '.png': 'image/png',
    '.glb': 'model/gltf-binary', '.m4a': 'audio/mp4', '.mp4': 'video/mp4', '.hdr': 'application/octet-stream',
}));

export async function startServer(port = 0, extraFiles = new Map()) {
    const publicFiles = new Set([
        'index.html', 'style.css', 'viewer.js', 'camera.js', 'forest.js', 'tail.js', 'skin.js',
        'creature-material.js', 'motion.json',
        'node_modules/three/build/three.core.min.js', 'node_modules/three/build/three.module.min.js',
        'node_modules/three/examples/jsm/loaders/GLTFLoader.js',
        'node_modules/three/examples/jsm/loaders/HDRLoader.js',
        'node_modules/three/examples/jsm/utils/BufferGeometryUtils.js',
        'node_modules/three/examples/jsm/utils/SkeletonUtils.js',
    ]);
    const server = createServer(async (request, response) => {
        if (request.method !== 'GET' && request.method !== 'HEAD') {
            response.writeHead(405).end();
            return;
        }

        try {
            const url = new URL(request.url, 'http://127.0.0.1');
            const path = decodeURIComponent(url.pathname);
            const local = resolve(directory, '.' + (path === '/' ? '/index.html' : path));
            const inside = relative(directory, local);
            if (!inside || inside.startsWith('..') || isAbsolute(inside)) {
                response.writeHead(404).end();
                return;
            }

            // Capture callers can register exact local fixture routes.
            const localRoute = inside.replaceAll('\\', '/');
            const fixture = extraFiles.get(path);
            if (!fixture && !publicFiles.has(localRoute) && !localRoute.startsWith('assets/')) {
                response.writeHead(404).end();
                return;
            }
            const file = fixture ?? local;
            const metadata = await stat(file);
            if (!metadata.isFile()) {
                response.writeHead(404).end();
                return;
            }
            const headers = {
                'Content-Type': types.get(extname(file)) ?? 'application/octet-stream',
                'Accept-Ranges': 'bytes',
                'X-Content-Type-Options': 'nosniff',
            };
            const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.range ?? '');
            let start = 0;
            let end = metadata.size - 1;
            if (range) {
                start = Number(range[1]);
                end = range[2] ? Math.min(end, Number(range[2])) : end;
                if (start > end) {
                    response.writeHead(416, { 'Content-Range': 'bytes */' + metadata.size }).end();
                    return;
                }
                headers['Content-Range'] = 'bytes ' + start + '-' + end + '/' + metadata.size;
            }
            headers['Content-Length'] = end - start + 1;
            response.writeHead(range ? 206 : 200, headers);
            if (request.method === 'HEAD') response.end();
            else createReadStream(file, { start, end }).on('error', error => response.destroy(error)).pipe(response);
        } catch (error) {
            if (!response.headersSent) response.writeHead(error.code === 'ENOENT' ? 404 : 400);
            response.end();
        }
    });
    await new Promise((accept, reject) => {
        server.once('error', reject);
        server.listen(port, '127.0.0.1', accept);
    });
    return { server, url: 'http://127.0.0.1:' + server.address().port + '/' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    const { url } = await startServer(Number(process.argv[2] ?? 8766));
    console.log('Three.js  ' + url);
}
