"use client";

/**
 * Framing editor state: the working FramingConfig, saved baseline (dirty tracking), the live
 * crop-protection dry run (debounced POST /framing/check on every change) and the smart
 * camera path computed by the server (used by the preview while keyframes are recomputed).
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { checkFraming, errorMessage, useSaveFraming, type FramingCheckOut, type FramingOut } from "@/lib/api/editor";
import { compileLayout, applyConfigPatch } from "@/lib/framing/layout";
import type { FramingConfig, FramingWarning, Keyframe, SmartCropConfig } from "@/lib/framing/types";

const CHECK_DEBOUNCE_MS = 400;

const APPLY_KEY = "clipfusion.framing.applyToVideo";

/** "Apply to all clips of this video" (on unless switched off; remembered per browser). */
export function readApplyToVideo(): boolean {
  try {
    return window.localStorage.getItem(APPLY_KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeApplyToVideo(on: boolean): void {
  try {
    window.localStorage.setItem(APPLY_KEY, on ? "1" : "0");
  } catch {
    /* private mode: keep the default */
  }
}

export function useFramingEditor(clipId: string, data: FramingOut) {
  const [config, setConfig] = useState<FramingConfig>(data.config);
  const [savedJson, setSavedJson] = useState(() => JSON.stringify(data.config));
  const [serverKeyframes, setServerKeyframes] = useState<Keyframe[]>(
    data.config.smart.keyframes.length ? data.config.smart.keyframes : (data.compiled.layers[0]?.keyframes ?? []),
  );
  const [check, setCheck] = useState<{ json: string; result: FramingCheckOut } | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const save = useSaveFraming(clipId);

  const json = useMemo(() => JSON.stringify(config), [config]);
  const dirty = json !== savedJson;

  // Debounced live dry run of the crop protection.
  useEffect(() => {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      setChecking(true);
      checkFraming(clipId, JSON.parse(json) as FramingConfig, ctrl.signal)
        .then((result) => {
          setCheck({ json, result });
          setCheckError(null);
          if (result.config.mode === "smart" && result.config.smart.keyframes.length) setServerKeyframes(result.config.smart.keyframes);
        })
        .catch((e: unknown) => {
          if (!ctrl.signal.aborted) setCheckError(errorMessage(e));
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setChecking(false);
        });
    }, CHECK_DEBOUNCE_MS);
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [clipId, json]);

  /** Config used by the live preview (server smart path while local keyframes are empty). */
  const previewConfig = useMemo<FramingConfig>(() => {
    if (config.mode !== "smart" || config.smart.keyframes.length || !serverKeyframes.length) return config;
    return { ...config, smart: { ...config.smart, keyframes: serverKeyframes } };
  }, [config, serverKeyframes]);

  const compiled = useMemo(() => compileLayout(previewConfig, data.source.width, data.source.height), [previewConfig, data.source.width, data.source.height]);

  const update = useCallback((fn: (c: FramingConfig) => FramingConfig) => setConfig((c) => fn(c)), []);

  /** Smart settings changed -> keyframes cleared so the server recomputes the camera path. */
  const updateSmart = useCallback((patch: Partial<SmartCropConfig>) => setConfig((c) => ({ ...c, smart: { ...c.smart, ...patch, keyframes: [] } })), []);

  const applyPatch = useCallback((patch: Record<string, unknown>) => setConfig((c) => applyConfigPatch(c, patch)), []);

  const replace = useCallback((next: FramingConfig) => setConfig(next), []);

  const saveNow = useCallback(async () => {
    const sent = config;
    const sentJson = json;
    const out = await save.mutateAsync({ config: sent, applyToVideo: readApplyToVideo() });
    setConfig((cur) => (JSON.stringify(cur) === sentJson ? out.config : cur));
    setSavedJson(JSON.stringify(out.config));
    if (out.config.smart.keyframes.length) setServerKeyframes(out.config.smart.keyframes);
    return out;
  }, [config, json, save]);

  const resetToSaved = useCallback(() => setConfig(JSON.parse(savedJson) as FramingConfig), [savedJson]);

  const fresh = check?.json === json;
  const warnings: FramingWarning[] = fresh ? check.result.warnings : (check?.result.warnings ?? (dirty ? [] : data.warnings));

  return {
    config,
    previewConfig,
    compiled,
    dirty,
    update,
    updateSmart,
    applyPatch,
    replace,
    save: saveNow,
    saving: save.isPending,
    resetToSaved,
    warnings,
    warningsFresh: fresh,
    checking: checking || (!fresh && check !== null),
    checkError,
  };
}

export type FramingEditorState = ReturnType<typeof useFramingEditor>;
