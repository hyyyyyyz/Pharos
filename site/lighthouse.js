import * as THREE from "three";

const SIGNAL_NAMES = ["问题", "证据", "推进"];
const STORY_BEATS = [
  { key: "horizon", from: 0, to: 0.16, signal: -1 },
  { key: "discover", from: 0.16, to: 0.4, signal: 0 },
  { key: "read", from: 0.4, to: 0.64, signal: 1 },
  { key: "build", from: 0.64, to: 0.84, signal: 2 },
  { key: "handoff", from: 0.84, to: 1.01, signal: -1 },
];
const BEAM_COLOR = new THREE.Color(0xffd978);
const LIGHTHOUSE_POSITION = new THREE.Vector3(10, 0, -4);

const clamp = THREE.MathUtils.clamp;

function mulberry32(seed) {
  return () => {
    let value = (seed += 0x6d2b79f5);
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function selectQuality() {
  const memory = navigator.deviceMemory ?? 8;
  const cores = navigator.hardwareConcurrency ?? 8;
  const viewportPixels = window.innerWidth * window.innerHeight;

  if (window.innerWidth < 620 || memory <= 2 || cores <= 2 || viewportPixels > 3_600_000) {
    return "low";
  }
  if (window.innerWidth < 1080 || memory <= 4 || cores <= 4) return "medium";
  return "high";
}

function makeGlowTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext("2d");
  const gradient = context.createRadialGradient(128, 128, 0, 128, 128, 128);
  gradient.addColorStop(0, "rgba(255, 246, 205, 1)");
  gradient.addColorStop(0.13, "rgba(255, 218, 119, 0.88)");
  gradient.addColorStop(0.4, "rgba(248, 192, 64, 0.24)");
  gradient.addColorStop(1, "rgba(248, 192, 64, 0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 256, 256);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function makeMistTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 128;
  const context = canvas.getContext("2d");
  const gradient = context.createRadialGradient(128, 64, 4, 128, 64, 124);
  gradient.addColorStop(0, "rgba(214, 231, 229, 0.6)");
  gradient.addColorStop(0.38, "rgba(151, 191, 196, 0.2)");
  gradient.addColorStop(1, "rgba(74, 132, 145, 0)");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 256, 128);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function makeTowerTexture(renderer, quality) {
  const size = quality === "low" ? 256 : quality === "medium" ? 384 : 512;
  const speckleCount = quality === "low" ? 2500 : quality === "medium" ? 6000 : 11000;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  const random = mulberry32(9071);

  context.fillStyle = "#d8d5c9";
  context.fillRect(0, 0, size, size);

  for (let i = 0; i < speckleCount; i += 1) {
    const shade = 188 + Math.floor(random() * 45);
    context.fillStyle = `rgba(${shade}, ${shade}, ${shade - 7}, ${0.025 + random() * 0.04})`;
    const radius = 0.3 + random() * 1.25;
    context.fillRect(random() * size, random() * size, radius, radius);
  }

  context.lineWidth = 1;
  const courseHeight = size / 15;
  const blockWidth = size / 6.75;
  for (let y = courseHeight * 0.7; y < size; y += courseHeight) {
    context.strokeStyle = "rgba(66, 75, 77, 0.11)";
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(size, y + random() * 2 - 1);
    context.stroke();

    const offset = Math.floor(y / courseHeight) % 2 === 0 ? 0 : blockWidth / 2;
    for (let x = offset; x < size; x += blockWidth) {
      context.strokeStyle = "rgba(70, 77, 77, 0.07)";
      context.beginPath();
      context.moveTo(x, y - courseHeight);
      context.lineTo(x, y);
      context.stroke();
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(2.2, 5.4);
  texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  return texture;
}

function createSky(scene, quality, textures, random) {
  const skyGeometry = new THREE.SphereGeometry(280, 36, 20);
  const skyMaterial = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      topColor: { value: new THREE.Color(0x010812) },
      horizonColor: { value: new THREE.Color(0x123348) },
      glowColor: { value: new THREE.Color(0x2d6170) },
    },
    vertexShader: `
      varying vec3 vWorldPosition;
      void main() {
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vWorldPosition = worldPosition.xyz;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 topColor;
      uniform vec3 horizonColor;
      uniform vec3 glowColor;
      varying vec3 vWorldPosition;
      void main() {
        vec3 direction = normalize(vWorldPosition - cameraPosition);
        float heightMix = smoothstep(-0.16, 0.76, direction.y);
        float horizon = exp(-abs(direction.y + 0.055) * 11.0);
        float farGlow = pow(max(dot(direction, normalize(vec3(0.42, 0.12, -0.9))), 0.0), 14.0);
        vec3 color = mix(horizonColor, topColor, heightMix);
        color += glowColor * horizon * 0.12;
        color += glowColor * farGlow * 0.08;
        gl_FragColor = vec4(color, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });
  scene.add(new THREE.Mesh(skyGeometry, skyMaterial));

  const starCount = quality === "high" ? 950 : quality === "medium" ? 540 : 260;
  const starPositions = new Float32Array(starCount * 3);
  const starColors = new Float32Array(starCount * 3);
  const cool = new THREE.Color(0xb8d1d4);
  const warm = new THREE.Color(0xffe7ad);

  for (let index = 0; index < starCount; index += 1) {
    const angle = random() * Math.PI * 2;
    const radius = 125 + random() * 115;
    const height = 24 + Math.pow(random(), 0.72) * 150;
    const offset = index * 3;
    starPositions[offset] = Math.cos(angle) * radius;
    starPositions[offset + 1] = height;
    starPositions[offset + 2] = Math.sin(angle) * radius - 35;

    const color = random() > 0.87 ? warm : cool;
    const intensity = 0.46 + random() * 0.54;
    starColors[offset] = color.r * intensity;
    starColors[offset + 1] = color.g * intensity;
    starColors[offset + 2] = color.b * intensity;
  }

  const starGeometry = new THREE.BufferGeometry();
  starGeometry.setAttribute("position", new THREE.BufferAttribute(starPositions, 3));
  starGeometry.setAttribute("color", new THREE.BufferAttribute(starColors, 3));
  const stars = new THREE.Points(
    starGeometry,
    new THREE.PointsMaterial({
      size: quality === "high" ? 0.48 : 0.62,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.72,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  );
  scene.add(stars);

  const moon = new THREE.Mesh(
    new THREE.SphereGeometry(3.8, 36, 24),
    new THREE.MeshBasicMaterial({ color: 0xd9e3df }),
  );
  moon.position.set(46, 66, -108);
  scene.add(moon);

  const moonHalo = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: textures.glow,
      color: 0x9bc1c5,
      transparent: true,
      opacity: 0.17,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  );
  moonHalo.position.copy(moon.position);
  moonHalo.scale.set(29, 29, 1);
  scene.add(moonHalo);

  const mistLayers = [];
  const mistCount = quality === "high" ? 18 : quality === "medium" ? 11 : 6;
  for (let index = 0; index < mistCount; index += 1) {
    const material = new THREE.SpriteMaterial({
      map: textures.mist,
      color: index % 3 === 0 ? 0x7aaeb4 : 0xa6c3c1,
      transparent: true,
      opacity: 0.025 + random() * 0.032,
      depthWrite: false,
    });
    const sprite = new THREE.Sprite(material);
    sprite.position.set(
      -100 + random() * 220,
      3 + random() * 21,
      -125 + random() * 185,
    );
    const scale = 35 + random() * 70;
    sprite.scale.set(scale, scale * (0.28 + random() * 0.18), 1);
    sprite.userData.speed = 0.08 + random() * 0.12;
    sprite.userData.originX = sprite.position.x;
    mistLayers.push(sprite);
    scene.add(sprite);
  }

  return { stars, moonHalo, mistLayers };
}

function createWater(scene, quality) {
  const segments = quality === "high" ? 150 : quality === "medium" ? 92 : 54;
  const geometry = new THREE.PlaneGeometry(520, 520, segments, segments);
  const material = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 },
      deepColor: { value: new THREE.Color(0x03101a) },
      surfaceColor: { value: new THREE.Color(0x0c3448) },
      horizonColor: { value: new THREE.Color(0x2e6370) },
      fogColor: { value: new THREE.Color(0x071722) },
      beamColor: { value: BEAM_COLOR.clone() },
      beamOrigin: { value: new THREE.Vector3() },
      beamDirection: { value: new THREE.Vector3(1, -0.04, 0) },
      beamIntensity: { value: 1 },
    },
    vertexShader: `
      uniform float time;
      varying vec3 vWorldPosition;
      varying vec3 vWorldNormal;
      varying float vCrest;

      void main() {
        vec3 transformed = position;
        float waveA = sin(position.x * 0.055 + time * 0.62) * 0.48;
        float waveB = sin(position.y * 0.078 - time * 0.46 + position.x * 0.019) * 0.31;
        float waveC = sin((position.x + position.y) * 0.032 + time * 0.31) * 0.23;
        float wave = waveA + waveB + waveC;
        transformed.z += wave;

        float derivativeX = cos(position.x * 0.055 + time * 0.62) * 0.0264
          + cos(position.y * 0.078 - time * 0.46 + position.x * 0.019) * 0.0059
          + cos((position.x + position.y) * 0.032 + time * 0.31) * 0.0074;
        float derivativeY = cos(position.y * 0.078 - time * 0.46 + position.x * 0.019) * 0.0242
          + cos((position.x + position.y) * 0.032 + time * 0.31) * 0.0074;

        vec3 localNormal = normalize(vec3(-derivativeX, -derivativeY, 1.0));
        vec4 worldPosition = modelMatrix * vec4(transformed, 1.0);
        vWorldPosition = worldPosition.xyz;
        vWorldNormal = normalize(mat3(modelMatrix) * localNormal);
        vCrest = wave;
        gl_Position = projectionMatrix * viewMatrix * worldPosition;
      }
    `,
    fragmentShader: `
      uniform float time;
      uniform vec3 deepColor;
      uniform vec3 surfaceColor;
      uniform vec3 horizonColor;
      uniform vec3 fogColor;
      uniform vec3 beamColor;
      uniform vec3 beamOrigin;
      uniform vec3 beamDirection;
      uniform float beamIntensity;
      varying vec3 vWorldPosition;
      varying vec3 vWorldNormal;
      varying float vCrest;

      void main() {
        vec3 normal = normalize(vWorldNormal);
        vec3 viewDirection = normalize(cameraPosition - vWorldPosition);
        float fresnel = pow(1.0 - max(dot(normal, viewDirection), 0.0), 3.4);
        float facing = max(dot(normal, viewDirection), 0.0);

        vec3 moonDirection = normalize(vec3(-0.35, 0.76, 0.42));
        vec3 reflectedMoon = reflect(-moonDirection, normal);
        float moonSpecular = pow(max(dot(reflectedMoon, viewDirection), 0.0), 90.0);

        vec2 beamVector = vWorldPosition.xz - beamOrigin.xz;
        float beamDistance = max(length(beamVector), 0.001);
        vec2 beamRay = beamVector / beamDistance;
        vec2 beamHeading = normalize(beamDirection.xz);
        float alignment = max(dot(beamRay, beamHeading), 0.0);
        float beamCone = pow(alignment, 190.0);
        float beamReach = 1.0 - smoothstep(18.0, 118.0, beamDistance);
        float beamRipple = 0.82 + 0.18 * sin(beamDistance * 0.28 - time * 1.8 + vCrest * 3.0);
        // A nearly horizontal lighthouse beam does not directly illuminate the
        // foreground water. Keep only a faint, distant grazing reflection once
        // the expanding cone approaches the sea surface.
        float surfaceContact = smoothstep(78.0, 108.0, beamDistance);
        float beamLight = beamCone * beamReach * beamRipple * beamIntensity * surfaceContact;

        float crest = smoothstep(0.52, 0.96, vCrest) * (0.15 + fresnel * 0.35);
        vec3 color = mix(deepColor, surfaceColor, 0.22 + fresnel * 0.78);
        color = mix(color, horizonColor, fresnel * 0.24);
        color += vec3(0.47, 0.65, 0.68) * moonSpecular * 1.9;
        color += vec3(0.34, 0.48, 0.5) * crest;
        color += beamColor * beamLight * (0.16 + fresnel * 0.18);
        color += surfaceColor * facing * 0.06;

        float distanceToCamera = length(cameraPosition - vWorldPosition);
        float fogFactor = 1.0 - exp(-distanceToCamera * 0.0115);
        color = mix(color, fogColor, clamp(fogFactor, 0.0, 0.91));

        gl_FragColor = vec4(color, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });

  const water = new THREE.Mesh(geometry, material);
  water.rotation.x = -Math.PI / 2;
  water.position.y = 0;
  water.receiveShadow = quality === "high";
  scene.add(water);
  return { water, material };
}

function deformRockGeometry(radius, detail, seed) {
  const geometry = new THREE.IcosahedronGeometry(radius, detail);
  const positions = geometry.attributes.position;
  const vector = new THREE.Vector3();

  for (let index = 0; index < positions.count; index += 1) {
    vector.fromBufferAttribute(positions, index);
    const noise =
      Math.sin(vector.x * 0.73 + seed) * 0.08
      + Math.cos(vector.y * 1.17 - seed * 0.4) * 0.07
      + Math.sin(vector.z * 0.91 + vector.x * 0.31 + seed * 1.7) * 0.09;
    vector.multiplyScalar(1 + noise);
    positions.setXYZ(index, vector.x, vector.y, vector.z);
  }
  geometry.computeVertexNormals();
  return geometry;
}

function createIsland(scene, quality, random) {
  const group = new THREE.Group();
  group.position.copy(LIGHTHOUSE_POSITION);

  const rockMaterial = new THREE.MeshStandardMaterial({
    color: 0x253239,
    roughness: 0.94,
    metalness: 0.02,
  });
  const wetRockMaterial = new THREE.MeshStandardMaterial({
    color: 0x101d23,
    roughness: 0.68,
    metalness: 0.06,
  });

  const island = new THREE.Mesh(deformRockGeometry(8.5, quality === "low" ? 1 : 2, 3.4), rockMaterial);
  island.scale.set(1.72, 0.53, 1.16);
  island.position.y = 1.95;
  island.rotation.set(-0.08, -0.23, 0.04);
  island.castShadow = quality === "high";
  island.receiveShadow = true;
  group.add(island);

  const rockCount = quality === "high" ? 19 : quality === "medium" ? 13 : 8;
  for (let index = 0; index < rockCount; index += 1) {
    const angle = (index / rockCount) * Math.PI * 2 + random() * 0.34;
    const distance = 7.2 + random() * 6.5;
    const radius = 1.1 + random() * 2.6;
    const rock = new THREE.Mesh(
      deformRockGeometry(radius, quality === "high" && index < 7 ? 2 : 1, 8 + index * 0.71),
      index % 3 === 0 ? wetRockMaterial : rockMaterial,
    );
    rock.position.set(
      Math.cos(angle) * distance,
      0.45 + random() * 1.35,
      Math.sin(angle) * distance * 0.76,
    );
    rock.scale.set(0.8 + random() * 0.7, 0.44 + random() * 0.62, 0.7 + random() * 0.7);
    rock.rotation.set(random() * 0.5, random() * Math.PI, random() * 0.35);
    rock.castShadow = quality === "high";
    rock.receiveShadow = true;
    group.add(rock);
  }

  const foamMaterial = new THREE.MeshBasicMaterial({
    color: 0x9cc9c8,
    transparent: true,
    opacity: 0.13,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const foamRings = [];
  [13.5, 16].forEach((radius, index) => {
    const ring = new THREE.Mesh(new THREE.RingGeometry(radius, radius + 0.16, 96), foamMaterial.clone());
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.17 + index * 0.035;
    ring.scale.y = 0.72;
    group.add(ring);
    foamRings.push(ring);
  });

  scene.add(group);
  return { group, foamRings };
}

function addFacadePanel(group, material, frameMaterial, y, angle, width, height, radius) {
  const frame = new THREE.Mesh(
    new THREE.BoxGeometry(width + 0.28, height + 0.28, 0.24),
    frameMaterial,
  );
  frame.position.set(Math.sin(angle) * radius, y, Math.cos(angle) * radius);
  frame.rotation.y = angle;
  group.add(frame);

  const panel = new THREE.Mesh(new THREE.BoxGeometry(width, height, 0.16), material);
  panel.position.set(
    Math.sin(angle) * (radius + 0.16),
    y,
    Math.cos(angle) * (radius + 0.16),
  );
  panel.rotation.y = angle;
  group.add(panel);
  return panel;
}

function createBeamMaterial(opacity, intensity) {
  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    uniforms: {
      time: { value: 0 },
      color: { value: BEAM_COLOR.clone() },
      opacity: { value: opacity },
      intensity: { value: intensity },
    },
    vertexShader: `
      varying vec2 vUv;
      varying vec3 vWorldPosition;
      void main() {
        vUv = uv;
        vec4 worldPosition = modelMatrix * vec4(position, 1.0);
        vWorldPosition = worldPosition.xyz;
        gl_Position = projectionMatrix * viewMatrix * worldPosition;
      }
    `,
    fragmentShader: `
      uniform float time;
      uniform vec3 color;
      uniform float opacity;
      uniform float intensity;
      varying vec2 vUv;
      varying vec3 vWorldPosition;
      void main() {
        float nearFade = smoothstep(0.0, 0.075, vUv.x);
        float farFade = 1.0 - smoothstep(0.52, 1.0, vUv.x);
        float pulse = 0.92 + 0.08 * sin(time * 1.1 + vWorldPosition.x * 0.08 + vWorldPosition.z * 0.06);
        float radial = pow(max(sin(vUv.y * 3.14159265), 0.0), 1.65);
        float dust = 0.92 + 0.08 * sin(vUv.x * 37.0 + time * 0.7);
        float alpha = opacity * nearFade * farFade * pulse * radial * dust;
        gl_FragColor = vec4(color * intensity, alpha);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });
  material.userData.baseOpacity = opacity;
  material.userData.baseIntensity = intensity;
  return material;
}

function createBeamPlaneGeometry(length, farRadius, nearRadius = 0.18) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(
      [
        0, -nearRadius, 0,
        length, -farRadius, 0,
        length, farRadius, 0,
        0, nearRadius, 0,
      ],
      3,
    ),
  );
  geometry.setAttribute(
    "uv",
    new THREE.Float32BufferAttribute(
      [
        0, 0,
        1, 0,
        1, 1,
        0, 1,
      ],
      2,
    ),
  );
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.computeVertexNormals();
  return geometry;
}

function addVolumetricBeam(rig, geometry, material, planeCount) {
  const planes = [];
  for (let index = 0; index < planeCount; index += 1) {
    const plane = new THREE.Mesh(geometry, material);
    plane.rotation.x = (index / planeCount) * Math.PI;
    plane.renderOrder = 3;
    rig.add(plane);
    planes.push(plane);
  }
  return planes;
}

function createLighthouse(scene, quality, renderer, textures) {
  const group = new THREE.Group();
  group.position.copy(LIGHTHOUSE_POSITION);

  const towerMaterial = new THREE.MeshStandardMaterial({
    map: textures.tower,
    color: 0xf0eee5,
    roughness: 0.82,
    metalness: 0.01,
  });
  const stoneMaterial = new THREE.MeshStandardMaterial({
    color: 0x667176,
    roughness: 0.92,
    metalness: 0.02,
  });
  const metalMaterial = new THREE.MeshStandardMaterial({
    color: 0x13232d,
    roughness: 0.45,
    metalness: 0.68,
  });
  const windowMaterial = quality === "low"
    ? new THREE.MeshBasicMaterial({ color: 0xffcb5f, toneMapped: false })
    : new THREE.MeshStandardMaterial({
        color: 0xffd773,
        emissive: 0xffbd3b,
        emissiveIntensity: 4.8,
        roughness: 0.25,
        metalness: 0.02,
      });

  const foundation = new THREE.Mesh(new THREE.CylinderGeometry(6.35, 6.7, 2.4, 48), stoneMaterial);
  foundation.position.y = 4.4;
  foundation.castShadow = quality === "high";
  foundation.receiveShadow = true;
  group.add(foundation);

  const tower = new THREE.Mesh(
    new THREE.CylinderGeometry(3.18, 5.12, 24.4, quality === "low" ? 32 : 64, 12),
    towerMaterial,
  );
  tower.position.y = 17.7;
  tower.castShadow = quality === "high";
  tower.receiveShadow = true;
  group.add(tower);

  const seamMaterial = new THREE.MeshStandardMaterial({
    color: 0x7d8582,
    roughness: 0.86,
    metalness: 0,
    transparent: true,
    opacity: 0.36,
  });
  for (let index = 0; index < 9; index += 1) {
    const y = 7.2 + index * 2.55;
    const ratio = (y - 5.5) / 24.4;
    const radius = 5.12 - ratio * 1.94 + 0.02;
    const seam = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.035, 5, 64), seamMaterial);
    seam.rotation.x = Math.PI / 2;
    seam.position.y = y;
    group.add(seam);
  }

  addFacadePanel(group, metalMaterial, metalMaterial, 6.35, 0, 2.15, 3.15, 5.25);
  const handle = new THREE.Mesh(new THREE.SphereGeometry(0.09, 12, 8), windowMaterial);
  handle.position.set(0.65, 6.25, 5.44);
  group.add(handle);

  [12.2, 18.9, 25.2].forEach((y, index) => {
    const ratio = (y - 5.5) / 24.4;
    const radius = 5.12 - ratio * 1.94;
    addFacadePanel(group, windowMaterial, metalMaterial, y, index === 1 ? -0.08 : 0.08, 1.05, 1.65, radius);
  });

  const balconySupport = new THREE.Mesh(new THREE.CylinderGeometry(3.62, 3.95, 1.2, 48), metalMaterial);
  balconySupport.position.y = 29.95;
  balconySupport.castShadow = quality === "high";
  group.add(balconySupport);

  const balcony = new THREE.Mesh(new THREE.CylinderGeometry(4.55, 4.55, 0.42, 64), stoneMaterial);
  balcony.position.y = 30.7;
  balcony.castShadow = quality === "high";
  group.add(balcony);

  const postGeometry = new THREE.CylinderGeometry(0.055, 0.065, 1.55, 7);
  const railPosts = new THREE.InstancedMesh(postGeometry, metalMaterial, 24);
  const matrix = new THREE.Matrix4();
  for (let index = 0; index < 24; index += 1) {
    const angle = (index / 24) * Math.PI * 2;
    matrix.makeTranslation(Math.cos(angle) * 4.22, 31.62, Math.sin(angle) * 4.22);
    railPosts.setMatrixAt(index, matrix);
  }
  railPosts.instanceMatrix.needsUpdate = true;
  group.add(railPosts);

  [31.02, 32.3].forEach((y) => {
    const rail = new THREE.Mesh(new THREE.TorusGeometry(4.22, 0.065, 7, 72), metalMaterial);
    rail.rotation.x = Math.PI / 2;
    rail.position.y = y;
    group.add(rail);
  });

  const lanternBase = new THREE.Mesh(new THREE.CylinderGeometry(3.2, 3.38, 0.55, 48), metalMaterial);
  lanternBase.position.y = 31.7;
  group.add(lanternBase);

  const glassMaterial = new THREE.MeshPhysicalMaterial({
    color: 0xaac8c8,
    roughness: 0.08,
    metalness: 0,
    transmission: quality === "low" ? 0 : 0.42,
    transparent: true,
    opacity: quality === "low" ? 0.2 : 0.28,
    depthWrite: false,
  });
  const glass = new THREE.Mesh(new THREE.CylinderGeometry(2.78, 2.78, 3.65, 32, 1, true), glassMaterial);
  glass.position.y = 33.82;
  group.add(glass);

  const frameGeometry = new THREE.CylinderGeometry(0.045, 0.055, 3.7, 6);
  const frames = new THREE.InstancedMesh(frameGeometry, metalMaterial, 12);
  for (let index = 0; index < 12; index += 1) {
    const angle = (index / 12) * Math.PI * 2;
    matrix.makeTranslation(Math.cos(angle) * 2.76, 33.82, Math.sin(angle) * 2.76);
    frames.setMatrixAt(index, matrix);
  }
  frames.instanceMatrix.needsUpdate = true;
  group.add(frames);

  const roofLip = new THREE.Mesh(new THREE.CylinderGeometry(3.55, 3.35, 0.36, 48), metalMaterial);
  roofLip.position.y = 35.83;
  group.add(roofLip);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(4.05, 2.45, 48), metalMaterial);
  roof.position.y = 37.15;
  roof.castShadow = quality === "high";
  group.add(roof);
  const roofCap = new THREE.Mesh(new THREE.SphereGeometry(0.18, 16, 10), metalMaterial);
  roofCap.position.y = 38.52;
  group.add(roofCap);

  const lensMaterial = quality === "low"
    ? new THREE.MeshBasicMaterial({ color: 0xffd76f, toneMapped: false })
    : new THREE.MeshStandardMaterial({
        color: 0xffdc77,
        emissive: 0xffbd42,
        emissiveIntensity: quality === "high" ? 10 : 7,
        roughness: 0.18,
        metalness: 0.08,
      });
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 0.9, 2.3, 32), lensMaterial);
  lens.position.y = 33.85;
  group.add(lens);
  [-0.72, -0.22, 0.28, 0.78].forEach((offset) => {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.92, 0.055, 8, 36), lensMaterial);
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 33.85 + offset;
    group.add(ring);
  });

  const lampGlow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: textures.glow,
      color: 0xffd064,
      transparent: true,
      opacity: quality === "low" ? 0.56 : 0.72,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  );
  lampGlow.position.y = 33.85;
  lampGlow.scale.set(10, 10, 1);
  group.add(lampGlow);

  const lampLight = new THREE.PointLight(0xffcf66, quality === "high" ? 55 : 38, 72, 2);
  lampLight.position.y = 33.85;
  group.add(lampLight);

  const beamRig = new THREE.Group();
  beamRig.position.y = 33.85;
  group.add(beamRig);

  const beamMaterials = [];
  const beamPlanes = [];
  const outerMaterial = createBeamMaterial(quality === "low" ? 0.046 : 0.062, quality === "high" ? 3.1 : 2.6);
  const outerGeometry = createBeamPlaneGeometry(98, 15.5, 0.22);
  beamPlanes.push(...addVolumetricBeam(beamRig, outerGeometry, outerMaterial, 1));
  beamMaterials.push(outerMaterial);

  if (quality !== "low") {
    const coreMaterial = createBeamMaterial(0.07, quality === "high" ? 3.8 : 3.1);
    const coreGeometry = createBeamPlaneGeometry(78, 6.4, 0.14);
    beamPlanes.push(...addVolumetricBeam(beamRig, coreGeometry, coreMaterial, 1));
    beamMaterials.push(coreMaterial);
  }

  const spotTarget = new THREE.Object3D();
  scene.add(spotTarget);
  const spot = new THREE.SpotLight(0xffd779, quality === "high" ? 190 : 120, 112, 0.22, 0.82, 2);
  spot.target = spotTarget;
  beamRig.add(spot);

  scene.add(group);
  return {
    group,
    beamRig,
    beamMaterials,
    beamPlanes,
    spot,
    spotTarget,
    lampGlow,
    lensMaterial,
    windowMaterial,
  };
}

function createSignal(scene, position, index, quality, textures) {
  const group = new THREE.Group();
  group.position.copy(position);

  const darkMaterial = new THREE.MeshStandardMaterial({
    color: 0x20333c,
    roughness: 0.7,
    metalness: 0.38,
  });
  const lightMaterial = new THREE.MeshStandardMaterial({
    color: 0x4db8b2,
    emissive: 0x2b9998,
    emissiveIntensity: 0.7,
    roughness: 0.35,
  });
  const paperMaterial = new THREE.MeshStandardMaterial({
    color: 0xe9e2d2,
    roughness: 0.8,
    metalness: 0,
    side: THREE.DoubleSide,
  });

  const float = new THREE.Mesh(new THREE.CylinderGeometry(0.62, 0.78, 0.85, 12), darkMaterial);
  float.position.y = 0.6;
  group.add(float);

  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.11, 2.2, 8), darkMaterial);
  mast.position.y = 1.92;
  group.add(mast);

  const light = new THREE.Mesh(new THREE.SphereGeometry(0.25, 16, 10), lightMaterial);
  light.position.y = 3.05;
  group.add(light);

  const glow = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: textures.glow,
      color: 0x4db8b2,
      transparent: true,
      opacity: 0.2,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  );
  glow.position.y = 3.05;
  glow.scale.set(3.6, 3.6, 1);
  group.add(glow);

  if (quality !== "low") {
    const page = new THREE.Mesh(new THREE.BoxGeometry(3.1, 0.08, 4.1), paperMaterial);
    page.position.set(index % 2 === 0 ? 2.2 : -2.2, 0.48, index === 1 ? 0.8 : -0.4);
    page.rotation.set(0.08, -0.35 + index * 0.31, 0.03);
    group.add(page);

    for (let line = 0; line < 4; line += 1) {
      const ink = new THREE.Mesh(
        new THREE.BoxGeometry(1.9 - line * 0.18, 0.012, 0.05),
        darkMaterial,
      );
      ink.position.set(page.position.x, page.position.y + 0.055, page.position.z - 1.1 + line * 0.55);
      ink.rotation.copy(page.rotation);
      group.add(ink);
    }
  }

  group.userData.index = index;
  group.userData.baseY = 0.14 + index * 0.035;
  group.userData.lightMaterial = lightMaterial;
  group.userData.glow = glow;
  group.userData.activation = 0;
  scene.add(group);
  return group;
}

function disposeMaterial(material) {
  Object.values(material).forEach((value) => {
    if (value?.isTexture) value.dispose();
  });
  material.dispose();
}

function disposeScene(scene) {
  const geometries = new Set();
  const materials = new Set();
  scene.traverse((object) => {
    if (object.geometry) geometries.add(object.geometry);
    if (Array.isArray(object.material)) object.material.forEach((material) => materials.add(material));
    else if (object.material) materials.add(object.material);
  });
  geometries.forEach((geometry) => geometry.dispose());
  materials.forEach(disposeMaterial);
}

export async function initLighthouseScene({ root, canvas }) {
  if (!root || !canvas) return null;

  const quality = selectQuality();
  const random = mulberry32(20260725);
  const useBloom = quality === "high";
  const bloomModule = useBloom
    ? await import("three/addons/postprocessing/UnrealBloomPass.js")
    : null;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: quality !== "low",
    alpha: false,
    powerPreference: "high-performance",
    outputBufferType: useBloom ? THREE.HalfFloatType : THREE.UnsignedByteType,
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = quality === "high" ? 1.05 : 1.1;
  renderer.shadowMap.enabled = quality === "high";
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.setClearColor(0x06131f, 1);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x06131f);
  scene.fog = new THREE.FogExp2(0x071722, quality === "low" ? 0.0135 : 0.0105);

  const camera = new THREE.PerspectiveCamera(41, 1, 0.1, 500);
  const timer = new THREE.Timer();
  timer.connect(document);

  const textures = {
    glow: makeGlowTexture(),
    mist: makeMistTexture(),
    tower: makeTowerTexture(renderer, quality),
  };

  const sky = createSky(scene, quality, textures, random);
  const water = createWater(scene, quality);
  const island = createIsland(scene, quality, random);
  const lighthouse = createLighthouse(scene, quality, renderer, textures);

  scene.add(new THREE.HemisphereLight(0x8bb2c0, 0x071018, quality === "high" ? 1.85 : 2));
  const moonLight = new THREE.DirectionalLight(0xb7d2d8, quality === "high" ? 2.25 : 1.8);
  moonLight.position.set(-42, 74, 38);
  moonLight.castShadow = quality === "high";
  if (moonLight.castShadow) {
    moonLight.shadow.mapSize.set(1024, 1024);
    moonLight.shadow.camera.left = -32;
    moonLight.shadow.camera.right = 32;
    moonLight.shadow.camera.top = 48;
    moonLight.shadow.camera.bottom = -12;
    moonLight.shadow.camera.near = 10;
    moonLight.shadow.camera.far = 140;
    moonLight.shadow.bias = -0.0004;
  }
  scene.add(moonLight);
  const facadeFill = new THREE.DirectionalLight(0x9dbdca, quality === "high" ? 1.05 : 0.82);
  facadeFill.position.set(24, 30, 58);
  scene.add(facadeFill);
  const horizonFill = new THREE.DirectionalLight(0x246f7b, 0.72);
  horizonFill.position.set(55, 16, -60);
  scene.add(horizonFill);

  const signals = [
    createSignal(scene, new THREE.Vector3(-34, 0.2, 22), 0, quality, textures),
    createSignal(scene, new THREE.Vector3(-22, 0.18, -43), 1, quality, textures),
    createSignal(scene, new THREE.Vector3(60, 0.16, 12), 2, quality, textures),
  ];
  const statusElement = root.querySelector("[data-beacon-status]");

  const cameraPath = new THREE.CatmullRomCurve3(
    [
      new THREE.Vector3(50, 13.5, 65),
      new THREE.Vector3(43, 15, 57),
      new THREE.Vector3(35, 18.5, 48),
      new THREE.Vector3(27, 24, 38),
    ],
    false,
    "centripetal",
  );
  const targetPath = new THREE.CatmullRomCurve3(
    [
      new THREE.Vector3(-2, 13.2, -6),
      new THREE.Vector3(1, 15.4, -5),
      new THREE.Vector3(5, 21, -4.5),
      new THREE.Vector3(9, 29, -4),
    ],
    false,
    "centripetal",
  );

  let bloomPass = null;
  if (bloomModule) {
    bloomPass = new bloomModule.UnrealBloomPass(new THREE.Vector2(1, 1), 0.2, 0.38, 1.22);
    renderer.setEffects([bloomPass]);
  }

  let width = 1;
  let height = 1;
  let heroLeft = 0;
  let heroTop = 0;
  let heroHeight = root.offsetHeight;
  let targetProgress = 0;
  let currentProgress = 0;
  let sceneVisible = true;
  let destroyed = false;
  let frameId = 0;
  let hasRendered = false;
  let activeSignal = -1;
  let activeSignalSource = "none";
  let activeStoryBeat = -1;
  let previewSignal = -1;
  let pinnedSignal = -1;
  let pinnedUntil = 0;
  let beamAngleState = -0.45;
  let guidedStrength = 0;
  let pointerTargetX = 0;
  let pointerTargetY = 0;
  let pointerX = 0;
  let pointerY = 0;
  let scrollTicking = false;
  let lastRenderTimestamp = 0;
  const beamOrigin = new THREE.Vector3();
  const beamDirection = new THREE.Vector3();
  const cameraPosition = new THREE.Vector3();
  const cameraTarget = new THREE.Vector3();
  const spotTargetPosition = new THREE.Vector3();
  const cameraInBeamSpace = new THREE.Vector3();
  const signalHeading = new THREE.Vector2();
  const beamHeading = new THREE.Vector2();

  const refreshBounds = () => {
    const rect = root.getBoundingClientRect();
    heroLeft = rect.left;
    heroTop = window.scrollY + rect.top;
    heroHeight = Math.max(root.offsetHeight, 1);
  };

  const updateScrollProgress = () => {
    scrollTicking = false;
    targetProgress = clamp((window.scrollY - heroTop) / Math.max(heroHeight * 0.72, 1), 0, 1);
  };

  const onScroll = () => {
    if (scrollTicking) return;
    scrollTicking = true;
    requestAnimationFrame(updateScrollProgress);
  };

  const resize = () => {
    const rect = root.getBoundingClientRect();
    width = Math.max(1, Math.round(rect.width));
    height = Math.max(1, Math.round(rect.height));
    const maxPixels = quality === "high" ? 2_000_000 : quality === "medium" ? 1_200_000 : 720_000;
    const dprLimit = quality === "high" ? 1.5 : quality === "medium" ? 1.25 : 1;
    const desiredDpr = Math.min(window.devicePixelRatio || 1, dprLimit);
    const pixelRatio = Math.min(desiredDpr, Math.sqrt(maxPixels / (width * height)));
    renderer.setPixelRatio(Math.max(0.72, pixelRatio));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    if (bloomPass) {
      const bufferSize = renderer.getDrawingBufferSize(new THREE.Vector2());
      bloomPass.setSize(bufferSize.x, bufferSize.y);
    }
    refreshBounds();
    updateScrollProgress();
  };

  const setStoryBeat = (index) => {
    if (activeStoryBeat === index) return;
    activeStoryBeat = index;
    const beat = STORY_BEATS[index] ?? STORY_BEATS[0];
    root.dataset.storyBeat = beat.key;
    root.dispatchEvent(new CustomEvent("pharos:story-active", {
      detail: { index, key: beat.key },
    }));
    if (statusElement && beat.signal < 0) {
      statusElement.textContent = index === STORY_BEATS.length - 1
        ? "继续滚动，让灯塔光束落向论文"
        : "移动指针操控视角与光束 · 滚动进入航线";
    }
  };

  const setActiveSignal = (index, source = "none") => {
    if (activeSignal === index && activeSignalSource === source) return;
    activeSignal = index;
    activeSignalSource = source;
    root.dispatchEvent(new CustomEvent("pharos:signal-active", {
      detail: { index, source },
    }));
    if (!statusElement) return;
    if (source === "scan") return;
    if (source === "preview") statusElement.textContent = `正在预览「${SIGNAL_NAMES[index]}」节点 · 点击即可锁定`;
    else if (source === "pinned") statusElement.textContent = `光束已锁定「${SIGNAL_NAMES[index]}」节点 · 5 秒后恢复航线`;
    else if (source === "story") statusElement.textContent = `滚动航线正在连接「${SIGNAL_NAMES[index]}」节点`;
    else if (activeStoryBeat === STORY_BEATS.length - 1) statusElement.textContent = "继续滚动，让灯塔光束落向论文";
    else if (index < 0) statusElement.textContent = "移动指针操控视角与光束 · 滚动进入航线";
  };

  const onSignalRequest = (event) => {
    const index = Number(event.detail?.index);
    if (!Number.isInteger(index) || index < 0 || index >= signals.length) return;
    if (event.detail?.mode === "pin") {
      pinnedSignal = index;
      pinnedUntil = performance.now() + 5200;
      previewSignal = -1;
    } else {
      previewSignal = index;
    }
    start();
  };

  const onSignalRelease = (event) => {
    if (event.detail?.mode !== "preview") return;
    const index = Number(event.detail?.index);
    if (previewSignal !== index) return;
    previewSignal = -1;
    if (!statusElement) return;
    if (pinnedSignal >= 0 && performance.now() < pinnedUntil) {
      statusElement.textContent = `光束已锁定「${SIGNAL_NAMES[pinnedSignal]}」节点 · 5 秒后恢复航线`;
      return;
    }
    const storySignal = STORY_BEATS[activeStoryBeat]?.signal ?? -1;
    statusElement.textContent = storySignal >= 0
      ? `滚动航线正在连接「${SIGNAL_NAMES[storySignal]}」节点`
      : activeStoryBeat === STORY_BEATS.length - 1
        ? "继续滚动，让灯塔光束落向论文"
        : "移动指针操控视角与光束 · 滚动进入航线";
  };

  const onPointerMove = (event) => {
    if (event.pointerType === "touch") return;
    const heroViewportTop = heroTop - window.scrollY;
    pointerTargetX = clamp(((event.clientX - heroLeft) / width) * 2 - 1, -1, 1);
    pointerTargetY = clamp(((event.clientY - heroViewportTop) / height) * 2 - 1, -1, 1);
  };

  const onPointerLeave = () => {
    pointerTargetX = 0;
    pointerTargetY = 0;
  };

  const stop = () => {
    if (!frameId) return;
    cancelAnimationFrame(frameId);
    frameId = 0;
  };

  const start = () => {
    if (destroyed || frameId || !sceneVisible || document.hidden) return;
    timer.reset();
    frameId = requestAnimationFrame(renderFrame);
  };

  function renderFrame(timestamp) {
    frameId = 0;
    if (destroyed || !sceneVisible || document.hidden) return;
    if (quality === "low" && lastRenderTimestamp && timestamp - lastRenderTimestamp < 32) {
      frameId = requestAnimationFrame(renderFrame);
      return;
    }
    lastRenderTimestamp = timestamp;

    timer.update(timestamp);
    const delta = Math.min(timer.getDelta(), 0.05);
    const elapsed = timer.getElapsed();
    currentProgress = THREE.MathUtils.damp(currentProgress, targetProgress, 3.2, delta);
    const storyBeatIndex = STORY_BEATS.findIndex((beat) => currentProgress >= beat.from && currentProgress < beat.to);
    setStoryBeat(storyBeatIndex >= 0 ? storyBeatIndex : STORY_BEATS.length - 1);
    pointerX = THREE.MathUtils.damp(pointerX, pointerTargetX, 4.1, delta);
    pointerY = THREE.MathUtils.damp(pointerY, pointerTargetY, 4.1, delta);

    cameraPath.getPointAt(currentProgress, cameraPosition);
    targetPath.getPointAt(currentProgress, cameraTarget);
    cameraPosition.x += pointerX * 2.6;
    cameraPosition.y += -pointerY * 1.15 + Math.sin(elapsed * 0.28) * 0.18;
    cameraPosition.z += pointerX * -0.75;
    cameraTarget.x += pointerX * 0.9;
    cameraTarget.y += -pointerY * 0.45;
    camera.position.copy(cameraPosition);
    camera.lookAt(cameraTarget);
    camera.rotation.z += pointerX * -0.006 + Math.sin(elapsed * 0.17) * 0.0015;

    if (pinnedSignal >= 0 && performance.now() >= pinnedUntil) pinnedSignal = -1;
    const storySignal = STORY_BEATS[activeStoryBeat]?.signal ?? -1;
    const guidedSignal = previewSignal >= 0
      ? previewSignal
      : pinnedSignal >= 0
        ? pinnedSignal
        : storySignal;
    const guidedSource = previewSignal >= 0
      ? "preview"
      : pinnedSignal >= 0
        ? "pinned"
        : storySignal >= 0
          ? "story"
          : "scan";
    let desiredBeamAngle = elapsed * 0.145 + pointerX * 0.78 - 0.45;
    if (guidedSignal >= 0) {
      const target = signals[guidedSignal].position;
      desiredBeamAngle = Math.atan2(
        -(target.z - LIGHTHOUSE_POSITION.z),
        target.x - LIGHTHOUSE_POSITION.x,
      );
    }
    const angleDelta = Math.atan2(
      Math.sin(desiredBeamAngle - beamAngleState),
      Math.cos(desiredBeamAngle - beamAngleState),
    );
    const angleResponse = 1 - Math.exp(-(guidedSignal >= 0 ? 5.4 : 2.6) * delta);
    beamAngleState += angleDelta * angleResponse;
    guidedStrength = THREE.MathUtils.damp(guidedStrength, guidedSignal >= 0 ? 1 : 0, 4.8, delta);
    const beamAngle = beamAngleState;
    lighthouse.beamRig.rotation.y = beamAngle;
    lighthouse.beamRig.rotation.z = -0.035;
    lighthouse.beamRig.updateWorldMatrix(true, false);
    cameraInBeamSpace.copy(camera.position);
    lighthouse.beamRig.worldToLocal(cameraInBeamSpace);
    const billboardAngle = Math.atan2(-cameraInBeamSpace.y, cameraInBeamSpace.z);
    lighthouse.beamPlanes.forEach((plane) => {
      plane.rotation.x = billboardAngle;
    });
    lighthouse.beamRig.getWorldPosition(beamOrigin);
    beamDirection.set(Math.cos(beamAngle), -0.045, -Math.sin(beamAngle)).normalize();
    water.material.uniforms.time.value = elapsed;
    water.material.uniforms.beamOrigin.value.copy(beamOrigin);
    water.material.uniforms.beamDirection.value.copy(beamDirection);
    water.material.uniforms.beamIntensity.value = 0.42 + guidedStrength * 0.08 + Math.sin(elapsed * 0.74) * 0.035;
    lighthouse.beamMaterials.forEach((material) => {
      material.uniforms.time.value = elapsed;
      material.uniforms.opacity.value = material.userData.baseOpacity * (1 + guidedStrength * 0.32);
      material.uniforms.intensity.value = material.userData.baseIntensity * (1 + guidedStrength * 0.2);
    });

    spotTargetPosition.copy(beamOrigin).addScaledVector(beamDirection, 88);
    lighthouse.spotTarget.position.copy(spotTargetPosition);
    lighthouse.spotTarget.updateMatrixWorld();

    lighthouse.lampGlow.material.opacity = 0.65 + guidedStrength * 0.12 + Math.sin(elapsed * 1.7) * 0.055;
    const lampGlowScale = 10 + guidedStrength * 2.4;
    lighthouse.lampGlow.scale.set(lampGlowScale, lampGlowScale, 1);
    lighthouse.spot.intensity = (quality === "high" ? 190 : 120) * (1 + guidedStrength * 0.28);
    if (bloomPass) bloomPass.strength = 0.2 + guidedStrength * 0.07;
    root.classList.toggle("is-beam-guided", guidedStrength > 0.08);
    if ("emissiveIntensity" in lighthouse.lensMaterial) {
      lighthouse.lensMaterial.emissiveIntensity = (quality === "high" ? 10 : 7) + Math.sin(elapsed * 1.7) * 0.8;
    }
    if ("emissiveIntensity" in lighthouse.windowMaterial) {
      lighthouse.windowMaterial.emissiveIntensity = 4.6 + Math.sin(elapsed * 0.61) * 0.32;
    }

    island.foamRings.forEach((ring, index) => {
      const cycle = (elapsed * 0.075 + index * 0.47) % 1;
      const scale = 0.96 + cycle * 0.1;
      ring.scale.set(scale, scale * 0.72, scale);
      ring.material.opacity = 0.14 * (1 - cycle);
    });

    sky.stars.rotation.y = elapsed * 0.0035;
    sky.moonHalo.material.opacity = 0.15 + Math.sin(elapsed * 0.18) * 0.025;
    sky.mistLayers.forEach((mist, index) => {
      mist.position.x = mist.userData.originX + Math.sin(elapsed * mist.userData.speed + index) * 7;
      mist.material.opacity *= 0.995;
      mist.material.opacity += (0.028 + (index % 4) * 0.004 - mist.material.opacity) * 0.004;
    });

    let strongestSignal = -1;
    let strongestAlignment = 0;
    beamHeading.set(beamDirection.x, beamDirection.z).normalize();
    signals.forEach((signal, index) => {
      signalHeading.set(
        signal.position.x - beamOrigin.x,
        signal.position.z - beamOrigin.z,
      ).normalize();
      const alignment = signalHeading.dot(beamHeading);
      if (alignment > 0.988 && alignment > strongestAlignment) {
        strongestAlignment = alignment;
        strongestSignal = index;
      }

      const sweepActivation = alignment > 0.982 ? clamp((alignment - 0.982) / 0.018, 0, 1) : 0;
      const targetActivation = index === guidedSignal
        ? Math.max(sweepActivation, 0.78 + guidedStrength * 0.22)
        : sweepActivation;
      signal.userData.activation = THREE.MathUtils.damp(signal.userData.activation, targetActivation, 5.2, delta);
      const activation = signal.userData.activation;
      signal.position.y = signal.userData.baseY + Math.sin(elapsed * 0.72 + index * 2.1) * 0.18;
      signal.rotation.z = Math.sin(elapsed * 0.55 + index) * 0.025;
      signal.userData.lightMaterial.emissiveIntensity = 0.7 + activation * 7.5;
      signal.userData.glow.material.opacity = 0.18 + activation * 0.58;
      const glowScale = 3.6 + activation * 3.8;
      signal.userData.glow.scale.set(glowScale, glowScale, 1);
    });
    setActiveSignal(guidedSignal >= 0 ? guidedSignal : strongestSignal, guidedSignal >= 0 ? guidedSource : "scan");

    renderer.render(scene, camera);
    if (!hasRendered) {
      hasRendered = true;
      root.classList.add("is-scene-ready");
      root.dataset.sceneQuality = quality;
    }
    frameId = requestAnimationFrame(renderFrame);
  }

  const intersectionObserver = new IntersectionObserver(
    ([entry]) => {
      sceneVisible = Boolean(entry?.isIntersecting);
      if (sceneVisible) start();
      else stop();
    },
    { threshold: 0.01 },
  );
  intersectionObserver.observe(root);

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(root);
  window.addEventListener("scroll", onScroll, { passive: true });
  root.addEventListener("pointermove", onPointerMove, { passive: true });
  root.addEventListener("pointerleave", onPointerLeave, { passive: true });
  root.addEventListener("pharos:signal-request", onSignalRequest);
  root.addEventListener("pharos:signal-release", onSignalRelease);

  const onVisibilityChange = () => {
    if (document.hidden) stop();
    else start();
  };
  document.addEventListener("visibilitychange", onVisibilityChange);

  const onContextLost = (event) => {
    event.preventDefault();
    stop();
    hasRendered = false;
    root.classList.remove("is-scene-ready");
  };
  const onContextRestored = () => {
    renderer.compileAsync(scene, camera).catch(() => {});
    start();
  };
  canvas.addEventListener("webglcontextlost", onContextLost);
  canvas.addEventListener("webglcontextrestored", onContextRestored);

  const destroy = () => {
    if (destroyed) return;
    destroyed = true;
    stop();
    intersectionObserver.disconnect();
    resizeObserver.disconnect();
    window.removeEventListener("scroll", onScroll);
    root.removeEventListener("pointermove", onPointerMove);
    root.removeEventListener("pointerleave", onPointerLeave);
    root.removeEventListener("pharos:signal-request", onSignalRequest);
    root.removeEventListener("pharos:signal-release", onSignalRelease);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    canvas.removeEventListener("webglcontextlost", onContextLost);
    canvas.removeEventListener("webglcontextrestored", onContextRestored);
    window.removeEventListener("pagehide", onPageHide);
    window.removeEventListener("pageshow", onPageShow);
    timer.dispose();
    if (bloomPass) bloomPass.dispose();
    disposeScene(scene);
    renderer.dispose();
    root.classList.remove("is-scene-ready");
    root.classList.remove("is-beam-guided");
    delete root.dataset.storyBeat;
    delete root.dataset.sceneQuality;
  };

  resize();
  currentProgress = targetProgress;
  renderer.compileAsync(scene, camera).catch(() => {});
  start();
  const onPageHide = (event) => {
    if (event.persisted) stop();
    else destroy();
  };
  const onPageShow = (event) => {
    if (!event.persisted || destroyed) return;
    const rect = root.getBoundingClientRect();
    sceneVisible = rect.bottom > 0 && rect.top < window.innerHeight;
    lastRenderTimestamp = 0;
    resize();
    start();
  };
  window.addEventListener("pagehide", onPageHide);
  window.addEventListener("pageshow", onPageShow);
  return destroy;
}
