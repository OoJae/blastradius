/** The blast field: one instanced point cloud, three shells, one seed.
 *
 * What is real here and what is not, stated plainly because the rest of this
 * project holds itself to it: the COUNTS are real -- every point at depth n
 * corresponds to a package the traversal actually returned -- and so is the
 * saturation curve that drives the ignition. The LAYOUT is not. We do not ship
 * an edge list, so positions are a deterministic spherical distribution seeded
 * per depth, not the graph's true topology. The page says so where it matters.
 */

import * as THREE from 'three'

export type FieldCounts = { depth: number; total: number }[]

const VERT = /* glsl */ `
  attribute float aDepth;      // 0 = seed, 1..3 = shell
  attribute float aSeed;       // per-point randomness, stable across frames
  uniform float uTime;
  uniform float uIgnite;       // 0..3+, how far the wave has travelled
  uniform float uPixelRatio;
  varying float vDepth;
  varying float vHot;

  void main() {
    vDepth = aDepth;

    // A shell lights when the wave front passes it, then cools to a floor --
    // the blast is over in four seconds but the packages stay exposed.
    float lit = smoothstep(aDepth - 0.85, aDepth - 0.05, uIgnite);
    vHot = lit;

    // Points drift outward as they ignite, and breathe almost imperceptibly
    // once settled, so a still scene never looks like a frozen frame.
    vec3 p = position;
    float breathe = sin(uTime * 0.35 + aSeed * 6.2831) * 0.035;
    p *= mix(0.55, 1.0, lit) + breathe * lit;

    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;

    float size = mix(1.7, 3.6, lit) * (aDepth < 0.5 ? 5.0 : 1.0);
    gl_PointSize = size * uPixelRatio * (28.0 / -mv.z);
  }
`

const FRAG = /* glsl */ `
  precision mediump float;
  uniform vec3 uEmber;
  uniform vec3 uEmberGlow;
  uniform vec3 uChalk;
  varying float vDepth;
  varying float vHot;

  void main() {
    // Round points with a soft edge; a square point reads as a bug.
    vec2 c = gl_PointCoord - vec2(0.5);
    float d = dot(c, c);
    if (d > 0.25) discard;
    float alpha = smoothstep(0.25, 0.02, d);

    // Ember only where the wave has actually reached: colour carries the same
    // rule as the rest of the product.
    vec3 cold = uChalk * 0.40;
    vec3 hot = mix(uEmber, uEmberGlow, vDepth < 0.5 ? 1.0 : 0.25);
    vec3 col = mix(cold, hot, vHot);

    gl_FragColor = vec4(col, alpha * mix(0.55, 1.0, vHot));
  }
`

export type Field = {
  mount: (el: HTMLElement) => void
  setIgnite: (v: number) => void
  setSpin: (v: number) => void
  renderOnce: () => void
  start: () => void
  stop: () => void
  dispose: () => void
}

/** Deterministic pseudo-random, so the field is identical on every load and
 *  between server and client. Math.random would make the scene unrepeatable. */
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

/** Whether this browser can actually give us a WebGL context.
 *
 *  Checked before constructing anything, because THREE.WebGLRenderer throws
 *  when it cannot get one -- on a locked-down corporate machine, a VM with no
 *  GPU, or a browser with 3D disabled -- and an uncaught throw inside a React
 *  effect unmounts the tree and leaves the page blank. The landing page has to
 *  survive that: the type carries the argument, the field only illustrates it.
 */
export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas')
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'))
  } catch {
    return false
  }
}

export function createField(counts: FieldCounts, opts: { reducedMotion: boolean }): Field {
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200)
  camera.position.set(0, 0, 34)

  const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true, powerPreference: 'high-performance' })
  renderer.setClearColor(0x000000, 0)

  // Cap the cloud so a big slice cannot cost frames; the visible density is
  // what matters, and the true totals are printed as type beside the scene.
  const CAP = 9000
  const scale = Math.min(1, CAP / Math.max(counts.reduce((n, c) => n + c.total, 0), 1))

  const positions: number[] = []
  const depths: number[] = []
  const seeds: number[] = []

  positions.push(0, 0, 0); depths.push(0); seeds.push(0)

  const RADII = [0, 8.5, 15, 22]
  for (const { depth, total } of counts) {
    const n = Math.max(Math.round(total * scale), 24)
    const rand = rng(depth * 9176 + 17)
    const r0 = RADII[depth] ?? 22
    for (let i = 0; i < n; i++) {
      // Fibonacci-ish spherical scatter with jitter: even coverage without the
      // banding a naive lat/long distribution produces.
      const u = rand() * 2 - 1
      const theta = rand() * Math.PI * 2
      const s = Math.sqrt(1 - u * u)
      const jitter = 1 + (rand() - 0.5) * 0.13
      const r = r0 * jitter
      positions.push(s * Math.cos(theta) * r, s * Math.sin(theta) * r * 0.72, u * r)
      depths.push(depth)
      seeds.push(rand())
    }
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('aDepth', new THREE.Float32BufferAttribute(depths, 1))
  geometry.setAttribute('aSeed', new THREE.Float32BufferAttribute(seeds, 1))

  const uniforms = {
    uTime: { value: 0 },
    uIgnite: { value: 0 },
    uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
    uEmber: { value: new THREE.Color('#FF6B35') },
    uEmberGlow: { value: new THREE.Color('#FF8F5E') },
    uChalk: { value: new THREE.Color('#E8EAED') },
  }

  const material = new THREE.ShaderMaterial({
    uniforms, vertexShader: VERT, fragmentShader: FRAG,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  })

  const points = new THREE.Points(geometry, material)
  // The type owns the left of the screen, so the detonation is composed into
  // the right of it rather than sitting behind the headline. A group wrapper
  // keeps the rotation centred on the seed while the whole thing is offset.
  const group = new THREE.Group()
  group.add(points)
  scene.add(group)

  let host: HTMLElement | null = null
  let raf = 0
  let running = false
  let spin = 0
  const clock = new THREE.Clock()

  const resize = () => {
    if (!host) return
    const { clientWidth: w, clientHeight: h } = host
    if (!w || !h) return
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    uniforms.uPixelRatio.value = Math.min(window.devicePixelRatio, 2)
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    // Pull back on narrow screens so the outer shell stays in frame, and only
    // offset the field to the right where there is room beside the type.
    const narrow = w < 900
    camera.position.z = narrow ? 50 : 36
    group.position.x = narrow ? 0 : 11
    group.position.y = narrow ? 0 : 1
    camera.updateProjectionMatrix()
  }

  const draw = () => {
    uniforms.uTime.value = clock.getElapsedTime()
    points.rotation.y = spin
    points.rotation.x = spin * 0.18
    renderer.render(scene, camera)
  }

  const loop = () => {
    if (!running) return
    draw()
    raf = requestAnimationFrame(loop)
  }

  return {
    mount(el) {
      host = el
      el.appendChild(renderer.domElement)
      renderer.domElement.style.cssText = 'width:100%;height:100%;display:block'
      resize()
      window.addEventListener('resize', resize)
      draw()
    },
    // When the loop is not running -- reduced motion, or off-screen -- the
    // scene still has to reflect the scroll position, so a set repaints once.
    // Without this the field freezes at zero for anyone who asked for less
    // motion, which is a worse experience than the animation they opted out of.
    setIgnite(v) { uniforms.uIgnite.value = v; if (!running) draw() },
    setSpin(v) { spin = v; if (!running) draw() },
    renderOnce() { draw() },
    start() { if (opts.reducedMotion || running) return; running = true; clock.start(); loop() },
    stop() { running = false; cancelAnimationFrame(raf) },
    dispose() {
      running = false
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      geometry.dispose(); material.dispose(); renderer.dispose()
      renderer.domElement.remove()
    },
  }
}
