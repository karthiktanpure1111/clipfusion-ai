/**
 * Exact TypeScript port of backend/app/modules/framing/layout.py: FramingConfig ->
 * CompiledLayout, default configs per mode, plus deep-merging of FramingSuggestion patches.
 * All normalised numbers produced here are rounded to 6 decimals like the backend.
 */
import {
  FULL_FRAME,
  type CompiledLayer,
  type CompiledLayout,
  type FacecamGameplayConfig,
  type FramingConfig,
  type FramingMode,
  type PipConfig,
  type Rect,
} from "./types";

export const FRAMING_MODES: FramingMode[] = ["smart", "manual", "facecam_gameplay", "pip", "full_frame"];

export function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}

/** Rounded, clamped normalised rect. */
export function makeRect(x: number, y: number, w: number, h: number): Rect {
  w = Math.min(Math.max(w, 1e-4), 1.0);
  h = Math.min(Math.max(h, 1e-4), 1.0);
  x = Math.min(Math.max(x, 0.0), 1.0 - w);
  y = Math.min(Math.max(y, 0.0), 1.0 - h);
  return { x: round6(x), y: round6(y), w: round6(w), h: round6(h) };
}

function checkSource(sourceW: number, sourceH: number): void {
  if (!(sourceW > 0) || !(sourceH > 0)) throw new Error("Source dimensions must be positive");
}

/**
 * Normalised source rect whose PIXEL aspect (w/h) is ``aspect``, at most ``height`` of the
 * source tall (shrunk to fit the width), centred on (cx, cy) and clamped inside.
 */
export function aspectRect(
  sourceW: number,
  sourceH: number,
  aspect: number,
  opts: { cx?: number; cy?: number; height?: number } = {},
): Rect {
  checkSource(sourceW, sourceH);
  const { cx = 0.5, cy = 0.5, height = 1.0 } = opts;
  let hPx = sourceH * Math.min(Math.max(height, 1e-4), 1.0);
  let wPx = hPx * aspect;
  if (wPx > sourceW) {
    wPx = sourceW;
    hPx = wPx / aspect;
  }
  const w = wPx / sourceW;
  const h = hPx / sourceH;
  return makeRect(cx - w / 2, cy - h / 2, w, h);
}

/** Largest centred source rect with the given pixel aspect. */
export function centeredCrop(sourceW: number, sourceH: number, aspect: number): Rect {
  return aspectRect(sourceW, sourceH, aspect);
}

/** ``[facecamDest, gameplayDest]`` canvas rects of the stacked layout. */
export function panelDests(fg: FacecamGameplayConfig, canvasH: number): [Rect, Rect] {
  const split = fg.split;
  const gap = Math.min(fg.gap / canvasH, Math.max(0.0, 1.0 - split - 0.05));
  const rest = 1.0 - split - gap;
  if (fg.facecam_position === "top") return [makeRect(0, 0, 1, split), makeRect(0, split + gap, 1, rest)];
  return [makeRect(0, 1.0 - split, 1, split), makeRect(0, 0, 1, rest)];
}

/** Pixel aspect ratios ``[facecamPanel, gameplayPanel]`` of the stacked layout. */
export function panelAspects(config: FramingConfig, fg?: FacecamGameplayConfig): [number, number] {
  const f = fg ?? config.facecam_gameplay;
  const [fd, gd] = panelDests(f, config.output_height);
  const W = config.output_width;
  const H = config.output_height;
  return [(fd.w * W) / (fd.h * H), (gd.w * W) / (gd.h * H)];
}

/** Dest rect of the full_frame picture. */
export function fullFrameDest(config: FramingConfig, sourceW: number, sourceH: number): Rect {
  const ff = config.full_frame;
  const W = config.output_width;
  const H = config.output_height;
  const h = Math.min(1.0, (ff.scale * W * sourceH) / sourceW / H);
  return makeRect((1.0 - ff.scale) / 2, ff.vertical_position * (1.0 - h), ff.scale, h);
}

/** The schema defaults of ``FramingConfig`` (before source-specific sizing). */
export function baseFramingConfig(mode: FramingMode = "full_frame"): FramingConfig {
  return {
    version: 1,
    mode,
    output_width: 1080,
    output_height: 1920,
    background: { type: "blur", color: "#0F0F12", blur: 40.0, dim: 0.35, image_asset_id: null },
    smart: {
      primary_track_id: null,
      auto_switch: false,
      zoom: 1.0,
      smoothing: 0.85,
      deadzone: 0.08,
      headroom: 0.12,
      min_shot_seconds: 2.5,
      keyframes: [],
    },
    manual: { crop: { x: 0.341, y: 0.0, w: 0.3164, h: 1.0 }, fit: "cover", lock_aspect: true, keyframes: [] },
    facecam_gameplay: {
      facecam: { x: 0.0, y: 0.0, w: 0.25, h: 0.3 },
      gameplay: { x: 0.2, y: 0.0, w: 0.6, h: 1.0 },
      facecam_position: "top",
      split: 0.35,
      gap: 0.0,
      facecam_fit: "cover",
      gameplay_fit: "cover",
    },
    pip: {
      main: { x: 0, y: 0, w: 1, h: 1 },
      main_fit: "contain",
      overlay_source: { x: 0.0, y: 0.0, w: 0.25, h: 0.3 },
      overlay_dest: { x: 0.06, y: 0.06, w: 0.42, h: 0.24 },
      overlay_fit: "cover",
      overlay_radius: 28.0,
      overlay_border_width: 4.0,
      overlay_border_color: "#FFFFFF",
      overlay_shadow: true,
    },
    full_frame: { scale: 1.0, vertical_position: 0.5 },
  };
}

/** Sensible starting config for ``mode`` sized for the source (see layout.py). */
export function defaultFramingConfig(sourceW: number, sourceH: number, mode: FramingMode = "full_frame"): FramingConfig {
  checkSource(sourceW, sourceH);
  if (!FRAMING_MODES.includes(mode)) throw new Error(`Unknown framing mode '${mode}'`);
  const cfg = baseFramingConfig(mode);
  const outAspect = cfg.output_width / cfg.output_height;
  cfg.manual = { ...cfg.manual, crop: centeredCrop(sourceW, sourceH, outAspect) };
  const [faceAspect, gameAspect] = panelAspects(cfg);
  cfg.facecam_gameplay = {
    ...cfg.facecam_gameplay,
    facecam: aspectRect(sourceW, sourceH, faceAspect, { cx: 0, cy: 0, height: 0.3 }),
    gameplay: aspectRect(sourceW, sourceH, gameAspect),
  };
  const pd = cfg.pip.overlay_dest;
  const overlayAspect = (pd.w * cfg.output_width) / (pd.h * cfg.output_height);
  cfg.pip = {
    ...cfg.pip,
    overlay_source: aspectRect(sourceW, sourceH, overlayAspect, { cx: 0, cy: 0, height: 0.3 }),
  };
  return cfg;
}

function layer(partial: Partial<CompiledLayer> & Pick<CompiledLayer, "id">): CompiledLayer {
  return {
    role: "main",
    source: FULL_FRAME,
    keyframes: [],
    dest: FULL_FRAME,
    fit: "cover",
    radius: 0.0,
    border_width: 0.0,
    border_color: "#FFFFFF",
    shadow: false,
    z: 0,
    ...partial,
  };
}

function pipLayers(pip: PipConfig): CompiledLayer[] {
  return [
    layer({ id: "main", role: "main", source: pip.main, dest: FULL_FRAME, fit: pip.main_fit, z: 0 }),
    layer({
      id: "overlay",
      role: "face",
      source: pip.overlay_source,
      dest: pip.overlay_dest,
      fit: pip.overlay_fit,
      radius: pip.overlay_radius,
      border_width: pip.overlay_border_width,
      border_color: pip.overlay_border_color,
      shadow: pip.overlay_shadow,
      z: 1,
    }),
  ];
}

/** Compile ``config`` into drawable layers for a ``sourceW`` x ``sourceH`` source. */
export function compileLayout(config: FramingConfig, sourceW: number, sourceH: number): CompiledLayout {
  checkSource(sourceW, sourceH);
  const mode = config.mode;
  let layers: CompiledLayer[];
  if (mode === "smart") {
    const src = centeredCrop(sourceW, sourceH, config.output_width / config.output_height);
    layers = [layer({ id: "main", source: src, keyframes: [...config.smart.keyframes], fit: "cover" })];
  } else if (mode === "manual") {
    const m = config.manual;
    layers = [layer({ id: "main", source: m.crop, keyframes: [...m.keyframes], fit: m.fit })];
  } else if (mode === "facecam_gameplay") {
    const fg = config.facecam_gameplay;
    const [faceDest, gameDest] = panelDests(fg, config.output_height);
    layers = [
      layer({ id: "facecam", role: "face", source: fg.facecam, dest: faceDest, fit: fg.facecam_fit, z: 0 }),
      layer({ id: "gameplay", role: "content", source: fg.gameplay, dest: gameDest, fit: fg.gameplay_fit, z: 1 }),
    ];
  } else if (mode === "pip") {
    layers = pipLayers(config.pip);
  } else {
    layers = [layer({ id: "main", source: FULL_FRAME, dest: fullFrameDest(config, sourceW, sourceH), fit: "contain" })];
  }
  let backgroundSource: Rect = FULL_FRAME;
  if (mode !== "full_frame") {
    const bg = layers.find((l) => l.id === "main" || l.id === "gameplay");
    if (bg) backgroundSource = bg.source;
  }
  return {
    width: config.output_width,
    height: config.output_height,
    background: config.background,
    background_source: backgroundSource,
    layers,
  };
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Recursive merge: plain objects merge key by key; arrays, scalars and null replace. */
export function deepMerge<T>(base: T, patch: Record<string, unknown>): T {
  if (!isPlainObject(base)) return patch as T;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(patch)) {
    const current = out[key];
    out[key] = isPlainObject(value) && isPlainObject(current) ? deepMerge(current, value) : value;
  }
  return out as T;
}

/**
 * Apply a ``FramingSuggestion.config_patch``. A patch that changes ``manual.crop`` resets
 * ``manual.keyframes`` (the animated crop no longer matches); ``smart.keyframes = []`` is kept
 * as-is: it asks the server to recompute the smart camera path on the next check/save.
 */
export function applyConfigPatch(config: FramingConfig, patch: Record<string, unknown>): FramingConfig {
  const merged = deepMerge(config, patch);
  const manualPatch = patch.manual;
  if (isPlainObject(manualPatch) && "crop" in manualPatch && !("keyframes" in manualPatch)) {
    merged.manual = { ...merged.manual, keyframes: [] };
  }
  return merged;
}
