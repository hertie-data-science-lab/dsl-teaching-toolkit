// The Files panel's two columns and the bar between them (plan 2026-11-04, decision 8): drag the
// bar to give either column 25-75% of the width, double-click or Home to reset, Left/Right arrows
// to step. The ratio sits on the wrapper as `--lf`/`--rf`, so the From / To row above the columns
// follows it. Below 1060px the columns stack and the bar hides (CSS).

import type { ComponentChildren, RefObject } from 'preact';
import { useRef, useState } from 'preact/hooks';

export const MIN_RATIO = 0.25;
export const MAX_RATIO = 0.75;
const STEP = 0.05;

/** A ratio held inside 25-75%. */
export const clampRatio = (r: number) => Math.round(Math.max(MIN_RATIO, Math.min(MAX_RATIO, r)) * 1000) / 1000;

export function Splitter({ ratio, onRatio, initial, box }: { ratio: number; onRatio: (r: number) => void; initial: number; box: RefObject<HTMLElement> }) {
  const [dragging, setDragging] = useState(false);
  const down = (e: PointerEvent) => {
    e.preventDefault();
    const bar = e.currentTarget as HTMLElement;
    bar.setPointerCapture?.(e.pointerId);
    setDragging(true);
    const r = box.current?.getBoundingClientRect();
    const move = (ev: PointerEvent) => r && r.width && onRatio(clampRatio((ev.clientX - r.left) / r.width));
    const up = () => {
      setDragging(false);
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', up);
      bar.removeEventListener('pointercancel', up);
    };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', up);
    bar.addEventListener('pointercancel', up);
  };
  const key = (e: KeyboardEvent) => {
    const next = e.key === 'ArrowLeft' ? ratio - STEP : e.key === 'ArrowRight' ? ratio + STEP : e.key === 'Home' ? initial : null;
    if (next === null) return;
    e.preventDefault();
    onRatio(clampRatio(next));
  };
  return (
    <div class={`splitter${dragging ? ' dragging' : ''}`} role="separator" aria-orientation="vertical" aria-label="Resize the columns"
      aria-valuemin={MIN_RATIO * 100} aria-valuemax={MAX_RATIO * 100} aria-valuenow={Math.round(ratio * 100)}
      tabIndex={0} title="Drag to resize; double-click to reset" onPointerDown={down} onDblClick={() => onRatio(initial)} onKeyDown={key} />
  );
}

/**
 * `left` and `right` side by side with the bar between, `heads` (the From / To row) above them in
 * the same columns. `label` names the left column (a `nav`, as `Split` has it).
 */
export function SplitPanes({ left, right, heads, label, initial = 0.55 }: { left: ComponentChildren; right: ComponentChildren; heads?: [ComponentChildren, ComponentChildren]; label: string; initial?: number }) {
  const [ratio, setRatio] = useState(clampRatio(initial));
  const box = useRef<HTMLDivElement>(null);
  const fr = (x: number) => `${+(x * 100).toFixed(1)}fr`;
  const style = { '--lf': fr(ratio), '--rf': fr(1 - ratio) };
  return (
    <div class="split-panes" style={style}>
      {heads ? (
        <div class="files-split split-heads">
          <div class="rh-row">{heads[0]}</div>
          <span class="split-gap" />
          <div class="rh-row">{heads[1]}</div>
        </div>
      ) : null}
      <div class="materials-split files-split" ref={box}>
        <nav class="materials-tree" aria-label={label}>{left}</nav>
        <Splitter ratio={ratio} onRatio={setRatio} initial={clampRatio(initial)} box={box} />
        <div class="materials-file">{right}</div>
      </div>
    </div>
  );
}
