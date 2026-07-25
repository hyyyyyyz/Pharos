# Pharos cinematic landing roadmap

The landing page should feel like entering a research coastline at night, not
like opening a generic SaaS template with a 3D object attached to it. The
lighthouse is the navigation system: its beam discovers signals, turns into the
translation interaction, and then guides the visitor into the product workflow.

## Experience principles

1. **One visual metaphor.** Ocean, lighthouse, beam, paper, and research signals
   all express the same product story. Decorative particles that do not support
   that story do not ship.
2. **Product proof follows spectacle.** The cinematic first screen earns
   attention; the interactive translation reader immediately proves what Pharos
   actually does.
3. **HTML remains the product surface.** The canvas is decorative enhancement.
   Headlines, navigation, calls to action, and feature explanations remain real,
   accessible HTML.
4. **Motion has a budget.** Camera movement is slow and intentional. No rapid
   orbiting, scroll hijacking, flashing, or interaction that fights reading.
5. **Every quality tier is designed.** A low-power device receives a composed
   scene rather than a broken version of the desktop effect.

## Phase 1 — cinematic scene foundation (implemented)

- Independent Vite build for `site/`, with Three.js pinned in `package-lock.json`.
- Dynamically loaded scene chunk; failure never blocks the existing page.
- Procedural lighthouse, rocky island, animated water shader, sea mist, stars,
  moon lighting, moving volumetric beam, lamp bloom, and water reflection.
- Pointer-driven parallax and a damped scroll camera path.
- Three research signals that illuminate when the beam reaches them:
  discovery, close reading, and building research.
- CSS lighthouse fallback, reduced-motion fallback, data-saver fallback, WebGL
  context-loss fallback, visibility pausing, bounded DPR, and mobile quality
  tiers.
- The original translation interaction moved into a dedicated second act so it
  is no longer competing with the brand scene.

## Phase 2 — photoreal lighthouse asset pipeline

The procedural lighthouse is the scene-system reference model. The next major
visual leap should replace it with an owned PBR glTF asset without changing the
camera, beam, water, or lifecycle architecture.

### Asset target

- A recognisable but original coastal lighthouse, not a copied landmark.
- 80k–140k visible triangles for desktop LOD0.
- 25k–45k triangles for mobile LOD1.
- Separate materials for painted masonry, wet stone, oxidised metal, glass, and
  the Fresnel lens.
- 2K texture set on desktop; 1K texture set on mobile.
- Albedo, normal, roughness, ambient-occlusion, and selective emissive maps.
- Real architectural details: gallery brackets, railings, roof seams, door
  hardware, masonry wear, salt staining, and glass framing.

### Delivery pipeline

1. Model and UV in Blender.
2. Export glTF/GLB with clean material names and two LODs.
3. Apply Meshopt geometry compression.
4. Convert textures to KTX2/Basis Universal.
5. Preload the low LOD; stream the high LOD after the first meaningful frame.
6. Keep the current procedural lighthouse as the no-asset and test fallback.

No third-party model should enter the repository without a documented licence
and attribution decision.

## Phase 3 — scroll-directed research voyage

Turn the landing page into four restrained camera chapters. Scrolling controls a
target progress value; rendering uses damped interpolation and never takes over
the browser's native scroll.

| Chapter | Camera | Scene event | Product hand-off |
| --- | --- | --- | --- |
| 01 · Horizon | Low over the water | Lighthouse acquires the visitor | Product promise and CTA |
| 02 · Signal | Approaches the island | Beam finds floating paper signals | Literature discovery |
| 03 · Lens | Rises toward the lantern room | English markings resolve into Chinese | Translation demo |
| 04 · Chart | Pulls into an aerial coastline | Signals connect into one route | End-to-end research workflow |

The current one-screen camera path is intentionally compatible with this future
chapter controller.

## Phase 4 — richer environmental storytelling

- **Paper flotilla:** distant pages drift like navigation charts. The beam reveals
  only a title and one Chinese core trick, matching the real discovery UI.
- **Research constellation:** completed projects become dim coastal lights. Their
  links form a sparse knowledge graph only when the beam crosses them.
- **Weather as state, not decoration:** clear horizon for discovery, denser fog
  for uncertainty, and a warmer dawn after a project reaches a validated claim.
- **Architecture lens:** clicking the lantern lens can open an exploded diagram
  of Browser → FastAPI Core → Engine Worker, reusing the project architecture.
- **Optional ambience:** a muted-by-default wave and wind layer, activated only
  by an explicit control. No autoplay audio.

## Phase 5 — brand outputs from the same scene

- Deterministic camera presets for README GIF/video capture.
- Open Graph poster rendered from the real 3D scene.
- Release-specific scene states for major launches.
- A lightweight WebGPU enhancement tier for high-end devices, while WebGL stays
  the dependable default.

## Performance budgets

| Budget | Desktop high | Mobile medium | Mobile low |
| --- | ---: | ---: | ---: |
| Internal render pixels | ≤ 2.0M | ≤ 1.2M | ≤ 0.72M |
| Initial non-3D JS, gzip | ≤ 15 KB | ≤ 15 KB | ≤ 15 KB |
| Deferred 3D JS, gzip | ≤ 180 KB | ≤ 180 KB | ≤ 180 KB |
| Scene textures after Phase 2 | ≤ 4 MB | ≤ 2 MB | ≤ 1 MB |
| Target frame rate | 60 fps | 45–60 fps | 30–45 fps |

The current deferred lighthouse bundle is roughly 148 KB gzip and therefore
fits the Phase 1 JavaScript budget.

## Acceptance checklist for every visual phase

- Desktop, tablet, and narrow mobile screenshots reviewed at real breakpoints.
- Keyboard navigation and visible focus remain intact.
- `prefers-reduced-motion` shows a complete static composition.
- Data Saver and WebGL failure retain all content and calls to action.
- Rendering stops outside the viewport and while the tab is hidden.
- No console errors, accidental horizontal scroll, layout shift, or opaque canvas
  during loading.
- Production build, relative GitHub Pages paths, and third-party notices verified.
