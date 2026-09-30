# Local graphics dependency

Three.js **0.180.0**, MIT licensed. Only its two browser modules and OrbitControls are included. They were extracted from the official `three@0.180.0` npm tarball after verifying the registry's SHA-512 integrity metadata.

Upstream: https://github.com/mrdoob/three.js/tree/r180

Files: `three.module.min.js`, `three.core.min.js`, `OrbitControls.js`, and the original `LICENSE`. The only source adaptation is OrbitControls' `three` import changed to `./three.module.min.js`, so browser imports resolve without a package manager or remote CDN. Keep the license when redistributing.

This is a render-only browser dependency. It makes no telemetry, model, or third-party network requests. Kravel retains its same-origin script policy.
