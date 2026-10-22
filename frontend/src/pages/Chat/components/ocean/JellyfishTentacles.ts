import type { StrandRoot, Vec3 } from './JellyfishGeometry';

export type TentaclePoint = Vec3;

export interface TentacleStrand {
  points: Vec3[];
  kind: 'filament' | 'arm';
  radius: number;
  seed: number;
}

type Chain = {
  strand: TentacleStrand;
  previous: Vec3[];
  anchor: Vec3;
  unitLength: number;
  segmentLength: number;
};

const STEP = 1 / 60;
const EPSILON = 0.000001;
const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const copy = (point: Vec3): Vec3 => ({ x: point.x, y: point.y, z: point.z });
const finite = (point: Vec3) => Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z);

/**
 * Small, inextensible ropes in world space. Only the attachment is kinematic:
 * bell turns pull on the first joint while the rest keeps its position in water.
 * The current is a force field, never a replacement pose for the strands.
 */
export class JellyfishTentacles {
  private chains: Chain[] = [];
  private visible: TentacleStrand[] = [];
  private scale: number;
  private previousStep = STEP;
  private ambient: Vec3 = { x: 0, y: 0, z: 0 };

  constructor(roots: StrandRoot[], scale: number, toWorld: (point: Vec3) => Vec3) {
    this.scale = this.safeScale(scale);
    this.build(roots, toWorld);
  }

  get strands(): readonly TentacleStrand[] {
    return this.visible;
  }

  update(
    dt: number,
    time: number,
    roots: StrandRoot[],
    scale: number,
    toWorld: (point: Vec3) => Vec3,
    ambient: Vec3 = { x: 0, y: 0, z: 0 },
  ): void {
    this.ambient.x = Number.isFinite(ambient.x) ? ambient.x : 0;
    this.ambient.y = Number.isFinite(ambient.y) ? ambient.y : 0;
    this.ambient.z = Number.isFinite(ambient.z) ? ambient.z : 0;
    const nextScale = this.safeScale(scale);
    const ratio = nextScale / this.scale;
    const changed = roots.length !== this.chains.length || roots.some((root, index) => {
      const chain = this.chains[index];
      return !chain || root.kind !== chain.strand.kind || root.seed !== chain.strand.seed
        || root.length / chain.unitLength < 0.6 || root.length / chain.unitLength > 1.65;
    });
    if (changed || ratio < 0.72 || ratio > 1.4) {
      this.scale = nextScale;
      this.previousStep = STEP;
      this.build(roots, toWorld);
      return;
    }

    const anchors = roots.map((root) => toWorld(root.position));
    if (anchors.some((anchor) => !finite(anchor))) return;
    const rescaled = Math.abs(ratio - 1) > 0.00001;
    for (let index = 0; index < this.chains.length; index++) {
      const chain = this.chains[index];
      const anchor = anchors[index];
      const old = chain.anchor;
      const displaced = Math.hypot(anchor.x - old.x, anchor.y - old.y, anchor.z - old.z) > Math.max(32, nextScale * 0.9);
      // Viewport changes should not fling the whole rope through its new anchor.
      // Normal swimming and breathing move only the attachment, preserving lag.
      if (rescaled || displaced) {
        for (const list of [chain.strand.points, chain.previous]) {
          for (const point of list) {
            point.x = anchor.x + (point.x - old.x) * ratio;
            point.y = anchor.y + (point.y - old.y) * ratio;
            point.z = anchor.z + (point.z - old.z) * ratio;
          }
        }
        chain.anchor = copy(anchor);
      }
      chain.unitLength = Math.max(0.01, roots[index].length);
      chain.segmentLength = chain.unitLength * nextScale / (chain.strand.points.length - 1);
      chain.strand.radius = Math.max(0, roots[index].radius) * nextScale;
    }
    this.scale = nextScale;

    const duration = Number.isFinite(dt) ? clamp(dt, 0, 0.05) : 0;
    if (!duration) {
      for (let index = 0; index < this.chains.length; index++) {
        this.pin(this.chains[index], anchors[index]);
        this.chains[index].anchor = copy(anchors[index]);
      }
      return;
    }

    // At most three substeps; previous displacement is converted to the new
    // timestep so a 30/60/120 Hz renderer does not change rope velocity.
    const count = Math.ceil(duration / STEP);
    const step = duration / count;
    const clock = Number.isFinite(time) ? time : 0;
    for (let substep = 0; substep < count; substep++) {
      const progress = (substep + 1) / count;
      const sampleTime = clock - duration + step * (substep + 1);
      for (let index = 0; index < this.chains.length; index++) {
        const chain = this.chains[index];
        const goal = anchors[index];
        const anchor = {
          x: chain.anchor.x + (goal.x - chain.anchor.x) * progress,
          y: chain.anchor.y + (goal.y - chain.anchor.y) * progress,
          z: chain.anchor.z + (goal.z - chain.anchor.z) * progress,
        };
        this.integrate(chain, step, sampleTime);
        this.constrain(chain, anchor);
      }
      this.previousStep = step;
    }
    for (let index = 0; index < this.chains.length; index++) this.chains[index].anchor = copy(anchors[index]);
  }

  /** A bounded velocity impulse in pixels/second, fading smoothly with distance. */
  disturb(point: Vec3, strength: number): void {
    if (!finite(point) || !Number.isFinite(strength)) return;
    const impulse = clamp(strength, -this.scale * 1.2, this.scale * 1.2);
    const reach = this.scale * 2.5;
    for (const chain of this.chains) {
      const points = chain.strand.points;
      for (let index = 1; index < points.length; index++) {
        const position = points[index];
        const dx = position.x - point.x;
        const dy = position.y - point.y;
        const dz = position.z - point.z;
        const distance = Math.hypot(dx, dy, dz);
        if (distance < EPSILON) continue;
        const weight = Math.exp(-(distance * distance) / (reach * reach)) * (0.3 + 0.7 * index / (points.length - 1));
        const push = impulse * weight * this.previousStep / distance;
        chain.previous[index].x -= dx * push;
        chain.previous[index].y -= dy * push;
        chain.previous[index].z -= dz * push;
      }
    }
  }

  private safeScale(scale: number): number {
    return Number.isFinite(scale) ? Math.max(0.1, scale) : 1;
  }

  private build(roots: StrandRoot[], toWorld: (point: Vec3) => Vec3): void {
    this.chains = roots.map((root) => {
      const length = Math.max(0.01, root.length);
      const segments = clamp(Math.ceil(length * this.scale / Math.max(6, this.scale * 0.09)), 6, root.kind === 'arm' ? 20 : 26);
      const local = copy(root.position);
      const points = [toWorld(local)];
      const seed = root.seed * 6.283185307;
      // A relaxed, gently curved initial chain avoids a first-frame comb of
      // straight lines. This construction is never reapplied during swimming.
      for (let index = 1; index <= segments; index++) {
        const t = index / segments;
        const curl = t * t;
        const dx = Math.sin(seed + t * 4.2) * 0.29 + Math.cos(seed * 0.7) * curl * 0.36;
        const dy = 1 - curl * t * 0.42;
        const dz = Math.cos(seed * 0.8 + t * 3.6) * 0.25 + Math.sin(seed) * curl * 0.3;
        const unit = length / segments / Math.hypot(dx, dy, dz);
        local.x += dx * unit;
        local.y += dy * unit;
        local.z += dz * unit;
        points.push(toWorld(local));
      }
      const strand: TentacleStrand = {
        points, kind: root.kind, radius: Math.max(0, root.radius) * this.scale, seed: root.seed,
      };
      return {
        strand, previous: points.map(copy), anchor: copy(points[0]),
        unitLength: length, segmentLength: length * this.scale / segments,
      };
    });
    this.visible = this.chains.map((chain) => chain.strand);
  }

  private integrate(chain: Chain, step: number, time: number): void {
    const points = chain.strand.points;
    const drag = Math.exp(-step * (chain.strand.kind === 'arm' ? 2.8 : 2.05));
    const carry = step / this.previousStep * drag;
    const flowWeight = (1 - drag) * step;
    const spatial = 6.283185307 / (this.scale * 0.72);
    const seed = chain.strand.seed * 6.283185307;
    const pliancy = (chain.strand.kind === 'arm' ? 0.72 : 1) * (0.84 + chain.strand.seed * 0.32);
    for (let index = 1; index < points.length; index++) {
      const point = points[index];
      const previous = chain.previous[index];
      const t = index / (points.length - 1);
      const x = point.x * spatial;
      const y = point.y * spatial;
      const z = point.z * spatial;
      // Broad eddies span 0.72–0.87 bell radii, so different sections of a long
      // rope experience different currents instead of stretching into a cable.
      // Cross-axis samples keep the main field divergence-free and continuous;
      // these are water velocities, never prescribed point positions.
      const across = x * 0.9 - time * 0.53;
      const down = y * 0.83 - time * 0.61;
      const depth = z + time * 0.47;
      const freeTip = t * t * (3 - 2 * t);
      // A slow, broad secondary eddy rolls the free half of each strand. Its
      // phase and tissue response vary by strand; no oscillating target pose
      // pulls every chain into the same wave or erases its swimming inertia.
      const roll = y * 0.48 + seed + time * 0.29;
      const curl = (0.012 + freeTip * 0.048) * pliancy;
      // The current carrying the bell carries its tissue too. Pulse thrust is
      // deliberately absent here, so propulsion still leaves a trailing lag.
      const flowX = this.ambient.x + this.scale * (0.058 * Math.sin(depth) + 0.024 * Math.cos(down) + curl * Math.cos(roll));
      const flowY = this.ambient.y + this.scale * (0.026 * Math.sin(across) + 0.026 * Math.cos(depth)
        + freeTip * pliancy * 0.018 * Math.sin(x * 0.48 + seed - time * 0.23));
      const flowZ = this.ambient.z + this.scale * (0.052 * Math.sin(down) + 0.024 * Math.cos(across) + curl * Math.sin(roll));
      const vx = (point.x - previous.x) * carry;
      const vy = (point.y - previous.y) * carry;
      const vz = (point.z - previous.z) * carry;
      previous.x = point.x;
      previous.y = point.y;
      previous.z = point.z;
      point.x += vx + flowX * flowWeight;
      point.y += vy + flowY * flowWeight + this.scale * 0.03 * step * step;
      point.z += vz + flowZ * flowWeight;
    }
  }

  private constrain(chain: Chain, anchor: Vec3): void {
    const points = chain.strand.points;
    this.pin(chain, anchor);
    for (let pass = 0; pass < 5; pass++) {
      // Weak bending resistance suppresses kinks, not broad curves. The loose
      // tips remain softer than the thicker tissue near the attachment.
      const stiffness = chain.strand.kind === 'arm' ? 0.014 : 0.006;
      for (let index = 1; index < points.length - 1; index++) {
        const point = points[index];
        const before = points[index - 1];
        const after = points[index + 1];
        const weight = stiffness * (1 - 0.82 * index / (points.length - 1));
        point.x += ((before.x + after.x) * 0.5 - point.x) * weight;
        point.y += ((before.y + after.y) * 0.5 - point.y) * weight;
        point.z += ((before.z + after.z) * 0.5 - point.z) * weight;
      }
      for (let index = points.length - 1; index > 0; index--) this.segment(chain, index);
      for (let index = 1; index < points.length; index++) this.segment(chain, index);
      this.pin(chain, anchor);
    }
    // Finish from the pinned end without moving an already-solved joint. This
    // removes residual stretch after a large bell stroke while retaining each
    // segment's world-space direction and the chain's accumulated broad curve.
    for (let index = 1; index < points.length; index++) this.segment(chain, index, true);
  }

  private segment(chain: Chain, index: number, oneSided = false): void {
    const first = chain.strand.points[index - 1];
    const second = chain.strand.points[index];
    const dx = second.x - first.x;
    const dy = second.y - first.y;
    const dz = second.z - first.z;
    const distance = Math.hypot(dx, dy, dz);
    if (distance < EPSILON) {
      second.y = first.y + chain.segmentLength;
      return;
    }
    const weight = (distance - chain.segmentLength) / distance;
    if (index === 1 || oneSided) {
      second.x -= dx * weight;
      second.y -= dy * weight;
      second.z -= dz * weight;
    } else {
      first.x += dx * weight * 0.5;
      first.y += dy * weight * 0.5;
      first.z += dz * weight * 0.5;
      second.x -= dx * weight * 0.5;
      second.y -= dy * weight * 0.5;
      second.z -= dz * weight * 0.5;
    }
  }

  private pin(chain: Chain, anchor: Vec3): void {
    Object.assign(chain.strand.points[0], anchor);
    Object.assign(chain.previous[0], anchor);
  }
}
