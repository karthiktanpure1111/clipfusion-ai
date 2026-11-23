"use client";

import { useEffect } from "react";

const NON_TEXT_INPUTS = new Set(["range", "checkbox", "radio", "color", "file", "button", "submit"]);

/** True when key presses belong to a text field / select (sliders and toggles do not count). */
export function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  if (el instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(el.type);
  return el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

/** True when Space activates the focused control itself (buttons, switches, tabs, links...). */
export function isActivatable(el: EventTarget | null): boolean {
  if (!(el instanceof Element)) return false;
  if (el instanceof HTMLInputElement) return el.type !== "range";
  return !!el.closest("button,a,summary,[role=switch],[role=checkbox],[role=radio],[role=tab],[role=option],[role=menuitem]");
}

/**
 * Editor page guards: Ctrl/Cmd+S saves when dirty and closing/reloading the tab with unsaved
 * edits asks for confirmation. ``onKey`` receives every other non-typing key press.
 */
export function useEditorGuards({ dirty, onSave, onKey }: { dirty: boolean; onSave: () => void; onKey?: (e: KeyboardEvent) => void }): void {
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    const keydown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (dirty) onSave();
        return;
      }
      if (!isTypingTarget(e.target)) onKey?.(e);
    };
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("keydown", keydown);
    };
  }, [dirty, onSave, onKey]);
}
