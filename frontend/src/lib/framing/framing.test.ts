import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { fitRects, rectAt, sourceToCanvas, visibleSourceRect } from "./geometry";
import { applyConfigPatch, compileLayout, defaultFramingConfig } from "./layout";
import type { CompiledLayer, FramingConfig, FramingMode, PxRect, Rect } from "./types";

interface Fixtures {
  tolerance: number;
  default_framing_config: { source_w: number; source_h: number; mode: FramingMode; expected: unknown }[];
  compile_layout: { name: string; source_w: number; source_h: number; config: FramingConfig; expected: unknown }[];
  rect_at: { layer: CompiledLayer; cases: { t: number; expected: Rect }[] }[];
  fit_rects: { src: PxRect; dst: PxRect; fit: string; expected: { crop: PxRect; draw: PxRect } }[];
  visible_source_rect_px: (Geo & { expected: Rect })[];
  source_to_canvas: (Geo & { face: Rect; expected: Rect | null })[];
}
interface Geo {
  layer: CompiledLayer;
  t: number;
  source_w: number;
  source_h: number;
  canvas_w: number;
  canvas_h: number;
}

const fx: Fixtures = JSON.parse(
  readFileSync(resolve(process.cwd(), "../shared/fixtures/framing_cases.json"), "utf8"),
);
const TOL = fx.tolerance ?? 1e-6;

/** Deep equality with a numeric tolerance (handles tuples vs arrays and nulls). */
function expectClose(actual: unknown, expected: unknown, path = "$"): void {
  if (typeof expected === "number") {
    expect(typeof actual, path).toBe("number");
    expect(Math.abs((actual as number) - expected), `${path}: ${actual} vs ${expected}`).toBeLessThanOrEqual(TOL + 1e-12);
    return;
  }
  if (Array.isArray(expected)) {
    expect(Array.isArray(actual), path).toBe(true);
    expect((actual as unknown[]).length, path).toBe(expected.length);
    expected.forEach((e, i) => expectClose((actual as unknown[])[i], e, `${path}[${i}]`));
    return;
  }
  if (expected && typeof expected === "object") {
    expect(actual && typeof actual === "object", path).toBeTruthy();
    for (const [k, v] of Object.entries(expected)) expectClose((actual as Record<string, unknown>)[k], v, `${path}.${k}`);
    return;
  }
  expect(actual, path).toEqual(expected);
}

describe("framing parity with backend fixtures", () => {
  it("defaultFramingConfig", () => {
    for (const c of fx.default_framing_config) {
      expectClose(defaultFramingConfig(c.source_w, c.source_h, c.mode), c.expected, `${c.source_w}x${c.source_h}/${c.mode}`);
    }
  });

  it("compileLayout", () => {
    for (const c of fx.compile_layout) {
      expectClose(compileLayout(c.config, c.source_w, c.source_h), c.expected, `${c.name}@${c.source_w}x${c.source_h}`);
    }
  });

  it("rectAt / interpolateKeyframes (incl. cuts)", () => {
    for (const group of fx.rect_at) {
      for (const c of group.cases) expectClose(rectAt(group.layer, c.t), c.expected, `t=${c.t}`);
    }
  });

  it("fitRects", () => {
    for (const c of fx.fit_rects) {
      const [crop, draw] = fitRects(c.src, c.dst, c.fit);
      expectClose({ crop, draw }, c.expected);
    }
  });

  it("visibleSourceRect", () => {
    for (const c of fx.visible_source_rect_px) {
      const r = visibleSourceRect(c.layer, c.t, c.source_w, c.source_h, c.canvas_w, c.canvas_h);
      expectClose(r, c.expected);
    }
  });

  it("sourceToCanvas", () => {
    for (const c of fx.source_to_canvas) {
      const r = sourceToCanvas(c.face, c.layer, c.t, c.source_w, c.source_h, c.canvas_w, c.canvas_h);
      if (c.expected === null) expect(r).toBeNull();
      else expectClose(r, c.expected);
    }
  });
});

describe("applyConfigPatch", () => {
  it("deep merges and resets manual keyframes when the crop changes", () => {
    const base = defaultFramingConfig(1920, 1080, "manual");
    base.manual.keyframes = [{ t: 0, rect: base.manual.crop, cut: false }];
    const next = applyConfigPatch(base, { manual: { crop: { x: 0.1, y: 0, w: 0.3, h: 1 } } });
    expect(next.manual.crop.x).toBe(0.1);
    expect(next.manual.keyframes).toEqual([]);
    expect(next.manual.fit).toBe("cover");
    expect(base.manual.keyframes.length).toBe(1);
  });

  it("keeps smart keyframes = [] (server recomputes) and replaces scalars", () => {
    const base = defaultFramingConfig(1920, 1080, "smart");
    base.smart.keyframes = [{ t: 0, rect: { x: 0, y: 0, w: 0.3, h: 1 }, cut: false }];
    const next = applyConfigPatch(base, { mode: "smart", smart: { primary_track_id: 2, keyframes: [] } });
    expect(next.smart.primary_track_id).toBe(2);
    expect(next.smart.keyframes).toEqual([]);
    expect(next.smart.zoom).toBe(1);
  });
});
