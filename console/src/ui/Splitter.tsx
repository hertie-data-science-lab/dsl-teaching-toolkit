// The Files panel's two columns and the bar between them (plan 2026-11-04, decision 8): drag the
// bar to give either column 25-75% of the width, double-click or Home to reset, Left/Right arrows
// to step (`useDragHandle`). The ratio sits on the wrapper as `--lf`/`--rf`, so the From / To row
// above the columns follows it. The columns are the viewer's `Split` with the bar between. Below
// 1060px the columns stack and the bar hides (CSS).

import type { ComponentChildren } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { useDragHandle } from './dragHandle';
import { Split } from './Viewer';

export const MIN_RATIO = 0.25;
export const MAX_RATIO = 0.75;
const STEP = 0.05;

/** A ratio held inside 25-75%. */
export const clampRatio = (r: number) => Math.round(Math.max(MIN_RATIO, Math.min(MAX_RATIO, r)) * 1000) / 1000;

/**
 * `left` and `right` side by side with the bar between, `heads` (the From / To row) above them in
 * the same columns. `label` names the left column (a `nav`, as `Split` has it).
 */
export function SplitPanes({ left, right, heads, label, initial = 0.55 }: { left: ComponentChildren; right: ComponentChildren; heads?: [ComponentChildren, ComponentChildren]; label: string; initial?: number }) {
  const start = clampRatio(initial);
  const [ratio, setRatio] = useState(start);
  const box = useRef<HTMLDivElement>(null);
  const bar = useDragHandle({
    start: () => {
      const r = box.current?.getBoundingClientRect();
      return { move: (ev) => r && r.width && setRatio(clampRatio((ev.clientX - r.left) / r.width)) };
    },
    step: (dir) => setRatio(clampRatio(ratio + dir * STEP)),
    reset: () => setRatio(start),
    home: true,
  });
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
      <Split label={label} side={left} box={box} bar={
        <div class={`splitter${bar.dragging ? ' dragging' : ''}`} aria-label="Resize the columns" {...bar.props}
          aria-valuemin={MIN_RATIO * 100} aria-valuemax={MAX_RATIO * 100} aria-valuenow={Math.round(ratio * 100)} />
      }>{right}</Split>
    </div>
  );
}
