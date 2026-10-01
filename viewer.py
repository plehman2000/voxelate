import json
import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>3D Voxel Viewer</title>
    <style>
        body { margin: 0; overflow: hidden; font-family: sans-serif; background-color: #1a1a1a; color: #fff; }
        #canvas-container { width: 100vw; height: 100vh; }
        #ui {
            position: absolute; top: 15px; left: 15px; z-index: 10;
            background: rgba(0, 0, 0, 0.7); padding: 15px; border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }
        select {
            padding: 6px 12px; font-size: 14px; border-radius: 4px;
            border: 1px solid #555; background: #333; color: white; margin-bottom: 10px;
        }
        #info { font-size: 12px; line-height: 1.4; color: #ccc; max-width: 250px; }
        #controls-hint { position: absolute; bottom: 15px; left: 15px; background: rgba(0,0,0,0.6); padding: 8px 12px; border-radius: 4px; font-size: 12px; }
    </style>
    <!-- Three.js and OrbitControls -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
</head>
<body>
    <div id="ui">
        <label for="fileSelect">Select Model:</label><br>
        <select id="fileSelect"><option value="">Loading files...</option></select>
        <div id="info"></div>
    </div>

    <div id="controls-hint">
        <b>Controls:</b> Left Click + Drag = Rotate | Scroll = Zoom | Right Click + Drag = Pan
    </div>

    <div id="canvas-container"></div>

    <script>
        // Material color definitions
        const MATERIAL_COLORS = {
            'stone': 0x7d7d7d, 'wood': 0x9c7f4e, 'leaf': 0x3b6e2a, 'grass': 0x5d8c3a,
            'water': 0x3b5fa8, 'metal': 0xc6c6c6, 'glass': 0xa9cfe0, 'thatch': 0xb8a05a,
            'cloth': 0x9e2b27, 'bone': 0xd9d3b8,
            'concrete': 0x6e6e6e, 'corrugated_metal': 0x8a8f93, 'neon_signage': 0xb04a9c,
            'solar_paneling': 0x2b3a5c, 'tinted_glass': 0x4a6a7a, 'slate': 0x4b4f57,
            'dark_stone': 0x454545, 'timber': 0x6b5233, 'earth': 0x866043, 'sod': 0x5d7a3a,
            'default': 0x7d7d7d
        };

        let scene, camera, renderer, controls, voxelGroup;

        function init() {
            const container = document.getElementById('canvas-container');

            // Scene setup
            scene = new THREE.Scene();
            scene.background = new THREE.Color(0x222228);

            // Camera setup
            camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000);

            // Renderer setup
            renderer = new THREE.WebGLRenderer({ antialias: true });
            renderer.setSize(window.innerWidth, window.innerHeight);
            renderer.setPixelRatio(window.devicePixelRatio);
            renderer.shadowMap.enabled = true;
            container.appendChild(renderer.domElement);

            // Orbit Controls (Rotation, Scroll Zoom, Panning)
            controls = new THREE.OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.dampingFactor = 0.05;

            // Lighting
            const ambientLight = new THREE.AmbientLight(0xffffff, 0.5);
            scene.add(ambientLight);

            const dirLight = new THREE.DirectionalLight(0xffffff, 0.65);
            dirLight.position.set(20, 40, 20);
            dirLight.castShadow = true;
            scene.add(dirLight);

            const dirLight2 = new THREE.DirectionalLight(0x90b0ff, 0.3);
            dirLight2.position.set(-20, -20, -20);
            scene.add(dirLight2);

            // Grid Floor
            const gridHelper = new THREE.GridHelper(30, 30, 0x444444, 0x333333);
            gridHelper.position.y = -0.5;
            scene.add(gridHelper);

            // Initialize Group
            voxelGroup = new THREE.Group();
            scene.add(voxelGroup);

            // Window resize handler
            window.addEventListener('resize', onWindowResize);

            // Load file list
            fetchFileList();
        }

        async function fetchFileList() {
            const res = await fetch('/api/files');
            const files = await res.json();
            const select = document.getElementById('fileSelect');
            select.innerHTML = '';
            
            files.forEach(file => {
                const opt = document.createElement('option');
                opt.value = file;
                opt.textContent = file;
                select.appendChild(opt);
            });

            if (files.length > 0) {
                select.addEventListener('change', (e) => loadVoxelFile(e.target.value));
                loadVoxelFile(files[0]);
            } else {
                document.getElementById('info').textContent = "No .json voxel files found.";
            }
        }

        async function loadVoxelFile(filename) {
            const res = await fetch('/' + filename);
            const data = await res.json();

            // Clear current voxels
            while(voxelGroup.children.length > 0){ 
                voxelGroup.remove(voxelGroup.children[0]); 
            }

            document.getElementById('info').textContent = data.brief_summary || '';

            const geometry = new THREE.BoxGeometry(1, 1, 1);
            const materialsCache = {};

            // Calculate bounding box center to pivot around object center
            let minX = Infinity, maxX = -Infinity;
            let minY = Infinity, maxY = -Infinity;
            let minZ = Infinity, maxZ = -Infinity;

            data.voxels.forEach(v => {
                minX = Math.min(minX, v.x); maxX = Math.max(maxX, v.x);
                minY = Math.min(minY, v.y); maxY = Math.max(maxY, v.y);
                minZ = Math.min(minZ, v.z); maxZ = Math.max(maxZ, v.z);
            });

            const centerX = (minX + maxX) / 2;
            const centerY = (minY + maxY) / 2;

            // Render voxels
            data.voxels.forEach(v => {
                const matType = v.material || 'default';
                if (!materialsCache[matType]) {
                    const color = MATERIAL_COLORS[matType] || MATERIAL_COLORS['default'];
                    materialsCache[matType] = new THREE.MeshStandardMaterial({ 
                        color: color, 
                        roughness: 1.0
                    });
                }

                const mesh = new THREE.Mesh(geometry, materialsCache[matType]);
                // Shift coordinates so model is centered at origin (0,0,0)
                mesh.position.set(v.x - centerX, v.z - minZ, v.y - centerY); // Z up; lowest voxel sits on the grid plane
                mesh.castShadow = true;
                mesh.receiveShadow = true;
                voxelGroup.add(mesh);
            });

            // Adjust camera position to fit model
            const maxDim = Math.max(maxX - minX, maxY - minY, maxZ - minZ);
            camera.position.set(maxDim * 2, maxDim * 1.5, maxDim * 2);
            const midY = (maxZ - minZ) / 2;
            camera.position.y += midY;
            camera.lookAt(0, midY, 0);
            controls.target.set(0, midY, 0);
            controls.update();
        }

        function onWindowResize() {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        }

        function animate() {
            requestAnimationFrame(animate);
            controls.update();
            renderer.render(scene, camera);
        }

        init();
        animate();
    </script>
</body>
</html>
"""

class VoxelServerHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode('utf-8'))
        elif self.path == '/api/files':
            files = []
            for root, _, names in os.walk('outputs'):
                for n in names:
                    if n.endswith('.json') and not n.endswith('.spec.json'):
                        rel = os.path.relpath(os.path.join(root, n), '.')
                        files.append(rel.replace(os.sep, '/'))
            files.sort()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(files).encode('utf-8'))
        else:
            super().do_GET()

def run_server(port=8000):
    server_address = ('', port)
    httpd = HTTPServer(server_address, VoxelServerHandler)
    print(f"Voxel Web Viewer running at http://localhost:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

if __name__ == '__main__':
    run_server()