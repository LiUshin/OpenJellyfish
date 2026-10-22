import { createBellMesh, type BellMesh, type JellySpecies, type Vec3 } from './JellyfishGeometry';
import { JellyfishTentacles } from './JellyfishTentacles';

export type JellyfishSpecies = 'mixed' | JellySpecies;
export type JellyfishTheme = 'light' | 'dark';
export interface JellyfishOptions {
  species: JellyfishSpecies;
  theme: JellyfishTheme;
  speed: number;
  paused: boolean;
  /** Character-cell width in CSS pixels. */
  detail: number;
}

type Jelly = {
  species: JellySpecies; x: number; y: number; z: number; size: number; depth: number;
  phase: number; period: number; vx: number; vy: number; tilt: number;
  targetX: number; targetY: number; nextTurn: number; turns: number;
  currentX: number; currentY: number;
  mesh: BellMesh; tentacles: JellyfishTentacles | null;
};
type Projected = { x: number; y: number; z: number; scale: number };
type Vertex = Projected & { tone: number; warmth: number };
type Ripple = { x: number; y: number; age: number };
const TAU = Math.PI * 2;
const SPECIES: JellySpecies[] = ['moon', 'nettle', 'crown'];
const GLYPHS = ' .:o+=xS%X8@#';
const DEFAULTS: JellyfishOptions = { species: 'mixed', theme: 'dark', speed: 0.8, paused: false, detail: 8 };
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const smooth = (v: number) => v * v * (3 - 2 * v);
const noise = (x: number, y: number) => {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return n - Math.floor(n);
};

/** Deforming 3D shells and world-space ropes, projected into a character grid. */
export class JellyfishEngine {
  private readonly context: CanvasRenderingContext2D;
  private readonly observer: ResizeObserver;
  private readonly glow = document.createElement('canvas');
  private options: JellyfishOptions;
  private width = 1;
  private height = 1;
  private columns = 1;
  private rows = 1;
  private cellX = 5;
  private cellY = 6.5;
  private density = new Float32Array(1);
  private colors = new Uint8Array(1);
  private glyphs = new Uint8Array(1);
  private surfaceDepth = new Float32Array(1);
  private surfaceTone = new Float32Array(1);
  private surfaceWarmth = new Float32Array(1);
  private creatures: Jelly[] = [];
  private ripples: Ripple[] = [];
  private frame = 0;
  private previous = 0;
  private lastPaint = 0;
  private elapsed = 0;
  private hidden = false;
  private destroyed = false;

  constructor(private readonly canvas: HTMLCanvasElement, options: Partial<JellyfishOptions> = {}) {
    const context = canvas.getContext('2d', { alpha: true });
    if (!context) throw new Error('Canvas 2D is unavailable.');
    this.context = context;
    this.options = { ...DEFAULTS, ...options };
    this.sanitize();
    this.hidden = document.hidden;
    this.observer = new ResizeObserver(this.resize);
    try {
      this.observer.observe(canvas);
      document.addEventListener('visibilitychange', this.visibility);
      this.resize();
    } catch (error) {
      // A failed canvas initialization must not leave an observer or frame
      // attached after the decorative wrapper falls back to its CSS ocean.
      this.destroy();
      throw error;
    }
  }

  update(options: Partial<JellyfishOptions>): void {
    if (this.destroyed) return;
    const oldDetail = this.options.detail;
    const oldSpecies = this.options.species;
    this.options = { ...this.options, ...options };
    this.sanitize();
    if (oldSpecies !== this.options.species) this.populate();
    if (oldDetail !== this.options.detail) this.resize();
    this.previous = 0;
    if (this.options.paused) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
    this.paint();
    this.schedule();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    document.removeEventListener('visibilitychange', this.visibility);
    this.creatures = [];
    this.ripples = [];
  }

  disturb(x: number, y: number): void {
    if (this.destroyed || !Number.isFinite(x) || !Number.isFinite(y)) return;
    this.ripples.push({ x, y, age: 0 });
    this.ripples = this.ripples.slice(-3);
    for (const jelly of this.creatures) jelly.tentacles?.disturb({ x, y, z: jelly.z }, this.radius(jelly) * 0.22);
    this.paint();
  }

  private sanitize(): void {
    this.options.detail = clamp(Number.isFinite(this.options.detail) ? this.options.detail : 8, 4, 10);
    this.options.speed = clamp(Number.isFinite(this.options.speed) ? this.options.speed : 0.8, 0, 3);
  }

  private readonly visibility = (): void => {
    this.hidden = document.hidden;
    this.previous = 0;
    if (this.hidden) { cancelAnimationFrame(this.frame); this.frame = 0; }
    else this.schedule();
  };

  private readonly resize = (): void => {
    if (this.destroyed) return;
    const rect = this.canvas.getBoundingClientRect();
    this.width = Math.max(1, rect.width);
    this.height = Math.max(1, rect.height);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.width * dpr);
    this.canvas.height = Math.round(this.height * dpr);
    this.context.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cellX = Math.max(this.options.detail, Math.sqrt(this.width * this.height / 70000));
    this.cellY = this.cellX;
    this.columns = Math.ceil(this.width / this.cellX);
    this.rows = Math.ceil(this.height / this.cellY);
    this.glow.width = this.columns;
    this.glow.height = this.rows;
    const count = this.columns * this.rows;
    this.density = new Float32Array(count);
    this.colors = new Uint8Array(count);
    this.glyphs = new Uint8Array(count);
    this.surfaceDepth = new Float32Array(count);
    this.surfaceTone = new Float32Array(count);
    this.surfaceWarmth = new Float32Array(count);
    this.populate();
    this.paint();
    this.schedule();
  };

  private radius(jelly: Jelly): number {
    const base = Math.min(this.width * (this.width < 700 ? 0.17 : 0.14), this.height * 0.145);
    return Math.max(1, base * jelly.size);
  }

  private populate(): void {
    const layout = this.width < 700
      ? [[0.16, 0.23, 0.75, 0.85], [0.86, 0.31, 0.7, 0.9], [0.24, 0.66, 1.05, 1], [0.86, 0.76, 0.48, 0.6]]
      : [[0.17, 0.25, 0.64, 0.8], [0.53, 0.16, 0.4, 0.6], [0.82, 0.3, 0.86, 0.94], [0.28, 0.65, 1.05, 1], [0.88, 0.73, 0.52, 0.7], [0.06, 0.58, 0.34, 0.55]];
    this.creatures = layout.map(([x, y, size, depth], index) => {
      const mixed: JellySpecies[] = this.width < 700 ? ['moon', 'crown', 'nettle', 'moon'] : ['moon', 'moon', 'crown', 'nettle', 'crown', 'moon'];
      const species = this.options.species === 'mixed' ? mixed[index] : this.options.species;
      const jelly: Jelly = {
        species, x: x * this.width, y: y * this.height, z: (1 - depth) * 330, size, depth,
        phase: index * 2.29 + 0.4, period: 3.8 + noise(index, 14) * 1.3,
        vx: index % 2 ? -6 : 8, vy: -4, tilt: index % 2 ? -0.18 : 0.2,
        targetX: this.width * (0.12 + noise(index, 21) * 0.76), targetY: this.height * (0.25 + noise(index, 32) * 0.4),
        nextTurn: this.elapsed + 12 + index * 3, turns: 0,
        currentX: 0, currentY: 0,
        mesh: createBellMesh(species, 0, 0, this.elapsed, index * 2.29 + 0.4), tentacles: null,
      };
      this.updateAnatomy(jelly, 0);
      return jelly;
    });
  }

  private schedule(): void {
    if (!this.frame && !this.destroyed && !this.hidden && !this.options.paused) this.frame = requestAnimationFrame(this.tick);
  }

  private readonly tick = (now: number): void => {
    this.frame = 0;
    if (this.destroyed || this.hidden || this.options.paused) return;
    const dt = this.previous ? Math.min((now - this.previous) / 1000, 0.05) : 0;
    this.previous = now;
    const step = dt * this.options.speed;
    this.elapsed += step;
    const paintDue = now - this.lastPaint >= 1000 / 30;
    for (const jelly of this.creatures) {
      this.swim(jelly, step);
      this.updateAnatomy(jelly, step, paintDue);
    }
    for (const ripple of this.ripples) ripple.age += dt;
    this.ripples = this.ripples.filter(ripple => ripple.age < 2.5);
    if (paintDue) { this.paint(); this.lastPaint = now; }
    this.schedule();
  };

  private breath(jelly: Jelly, delay = 0): { stroke: number; thrust: number } {
    const cycle = ((this.elapsed - delay) / jelly.period + jelly.phase / TAU) % 1;
    const p = (cycle + 1) % 1;
    if (p < 0.24) {
      const t = p / 0.24;
      return { stroke: smooth(t), thrust: 6 * t * (1 - t) / (0.24 * jelly.period) };
    }
    return { stroke: 1 - smooth(clamp((p - 0.24) / 0.65, 0, 1)), thrust: 0 };
  }

  private swim(jelly: Jelly, dt: number): void {
    if (!dt) return;
    const r = this.radius(jelly);
    const edge = Math.min(r * 1.2, this.width * 0.3);
    const top = Math.min(r * 0.8 + 120, this.height * 0.32);
    const bottom = Math.max(top + 60, this.height - r * (jelly.species === 'nettle' ? 2.2 : 1.5) - 145);
    if (this.elapsed > jelly.nextTurn) {
      jelly.turns++;
      jelly.targetX = edge + noise(jelly.phase, jelly.turns) * Math.max(1, this.width - edge * 2);
      jelly.targetY = top + noise(jelly.turns, jelly.phase) * Math.max(1, bottom - top);
      jelly.nextTurn = this.elapsed + 14 + noise(jelly.turns, 12) * 9;
    }
    const dx = jelly.targetX - jelly.x;
    const dy = jelly.targetY - jelly.y;
    const distance = Math.max(80, Math.hypot(dx, dy));
    let waterX = dx / distance * 14 + Math.sin(this.elapsed * 0.17 + jelly.phase) * 3;
    let waterY = dy / distance * 12 + Math.cos(this.elapsed * 0.13 + jelly.phase) * 2;
    const spring = (value: number, low: number, high: number) => (clamp(value, low, high) - value) * 0.2;
    waterX += spring(jelly.x, edge, this.width - edge);
    waterY += spring(jelly.y, top, bottom);
    for (const other of this.creatures) {
      if (other === jelly) continue;
      const rx = jelly.x - other.x, ry = jelly.y - other.y;
      const d = Math.max(1, Math.hypot(rx, ry));
      const near = Math.max(0, 1 - d / ((r + this.radius(other)) * 1.25));
      waterX += rx / d * near * 42;
      waterY += ry / d * near * 42;
    }
    const pulse = this.breath(jelly).thrust * 18;
    jelly.currentX = waterX;
    jelly.currentY = waterY;
    const response = 1 - Math.exp(-dt * 0.85);
    jelly.vx += (waterX - jelly.vx) * response + Math.sin(jelly.tilt) * pulse * dt;
    jelly.vy += (waterY - jelly.vy) * response - Math.cos(jelly.tilt) * pulse * dt;
    jelly.x += jelly.vx * dt;
    jelly.y += jelly.vy * dt;
    const tilt = clamp(jelly.vx / 43, -0.4, 0.4) + Math.sin(this.elapsed * 0.17 + jelly.phase) * 0.06;
    jelly.tilt += (tilt - jelly.tilt) * (1 - Math.exp(-dt * 0.55));
  }

  private pose(jelly: Jelly): { toWorld: (p: Vec3) => Vec3; rotate: (p: Vec3) => Vec3 } {
    const elevation = Math.atan2(jelly.y - this.height * 0.5, 1200 + jelly.z);
    const pitch = -0.28 - elevation + Math.sin(this.elapsed * 0.16 + jelly.phase) * 0.11;
    const yaw = jelly.phase + this.elapsed * 0.07;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const cr = Math.cos(jelly.tilt), sr = Math.sin(jelly.tilt), r = this.radius(jelly);
    const rotate = (p: Vec3): Vec3 => {
      const x = p.x * cy + p.z * sy, z = -p.x * sy + p.z * cy;
      const y = p.y * cp - z * sp, zz = p.y * sp + z * cp;
      return { x: x * cr - y * sr, y: x * sr + y * cr, z: zz };
    };
    return { rotate, toWorld: (p: Vec3) => {
      const v = rotate(p);
      return { x: jelly.x + v.x * r, y: jelly.y + v.y * r, z: jelly.z + v.z * r };
    } };
  }

  private updateAnatomy(jelly: Jelly, dt: number, remesh = true): void {
    // Keep rope integration continuous; rebuild the shell only for rendered frames.
    if (remesh) jelly.mesh = createBellMesh(jelly.species, this.breath(jelly).stroke, this.breath(jelly, 0.2).stroke, this.elapsed, jelly.phase);
    const pose = this.pose(jelly);
    if (!jelly.tentacles) jelly.tentacles = new JellyfishTentacles(jelly.mesh.roots, this.radius(jelly), pose.toWorld);
    else jelly.tentacles.update(dt, this.elapsed, jelly.mesh.roots, this.radius(jelly), pose.toWorld, { x: jelly.currentX, y: jelly.currentY, z: 0 });
  }

  private project(p: Vec3): Projected {
    const scale = 1200 / Math.max(600, 1200 + p.z);
    return { x: this.width * 0.5 + (p.x - this.width * 0.5) * scale, y: this.height * 0.5 + (p.y - this.height * 0.5) * scale, z: p.z, scale };
  }

  private paint(): void {
    const ctx = this.context;
    const dark = this.options.theme === 'dark';
    ctx.clearRect(0, 0, this.width, this.height);
    this.density.fill(0); this.colors.fill(0); this.glyphs.fill(0);
    for (let i = 0; i < 90; i++) {
      const x = (noise(i, 2) * this.width + Math.sin(this.elapsed * 0.06 + i) * 12 + this.width) % this.width;
      const y = (noise(i, 8) * this.height - this.elapsed * (1.3 + noise(i, 7)) + this.height * 100) % this.height;
      const twinkle = 0.16 + 0.18 * (0.5 + 0.5 * Math.sin(this.elapsed * 0.7 + i * 2.3)) ** 3;
      this.splat(x, y, twinkle, 0, 0.3, 0, false, i % 13 === 0 ? 42 : 46);
    }
    for (const jelly of [...this.creatures].sort((a, b) => b.z - a.z)) this.drawJelly(jelly);
    for (const ripple of this.ripples) {
      const radius = ripple.age * 80 + 10;
      for (let i = 0; i < 80; i++) this.splat(ripple.x + Math.cos(i * TAU / 80) * radius, ripple.y + Math.sin(i * TAU / 80) * radius * 0.6, 0.18 * (1 - ripple.age / 2.5), 0, 0.4);
    }
    // Blue glass, violet tissue, warm inner ring, pale margins, rose threads.
    const palette = dark
      ? ['95,137,197', '174,194,255', '185,174,255', '255,222,155', '190,232,255', '212,162,220', '240,250,255']
      : ['96,126,152', '58,93,151', '94,79,151', '157,114,43', '57,115,139', '145,83,124', '68,99,139'];
    const levels = 12;
    const batches: number[][] = Array.from({ length: palette.length * levels }, () => []);
    for (let i = 0; i < this.density.length; i++) {
      if (this.density[i] < 0.055) continue;
      batches[this.colors[i] * levels + clamp(Math.floor(this.density[i] * levels), 0, levels - 1)].push(i);
    }
    // One low-resolution light pass keeps bloom cheap and the foreground glyphs crisp.
    const glow = this.glow.getContext('2d');
    if (dark && glow) {
      glow.clearRect(0, 0, this.columns, this.rows);
      for (let color = 1; color < palette.length; color++) {
        glow.fillStyle = `rgb(${palette[color]})`;
        for (let level = 4; level < levels; level++) {
          glow.globalAlpha = ((level - 3) / (levels - 3)) ** 2 * 0.8;
          for (const i of batches[color * levels + level]) glow.fillRect(i % this.columns, Math.floor(i / this.columns), 1, 1);
        }
      }
      glow.globalAlpha = 1;
      ctx.filter = `blur(${this.cellX * 1.3}px)`;
      ctx.globalAlpha = 0.85;
      ctx.drawImage(this.glow, 0, 0, this.columns * this.cellX, this.rows * this.cellY);
      ctx.filter = 'none';
      ctx.globalAlpha = 1;
    }
    ctx.font = `400 ${this.cellX * 0.86}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let color = 0; color < palette.length; color++) for (let level = 0; level < levels; level++) {
      const cells = batches[color * levels + level];
      if (!cells.length) continue;
      if (color > 0 && level >= 4) {
        const alpha = (dark ? 0.045 : 0.025) + ((level - 3) / (levels - 3)) ** 1.3 * (dark ? 0.72 : 0.18);
        ctx.fillStyle = `rgba(${palette[color]},${alpha})`;
        for (const i of cells) ctx.fillRect((i % this.columns) * this.cellX + 0.7, Math.floor(i / this.columns) * this.cellY + 0.7, this.cellX - 1.4, this.cellY - 1.4);
      }
      ctx.fillStyle = `rgba(${palette[color]},${0.2 + level / (levels - 1) * 0.76})`;
      for (const i of cells) {
        const value = this.density[i];
        const grain = noise(i % this.columns, Math.floor(i / this.columns));
        // A few cells change character slowly; the shape never relies on random flicker.
        const shimmer = grain > 0.88 ? (noise(i, Math.floor(this.elapsed * 1.1 + grain)) - 0.5) * 0.12 : 0;
        const glyph = this.glyphs[i] ? String.fromCharCode(this.glyphs[i]) : GLYPHS[clamp(Math.floor((value + (grain - 0.5) * 0.1 + shimmer) * (GLYPHS.length - 1)), 1, GLYPHS.length - 1)];
        ctx.fillText(glyph, (i % this.columns + 0.5) * this.cellX, (Math.floor(i / this.columns) + 0.5) * this.cellY);
      }
    }
  }

  private drawJelly(jelly: Jelly): void {
    const color = jelly.species === 'crown' ? 2 : 1;
    const stroke = this.breath(jelly).stroke;
    const pose = this.pose(jelly);
    this.surfaceDepth.fill(Infinity); this.surfaceTone.fill(0); this.surfaceWarmth.fill(0);
    this.drawStrands(jelly, color, false);
    const vertices: Vertex[] = jelly.mesh.vertices.map(v => {
      const normal = pose.rotate({ x: v.nx, y: v.ny, z: v.nz });
      const facing = Math.abs(normal.z);
      const light = Math.max(0, -normal.x * 0.38 - normal.y * 0.55 - normal.z * 0.74);
      const fresnel = (1 - facing) ** 2;
      const warmth = v.warmth * (normal.z <= 0 ? 1 : 0.22);
      const tone = normal.z <= 0
        ? 0.18 + light * 0.62 + fresnel * 0.18 + v.tone * 0.12 + warmth * (0.17 + stroke * 0.15)
        : 0.085 + light * 0.2 + fresnel * 0.18 + v.tone * 0.05;
      return { ...this.project(pose.toWorld(v)), tone: Math.min(0.98, tone) * (0.8 + jelly.depth * 0.2), warmth };

    });
    const triangles = jelly.mesh.triangles;
    for (let i = 0; i < triangles.length; i += 3) this.triangle(vertices[triangles[i]], vertices[triangles[i + 1]], vertices[triangles[i + 2]]);
    for (let i = 0; i < this.surfaceTone.length; i++) {
      const tone = this.surfaceTone[i];
      if (tone <= 0) continue;
      this.density[i] = Math.max(this.density[i] * 0.55, tone);
      this.colors[i] = this.surfaceWarmth[i] > 0.35 ? 3 : color;
      this.glyphs[i] = 0;
      const spark = noise(i, Math.floor(this.elapsed * 0.65));
      if (tone > 0.42 && spark > 0.997) {
        this.colors[i] = 6; this.glyphs[i] = spark > 0.9985 ? 42 : 43;
        this.density[i] = Math.max(this.density[i], 0.86);
      }
    }
    const rim = jelly.mesh.rim.map(p => this.project(pose.toWorld(p)));
    for (let i = 0; i < rim.length; i++) {
      const a = rim[i], b = rim[(i + 1) % rim.length];
      this.line(a, b, a.z < jelly.z ? 0.72 : 0.22, 4, 1, true, false);
    }
    const outerRim = jelly.mesh.outerRim.map(p => this.project(pose.toWorld(p)));
    for (let i = 0; i < outerRim.length; i++) {
      const a = outerRim[i], b = outerRim[(i + 1) % outerRim.length];
      this.line(a, b, a.z < jelly.z ? 0.7 : 0.18, 4, 1.2, false, false);
    }
    this.drawStrands(jelly, color, true);
  }

  private triangle(a: Vertex, b: Vertex, c: Vertex): void {
    const left = clamp(Math.floor(Math.min(a.x, b.x, c.x) / this.cellX), 0, this.columns - 1);
    const right = clamp(Math.ceil(Math.max(a.x, b.x, c.x) / this.cellX), 0, this.columns - 1);
    const top = clamp(Math.floor(Math.min(a.y, b.y, c.y) / this.cellY), 0, this.rows - 1);
    const bottom = clamp(Math.ceil(Math.max(a.y, b.y, c.y) / this.cellY), 0, this.rows - 1);
    const denom = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y);
    if (Math.abs(denom) < 0.001) return;
    for (let row = top; row <= bottom; row++) for (let column = left; column <= right; column++) {
      const x = (column + 0.5) * this.cellX, y = (row + 0.5) * this.cellY;
      const u = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / denom;
      const v = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / denom;
      const w = 1 - u - v;
      if (u < 0 || v < 0 || w < 0) continue;
      const z = a.z * u + b.z * v + c.z * w;
      const i = row * this.columns + column;
      if (z >= this.surfaceDepth[i]) continue;
      this.surfaceDepth[i] = z;
      this.surfaceTone[i] = a.tone * u + b.tone * v + c.tone * w;
      this.surfaceWarmth[i] = a.warmth * u + b.warmth * v + c.warmth * w;
    }
  }

  private drawStrands(jelly: Jelly, color: number, frontOnly: boolean): void {
    for (const strand of jelly.tentacles?.strands ?? []) {
      const points = strand.points.map(p => this.project(p));
      for (let i = 1; i < points.length; i++) {
        const t = i / (points.length - 1);
        const arm = strand.kind === 'arm';
        const wave = 0.78 + Math.sin(t * 24 + strand.seed * 7 + this.elapsed * 0.6) * 0.22;
        const radius = arm ? Math.max(0.6, strand.radius * 1.6 * points[i].scale * (1 - t * 0.66) * wave) : 0.6;
        const strandColor = arm ? strand.seed > 0.8 ? 4 : 2 : strand.seed < 0.33 ? 5 : color;
        const tone = arm ? (0.72 + strand.seed * 0.12) * (1 - t * 0.48) : 0.4 * (1 - t * 0.58);
        this.line(points[i - 1], points[i], tone * (0.65 + jelly.depth * 0.35), strandColor, radius, frontOnly, false);
      }
    }
  }

  private line(a: Projected, b: Projected, tone: number, color: number, radius: number, frontOnly: boolean, directional: boolean): void {
    const dx = b.x - a.x, dy = b.y - a.y;
    const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / (this.cellX * 0.38)));
    const angle = Math.atan2(dy, dx);
    const glyph = directional ? (Math.abs(dy) > Math.abs(dx) * 2 ? 124 : Math.abs(dx) > Math.abs(dy) * 2 ? 45 : angle > 0 ? 92 : 47) : 0;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      this.splat(a.x + dx * t, a.y + dy * t, tone, color, radius, a.z + (b.z - a.z) * t, frontOnly, glyph);
    }
  }

  private splat(x: number, y: number, tone: number, color: number, radius: number, z = -Infinity, frontOnly = false, glyph = 0): void {
    const size = Math.max(radius, this.cellX * 0.62);
    const left = Math.max(0, Math.floor((x - size) / this.cellX));
    const right = Math.min(this.columns - 1, Math.ceil((x + size) / this.cellX));
    const top = Math.max(0, Math.floor((y - size) / this.cellY));
    const bottom = Math.min(this.rows - 1, Math.ceil((y + size) / this.cellY));
    for (let row = top; row <= bottom; row++) for (let column = left; column <= right; column++) {
      const dx = (column + 0.5) * this.cellX - x, dy = (row + 0.5) * this.cellY - y;
      const d = Math.hypot(dx, dy);
      if (d > size + this.cellX * 0.2) continue;
      const index = row * this.columns + column;
      if (frontOnly && z > this.surfaceDepth[index] + 3) continue;
      const value = tone * Math.exp(-d * d / (size * size) * 1.15);
      if (value <= this.density[index]) continue;
      this.density[index] = value; this.colors[index] = color; this.glyphs[index] = glyph;
    }
  }
}
