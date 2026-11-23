/**
 * Framing contract types — mirror backend/app/schemas/framing.py exactly (field names and
 * defaults). See that module's docstring for the normative coordinate systems.
 */

export type FramingMode = "smart" | "manual" | "facecam_gameplay" | "pip" | "full_frame";
export type Fit = "cover" | "contain";

/** Normalised rect (source- or canvas-relative, 0..1). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Pixel rect tuple ``[x, y, w, h]``. */
export type PxRect = [number, number, number, number];

export interface Keyframe {
  t: number;
  rect: Rect;
  cut: boolean;
}

export interface Background {
  type: "blur" | "color" | "image";
  color: string;
  blur: number;
  dim: number;
  image_asset_id: string | null;
}

export interface SmartCropConfig {
  primary_track_id: number | null;
  auto_switch: boolean;
  zoom: number;
  smoothing: number;
  deadzone: number;
  headroom: number;
  min_shot_seconds: number;
  keyframes: Keyframe[];
}

export interface ManualCropConfig {
  crop: Rect;
  fit: Fit;
  lock_aspect: boolean;
  keyframes: Keyframe[];
}

export interface FacecamGameplayConfig {
  facecam: Rect;
  gameplay: Rect;
  facecam_position: "top" | "bottom";
  split: number;
  gap: number;
  facecam_fit: Fit;
  gameplay_fit: Fit;
}

export interface PipConfig {
  main: Rect;
  main_fit: Fit;
  overlay_source: Rect;
  overlay_dest: Rect;
  overlay_fit: Fit;
  overlay_radius: number;
  overlay_border_width: number;
  overlay_border_color: string;
  overlay_shadow: boolean;
}

export interface FullFrameConfig {
  scale: number;
  vertical_position: number;
}

export interface FramingConfig {
  version: number;
  mode: FramingMode;
  output_width: number;
  output_height: number;
  background: Background;
  smart: SmartCropConfig;
  manual: ManualCropConfig;
  facecam_gameplay: FacecamGameplayConfig;
  pip: PipConfig;
  full_frame: FullFrameConfig;
}

export interface CompiledLayer {
  id: string;
  role: "main" | "face" | "content";
  source: Rect;
  keyframes: Keyframe[];
  dest: Rect;
  fit: Fit;
  radius: number;
  border_width: number;
  border_color: string;
  shadow: boolean;
  z: number;
}

export interface CompiledLayout {
  width: number;
  height: number;
  background: Background;
  background_source: Rect;
  layers: CompiledLayer[];
}

export interface FaceBox {
  t: number;
  rect: Rect;
  score: number;
}

export interface FaceTrack {
  id: number;
  label: string | null;
  boxes: FaceBox[];
  coverage: number;
  mean_area: number;
  mean_center: [number, number];
  prominence: number;
  is_primary: boolean;
  is_facecam: boolean;
  facecam_rect: Rect | null;
  speaking_score: number;
}

export interface ActivityGrid {
  cols: number;
  rows: number;
  values: number[];
}

export interface SpeakerSegment {
  start: number;
  end: number;
  track_id: number;
}

export interface ClipVisionAnalysis {
  version: number;
  detector: string;
  sample_fps: number;
  source_width: number;
  source_height: number;
  clip_start: number;
  clip_end: number;
  tracks: FaceTrack[];
  activity: ActivityGrid | null;
  speaker_timeline: SpeakerSegment[];
  layout_hint: "single_speaker" | "multi_speaker" | "facecam_gameplay" | "no_faces" | "screen_content";
  suggested_mode: FramingMode;
}

export type WarningCode =
  | "face_outside"
  | "head_cut"
  | "streamer_hidden"
  | "content_outside"
  | "subtitle_overlap_face"
  | "no_faces_detected"
  | "vision_missing"
  | "upscale_quality";

export interface FramingSuggestion {
  action: "switch_mode" | "set_config" | "expand_crop" | "move_crop" | "enable_auto_switch";
  label: string;
  config_patch: Record<string, unknown>;
}

export interface FramingWarning {
  code: WarningCode;
  severity: "info" | "warning" | "critical";
  message: string;
  track_id: number | null;
  visible_fraction: number | null;
  affected_fraction: number | null;
  time_ranges: [number, number][];
  suggestions: FramingSuggestion[];
}

export const FULL_FRAME: Rect = Object.freeze({ x: 0, y: 0, w: 1, h: 1 }) as Rect;

/** Warnings that must be acknowledged (or fixed) before a render is allowed. */
export function blockingWarnings(warnings: FramingWarning[]): FramingWarning[] {
  return warnings.filter((w) => w.severity === "warning" || w.severity === "critical");
}
