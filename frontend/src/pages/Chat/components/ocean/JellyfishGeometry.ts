export type Vec3 = { x: number; y: number; z: number };
export type JellySpecies = 'moon' | 'nettle' | 'crown';

export interface BellVertex extends Vec3 {
  nx: number;
  ny: number;
  nz: number;
  /** A subtle material feature, centred around 0.5; lighting supplies the volume. */
  tone: number;
  /** Soft golden inner-band mask in material coordinates; the renderer adds light. */
  warmth: number;
}

export interface StrandRoot {
  position: Vec3;
  kind: 'filament' | 'arm';
  /** Both length and radius use the undeformed bell radius as their unit. */
  length: number;
  radius: number;
  seed: number;
}

export interface BellMesh {
  vertices: BellVertex[];
  /** Counter-clockwise outward-facing triangles, including the rolled lip. */
  triangles: number[];
  /** The open inner lip, ordered around the bell, without a duplicate endpoint. */
  rim: Vec3[];
  /** The actual outer margin before its inward roll, in the same 48-point order. */
  outerRim: Vec3[];
  /** Stable ordering: ten peripheral filaments, then twelve layered oral arms. */
  roots: StrandRoot[];
}

const TAU = Math.PI * 2;
const SIDES = 48;
const DOME_RINGS = 20;
const LIP_RINGS = 3;
const FILAMENTS = 10;
const ARMS = 12;
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const smooth = (value: number) => {
  const t = clamp(value, 0, 1);
  return t * t * (3 - 2 * t);
};
const hash = (value: number) => {
  const n = Math.sin(value * 127.1 + 311.7) * 43758.5453;
  return n - Math.floor(n);
};

const topology: number[] = [];
for (let side = 0; side < SIDES; side++) {
  topology.push(0, 1 + side, 1 + (side + 1) % SIDES);
}
for (let ring = 0; ring < DOME_RINGS + LIP_RINGS - 1; ring++) {
  for (let side = 0; side < SIDES; side++) {
    const next = (side + 1) % SIDES;
    const a = 1 + ring * SIDES + side;
    const b = a + SIDES;
    const c = 1 + (ring + 1) * SIDES + next;
    const d = 1 + ring * SIDES + next;
    topology.push(a, b, c, a, c, d);
  }
}

/**
 * A genuinely three-dimensional, open bell, with no texture or image dependency.
 * Y points down. A small shoulder contraction preserves the rounded dome while
 * the flexible lower meridians close and roll inward. Apply the pose after this.
 */
export function createBellMesh(
  species: JellySpecies,
  contraction: number,
  rimContraction: number,
  time: number,
  seed: number,
): BellMesh {
  const stroke = clamp(Number.isFinite(contraction) ? contraction : 0, 0, 1);
  const rimStroke = clamp(Number.isFinite(rimContraction) ? rimContraction : 0, 0, 1);
  const clock = Number.isFinite(time) ? time : 0;
  const identity = Number.isFinite(seed) ? seed : 0;
  const restingHeight = species === 'moon' ? 0.48 : species === 'nettle' ? 0.64 : 0.7;
  const height = restingHeight * (1 + stroke * 0.06);
  const closure = species === 'nettle' ? 0.19 : species === 'moon' ? 0.17 : 0.16;
  const lobeCount = species === 'crown' ? 8 : 12;
  const phase = hash(identity + 2.3) * TAU;
  const vertices: BellVertex[] = [];
  const rim: Vec3[] = [];
  const outerRim: Vec3[] = [];
  const roots: StrandRoot[] = [];

  const section = (q: number, azimuth: number): Vec3 => {
    const theta = q * Math.PI * 0.5;
    const sinTheta = Math.sin(theta);
    const flexible = smooth((q - 0.55) / 0.45);
    const margin = flexible * flexible;
    // The upper shoulder closes by at most 4.5%, while the flexible margin
    // supplies most of the stroke. The dome becomes at most 6% taller.
    const shoulder = smooth(q / 0.38);
    const radius = sinTheta * (1 - 0.045 * stroke * shoulder - closure * rimStroke * margin);
    const radialFlutter = 0.005 * margin * Math.sin(azimuth * 3 + clock * 0.24 + phase);
    const scallop = Math.sin(azimuth * lobeCount + phase) * (species === 'crown' ? 0.015 : 0.007) * margin;
    const y = -height * Math.cos(theta) + 0.022 * margin - 0.008 * rimStroke * margin + scallop;
    return {
      x: (radius + radialFlutter) * Math.cos(azimuth),
      y,
      z: (radius + radialFlutter) * Math.sin(azimuth),
    };
  };

  const materialTone = (q: number, azimuth: number): number => {
    const radial = Math.sin(q * Math.PI * 0.5);
    const canals = Math.max(0, Math.cos(azimuth * (species === 'nettle' ? 12 : 8) + phase)) ** 18;
    let tone = 0.48 + canals * radial * radial * (species === 'moon' ? 0.025 : 0.045);
    if (species === 'moon') {
      // Four small gonad rings in material coordinates, independent of lighting.
      // Their small contrast avoids turning the dome into a patterned billboard.
      const x = radial * Math.cos(azimuth);
      const z = radial * Math.sin(azimuth);
      for (let lobe = 0; lobe < 4; lobe++) {
        const angle = lobe * TAU / 4 + phase * 0.15;
        const distance = Math.hypot(x - Math.cos(angle) * 0.23, z - Math.sin(angle) * 0.23);
        tone += Math.exp(-(((distance - 0.115) / 0.033) ** 2)) * 0.075;
      }
    }
    return clamp(tone, 0.42, 0.6);
  };

  const materialWarmth = (q: number, azimuth: number): number => {
    // One narrow, soft ring follows the bell's material rather than the screen.
    // Brightening on the power stroke belongs to the renderer, not this mask.
    const centre = 0.58 + Math.sin(azimuth * 2 + phase) * 0.006;
    return Math.exp(-(((q - centre) / 0.045) ** 2));
  };

  const append = (position: Vec3, tone: number, warmth = 0) => {
    vertices.push({ ...position, nx: 0, ny: 0, nz: 0, tone, warmth });
  };
  append({ x: 0, y: -height, z: 0 }, 0.48);
  for (let ring = 1; ring <= DOME_RINGS; ring++) {
    const q = ring / DOME_RINGS;
    for (let side = 0; side < SIDES; side++) {
      const azimuth = side * TAU / SIDES;
      const position = section(q, azimuth);
      append(position, materialTone(q, azimuth), materialWarmth(q, azimuth));
      if (ring === DOME_RINGS) outerRim.push({ ...position });
    }
  }

  // Continue the shell around a thin curled lip rather than closing it with a
  // disk. The opening therefore remains hollow from every camera direction.
  const roll = 0.024 + rimStroke * 0.025;
  const rollDepth = 0.024 + rimStroke * 0.009;
  for (let ring = 1; ring <= LIP_RINGS; ring++) {
    const t = ring / LIP_RINGS;
    const angle = t * Math.PI;
    for (let side = 0; side < SIDES; side++) {
      const azimuth = side * TAU / SIDES;
      const outer = section(1, azimuth);
      const inset = roll * (1 - Math.cos(angle));
      const position = {
        x: outer.x - Math.cos(azimuth) * inset,
        y: outer.y + rollDepth * Math.sin(angle),
        z: outer.z - Math.sin(azimuth) * inset,
      };
      append(position, 0.49);
      if (ring === LIP_RINGS) rim.push({ ...position });
    }
  }

  // Area-weighted geometric normals respect the flexible meridians, scallops,
  // and inward-facing rim; no billboard-facing or screen-space normals are used.
  for (let triangle = 0; triangle < topology.length; triangle += 3) {
    const a = vertices[topology[triangle]];
    const b = vertices[topology[triangle + 1]];
    const c = vertices[topology[triangle + 2]];
    const ux = b.x - a.x;
    const uy = b.y - a.y;
    const uz = b.z - a.z;
    const vx = c.x - a.x;
    const vy = c.y - a.y;
    const vz = c.z - a.z;
    const nx = uy * vz - uz * vy;
    const ny = uz * vx - ux * vz;
    const nz = ux * vy - uy * vx;
    for (const vertex of [a, b, c]) {
      vertex.nx += nx;
      vertex.ny += ny;
      vertex.nz += nz;
    }
  }
  for (const vertex of vertices) {
    const length = Math.hypot(vertex.nx, vertex.ny, vertex.nz);
    if (length > 1e-10) {
      vertex.nx /= length;
      vertex.ny /= length;
      vertex.nz /= length;
    } else {
      vertex.nx = 0;
      vertex.ny = -1;
      vertex.nz = 0;
    }
  }

  const rootRotation = phase * 0.18;
  for (let index = 0; index < FILAMENTS; index++) {
    const rootSeed = hash(identity * 13.7 + index * 7.13 + 1.5);
    const azimuth = index * TAU / FILAMENTS + rootRotation;
    const length = species === 'moon'
      ? 0.3 + rootSeed * 0.25
      : species === 'nettle' ? 2.1 + rootSeed * 0.8 : 0.9 + rootSeed * 0.4;
    roots.push({
      position: section(1, azimuth), kind: 'filament', length,
      radius: (species === 'moon' ? 0.0045 : 0.0055) * (0.85 + rootSeed * 0.3),
      seed: rootSeed,
    });
  }
  for (let index = 0; index < ARMS; index++) {
    const rootSeed = hash(identity * 11.3 + index * 5.17 + 93);
    // Stagger three layers through the volume, so the added tissue remains
    // visible in projection instead of piling up on the same four chains.
    const layer = Math.floor(index / 4);
    const azimuth = index % 4 * TAU / 4 + layer * Math.PI / 6 + rootRotation + 0.4 + (rootSeed - 0.5) * 0.22;
    const rootRadius = 0.11 + layer * 0.13 + rootSeed * 0.045;
    const baseLength = species === 'moon'
      ? 0.4 + rootSeed * 0.25
      : species === 'nettle' ? 1.1 + rootSeed * 0.4 : 1.1 + rootSeed * 0.3;
    const length = baseLength * (layer === 0 ? 1.2 : layer === 1 ? 1.02 : 0.8);
    roots.push({
      position: { x: Math.cos(azimuth) * rootRadius, y: -0.065 + layer * 0.013 - stroke * 0.008, z: Math.sin(azimuth) * rootRadius },
      kind: 'arm', length,
      radius: (species === 'crown' ? 0.065 : species === 'moon' ? 0.043 : 0.045) * (1.1 - layer * 0.1) * (0.85 + rootSeed * 0.3),
      seed: rootSeed,
    });
  }

  return { vertices, triangles: topology.slice(), rim, outerRim, roots };
}
