import * as THREE from "./vendor/three.module.min.js";
import { OrbitControls } from "./vendor/OrbitControls.js";
import { layoutTopology, resourceId } from "./topology.mjs";

const colors = {Pod: 0x82aeff, Deployment: 0x8de5cf, ReplicaSet: 0x77b9c6, ConfigMap: 0xefc581, Service: 0xbcadff};
const healthColors = {healthy: 0x8de5cf, warning: 0xefc581, critical: 0xff8293};
const linkColors = {owns: 0x8de5cf, configures: 0xefc581, selects: 0xbcadff};
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function mesh(geometry, color, position = [0, 0, 0], metalness = .2) {
  const value = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({color, roughness: .46, metalness}));
  value.position.set(...position);
  return value;
}

function createModel(resource) {
  const group = new THREE.Group(), color = colors[resource.kind] || colors.Pod;
  const base = mesh(new THREE.CylinderGeometry(.78, .87, .16, 6), 0x26394e, [0, .08, 0]);
  group.add(base);
  const ring = mesh(new THREE.TorusGeometry(.77, .035, 8, 36), healthColors[resource.health] || healthColors.warning, [0, .2, 0]);
  ring.rotation.x = -Math.PI / 2;
  ring.material.emissive.copy(ring.material.color); ring.material.emissiveIntensity = .35;
  group.add(ring); group.userData.ring = ring;
  if (resource.kind === "Pod") {
    group.add(mesh(new THREE.CapsuleGeometry(.46, .47, 5, 12), color, [0, .98, 0]));
    group.add(mesh(new THREE.BoxGeometry(.67, .27, .12), 0x14263c, [0, 1.06, .43]));
    [-.16, .16].forEach((x) => group.add(mesh(new THREE.BoxGeometry(.08, .07, .05), 0xd4f7ff, [x, 1.08, .51])));
    [-.28, .28].forEach((x) => group.add(mesh(new THREE.BoxGeometry(.2, .12, .3), color, [x, .31, .14])));
  } else if (resource.kind === "Deployment") {
    for (let i = 0; i < 3; i++) {
      const cube = mesh(new THREE.BoxGeometry(.88, .33, .88), color, [0, .52 + i * .42, 0]);
      cube.rotation.y = Math.PI / 4; group.add(cube);
      group.add(mesh(new THREE.BoxGeometry(.4, .035, .025), 0x253d49, [0, .52 + i * .42, .64]));
    }
  } else if (resource.kind === "ReplicaSet") {
    [[-.34, .66, -.18], [.34, .66, -.18], [0, 1.22, .12]].forEach((pos) => {
      const cube = mesh(new THREE.BoxGeometry(.55, .55, .55), color, pos); cube.rotation.y = Math.PI / 4; group.add(cube);
    });
  } else if (resource.kind === "ConfigMap") {
    group.add(mesh(new THREE.BoxGeometry(.95, 1.14, .22), color, [0, .99, 0]));
    [.65, .98, 1.31].forEach((y, i) => group.add(mesh(new THREE.BoxGeometry(.58 - i * .1, .07, .035), 0x4d402e, [-.04, y, .13])));
    group.add(mesh(new THREE.BoxGeometry(.25, .2, .035), 0xffe4aa, [.28, 1.48, .14]));
  } else {
    group.add(mesh(new THREE.OctahedronGeometry(.43), color, [0, .97, 0], .45));
    const halo = mesh(new THREE.TorusGeometry(.63, .065, 8, 32), color, [0, .97, 0]); halo.rotation.x = .4; group.add(halo);
    group.add(mesh(new THREE.CylinderGeometry(.04, .04, .45, 8), color, [0, .43, 0]));
  }
  group.traverse((object) => { if (object.isMesh) object.userData.resourceId = resourceId(resource); });
  return group;
}

function release(group) {
  group.traverse((object) => {
    object.geometry?.dispose();
    if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose());
    else object.material?.dispose();
  });
  group.removeFromParent();
}

// Geometry is local and synthetic; the relationships and health are live API evidence.
export class ClusterScene {
  constructor(host, labels, onSelect, onCamera) {
    this.host = host; this.labels = labels; this.onSelect = onSelect; this.onCamera = onCamera;
    this.models = new Map(); this.buttons = new Map(); this.resources = []; this.connections = [];
    this.edges = []; this.showLinks = true; this.selected = null; this.visibleIds = new Set();
    this.projected = new THREE.Vector3(); this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2();
    this.scene = new THREE.Scene(); this.scene.fog = new THREE.Fog(0x0b1524, 38, 90);
    this.camera = new THREE.PerspectiveCamera(42, 1, .1, 160);
    this.camera.position.set(18, 23, 28);
    try {
      this.renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, powerPreference: "low-power"});
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      this.renderer.setClearColor(0x0b1524, 1); this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      host.append(this.renderer.domElement); host.parentElement.dataset.renderMode = "webgl";
    } catch {
      this.fallback = true; host.parentElement.classList.add("scene-fallback");
      host.parentElement.dataset.renderMode = "fallback"; this.onCamera("WebGL unavailable · list view");
      return;
    }
    const canvas = this.renderer.domElement;
    canvas.tabIndex = 0; canvas.setAttribute("aria-label", "3D cluster. Drag to orbit, Shift-drag to pan, scroll to zoom. Arrow keys pan; F focuses; R fits.");
    this.scene.add(new THREE.HemisphereLight(0xdbecff, 0x1b2b42, 2.6));
    const keyLight = new THREE.DirectionalLight(0xffffff, 3); keyLight.position.set(8, 14, 8); this.scene.add(keyLight);
    const rimLight = new THREE.DirectionalLight(0x82aeff, 2); rimLight.position.set(-8, 7, -12); this.scene.add(rimLight);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(100, 100), new THREE.MeshBasicMaterial({color: 0x0b1727})); floor.rotation.x = -Math.PI / 2; floor.position.y = -.03; this.scene.add(floor);
    const grid = new THREE.GridHelper(70, 70, 0x3c5970, 0x233449); grid.material.transparent = true; grid.material.opacity = .42; this.scene.add(grid);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = !reducedMotion; this.controls.dampingFactor = .09;
    this.controls.minDistance = 4; this.controls.maxDistance = 75; this.controls.maxPolarAngle = Math.PI * .47;
    this.controls.zoomToCursor = true; this.controls.target.set(0, .6, 0);
    this.controls.listenToKeyEvents(canvas);
    this.controls.addEventListener("change", () => this.invalidate());
    this.controls.addEventListener("start", () => { this.tween = null; this.invalidate(); });
    canvas.addEventListener("pointerdown", (event) => { this.down = {x: event.clientX, y: event.clientY}; canvas.focus({preventScroll: true}); });
    canvas.addEventListener("pointerup", (event) => {
      if (event.button !== 0 || !this.down || Math.hypot(event.clientX - this.down.x, event.clientY - this.down.y) > 5) return;
      const id = this.pick(event); if (id) this.onSelect(id);
    });
    canvas.addEventListener("pointermove", (event) => { if (!event.buttons) { const id = this.pick(event); canvas.dataset.hover = String(Boolean(id)); if (this.hovered !== id) { this.hovered = id; this.invalidate(); } } });
    canvas.addEventListener("pointerleave", () => { canvas.dataset.hover = "false"; this.hovered = null; this.invalidate(); });
    canvas.addEventListener("dblclick", (event) => { const id = this.pick(event); if (id) { this.onSelect(id); this.focus(id); } });
    canvas.addEventListener("keydown", (event) => {
      const key = event.key.toLowerCase();
      if (key === "f") this.focus(this.selected); else if (key === "r") this.fit();
      else if (["+", "="].includes(key)) this.zoom(.8); else if (key === "-") this.zoom(1.25); else return;
      event.preventDefault();
    });
    canvas.addEventListener("webglcontextlost", (event) => { event.preventDefault(); this.contextLost = true; this.fallback = true; host.parentElement.dataset.renderMode = "fallback"; this.labels.parentElement.classList.add("scene-fallback"); this.select(this.selected); this.onCamera("Graphics context lost · list view"); host.parentElement.dispatchEvent(new Event("scene-fallback")); });
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(host);
    this.intersection = new IntersectionObserver(([entry]) => { this.inView = entry.isIntersecting; if (this.inView) this.invalidate(); });
    this.intersection.observe(host); this.inView = true; this.resize();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) this.invalidate(); });
  }

  update(resources, connections, selected, visibleIds) {
    this.resources = resources; this.connections = connections; this.selected = selected; this.visibleIds = visibleIds;
    const positions = layoutTopology(resources, connections), ids = new Set(positions.keys());
    for (const [id, model] of this.models) if (!ids.has(id)) { release(model); this.models.delete(id); }
    for (const [id, button] of this.buttons) if (!ids.has(id)) { button.remove(); this.buttons.delete(id); }
    for (const [id, entry] of positions) {
      let button = this.buttons.get(id);
      if (!button) {
        button = document.createElement("button"); button.type = "button"; button.className = "resource-model";
        const label = document.createElement("span"); label.className = "resource-label";
        label.append(document.createElement("b"), document.createElement("span")); button.append(label);
        button.addEventListener("click", () => this.onSelect(id)); button.addEventListener("dblclick", () => this.focus(id));
        this.labels.append(button); this.buttons.set(id, button);
      }
      button.title = `${entry.resource.kind}/${entry.resource.name} · ${entry.resource.status}`;
      button.querySelector("b").textContent = entry.resource.name.length > 17 ? `${entry.resource.name.slice(0, 14)}…` : entry.resource.name;
      button.querySelector(".resource-label>span").textContent = entry.resource.kind;
      button.setAttribute("aria-label", `Inspect ${entry.resource.kind} ${entry.resource.name}, ${entry.resource.status}`);
      if (!this.fallback) {
        let model = this.models.get(id);
        if (!model) { model = createModel(entry.resource); this.models.set(id, model); this.scene.add(model); }
        model.position.set(...entry.position); model.userData.resource = entry.resource;
        const ring = model.userData.ring; ring.material.color.setHex(healthColors[entry.resource.health] || healthColors.warning); ring.material.emissive.copy(ring.material.color);
      }
    }
    const signature = JSON.stringify([...positions].map(([id, entry]) => [id, entry.position])) + JSON.stringify(connections);
    if (!this.fallback && signature !== this.edgeSignature) {
      this.edges.forEach(({group}) => release(group)); this.edges = [];
      connections.forEach((edge) => {
        const start = positions.get(edge.source), end = positions.get(edge.target); if (!start || !end) return;
        const a = new THREE.Vector3(...start.position).add(new THREE.Vector3(0, .3, 0));
        const b = new THREE.Vector3(...end.position).add(new THREE.Vector3(0, .3, 0));
        const middle = a.clone().lerp(b, .5); middle.y += edge.relation === "owns" ? .2 : .85;
        const curve = new THREE.QuadraticBezierCurve3(a, middle, b);
        const group = new THREE.Group();
        const material = new THREE.MeshBasicMaterial({color: linkColors[edge.relation], transparent: true, opacity: .78});
        const line = new THREE.Mesh(new THREE.TubeGeometry(curve, 24, .044, 5, false), material); group.add(line);
        const arrow = new THREE.Mesh(new THREE.ConeGeometry(.12, .32, 6), material.clone());
        arrow.position.copy(curve.getPoint(.76)); arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), curve.getTangent(.76).normalize()); group.add(arrow);
        this.scene.add(group); this.edges.push({group, edge});
      });
      this.edgeSignature = signature;
    }
    this.select(selected);
    if (!this.fitted && resources.length && !this.fallback) { this.fit(false); this.fitted = true; }
    this.invalidate();
  }

  select(id) {
    this.selected = id;
    const related = new Set(this.connections.filter((edge) => edge.source === id || edge.target === id).flatMap((edge) => [edge.source, edge.target]));
    for (const [key, button] of this.buttons) {
      const resource = this.resources.find((item) => resourceId(item) === key);
      button.querySelector("b").textContent = key === id || this.fallback ? resource.name : resource.name.length > 17 ? `${resource.name.slice(0, 14)}…` : resource.name;
      button.querySelector(".resource-label>span").textContent = key === id || this.fallback ? `${resource.kind} · ${resource.status}` : resource.kind;
      button.className = `resource-model ${resource?.health || "healthy"}${key === id ? " selected" : ""}${related.has(key) && key !== id ? " related" : ""}${id && !related.has(key) && key !== id ? " dimmed" : ""}`;
      button.setAttribute("aria-pressed", String(key === id)); button.hidden = !this.visibleIds.has(key);
      const model = this.models.get(key); if (model) { model.visible = this.visibleIds.has(key); model.userData.ring.scale.setScalar(key === id ? 1.15 : 1); }
    }
    for (const {group, edge} of this.edges) {
      group.visible = this.showLinks && this.visibleIds.has(edge.source) && this.visibleIds.has(edge.target);
      group.children.forEach((object) => { object.material.opacity = id ? (edge.source === id || edge.target === id ? 1 : .15) : .78; });
    }
    this.invalidate();
  }

  links(enabled) { this.showLinks = enabled; this.select(this.selected); }
  mode(mode) { if (!this.controls) return; this.renderer.domElement.dataset.mode = mode; this.controls.mouseButtons.LEFT = mode === "pan" ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE; }
  zoom(factor) {
    if (!this.controls) return;
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, this.controls.minDistance, this.controls.maxDistance));
    this.fly(this.controls.target.clone().add(offset), this.controls.target.clone());
  }
  fit(animate = true) {
    if (!this.controls) return;
    const points = [...this.models].filter(([id]) => this.visibleIds.has(id)).map(([, model]) => model.position);
    if (!points.length) return;
    const box = new THREE.Box3().setFromPoints(points).expandByScalar(1.8), center = box.getCenter(new THREE.Vector3()); center.y = .6;
    const size = box.getSize(new THREE.Vector3());
    const distance = Math.min(70, Math.max(size.x / Math.max(this.camera.aspect, .45), size.z, 8) * 1.15 + 4);
    const direction = new THREE.Vector3(.16, 1.25, 1).normalize();
    this.fly(center.clone().addScaledVector(direction, distance), center, animate);
  }
  focus(id) {
    const model = this.models.get(id); if (!model || !this.controls) return;
    const target = model.position.clone().add(new THREE.Vector3(0, .7, 0));
    const offset = this.camera.position.clone().sub(this.controls.target).normalize().multiplyScalar(7.5);
    this.fly(target.clone().add(offset), target);
  }
  fly(position, target, animate = true) {
    if (!animate || reducedMotion) { this.camera.position.copy(position); this.controls.target.copy(target); this.controls.update(); }
    else this.tween = {at: performance.now(), from: this.camera.position.clone(), targetFrom: this.controls.target.clone(), to: position, targetTo: target};
    this.invalidate();
  }
  pick(event) {
    const bounds = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects([...this.models.values()].filter((model) => model.visible), true);
    return hits[0]?.object.userData.resourceId;
  }
  resize() {
    if (!this.renderer || this.contextLost) return;
    const width = this.host.clientWidth, height = this.host.clientHeight; if (!width || !height) return;
    this.renderer.setSize(width, height, false); this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.invalidate();
  }
  invalidate() {
    if (!this.renderer || this.contextLost || this.frame || document.hidden || this.inView === false) return;
    this.frame = requestAnimationFrame(() => this.render());
  }
  render() {
    this.frame = null;
    if (this.tween) {
      const t = Math.min(1, (performance.now() - this.tween.at) / 380), ease = 1 - (1 - t) ** 3;
      this.camera.position.lerpVectors(this.tween.from, this.tween.to, ease); this.controls.target.lerpVectors(this.tween.targetFrom, this.tween.targetTo, ease);
      if (t === 1) this.tween = null;
    }
    const changed = this.controls.update(); this.renderer.render(this.scene, this.camera);
    const width = this.host.clientWidth, height = this.host.clientHeight;
    const occupied = [];
    const ordered = [...this.models].sort(([a], [b]) => Number(b === this.selected) * 100 + Number(b === this.hovered) * 50 - Number(a === this.selected) * 100 - Number(a === this.hovered) * 50);
    for (const [id, model] of ordered) {
      const button = this.buttons.get(id);
      this.projected.copy(model.position).add(new THREE.Vector3(0, -.35, 0)).project(this.camera);
      const x = (this.projected.x + 1) / 2 * width, y = (1 - this.projected.y) / 2 * height;
      const labelWidth = id === this.selected ? 154 : 94, labelHeight = id === this.selected ? 55 : 34;
      const rect = {left: x - labelWidth / 2, right: x + labelWidth / 2, top: y, bottom: y + labelHeight};
      const collides = occupied.some((other) => rect.left < other.right + 3 && rect.right > other.left - 3 && rect.top < other.bottom + 3 && rect.bottom > other.top - 3);
      button.hidden = !this.visibleIds.has(id) || this.projected.z > 1 || this.projected.z < -1 || x < 0 || x > width || y < 0 || y > height - 35 || (collides && id !== this.selected && id !== this.hovered);
      if (!button.hidden) occupied.push(rect);
      button.style.transform = `translate(${x - labelWidth / 2}px, ${y}px)`;
      button.style.zIndex = String(id === this.selected ? 100 : Math.round((1 - this.projected.z) * 40));
    }
    this.onCamera(`${this.camera.position.distanceTo(this.controls.target).toFixed(1)}× distance`);
    if (this.tween || changed) this.invalidate();
  }
}
