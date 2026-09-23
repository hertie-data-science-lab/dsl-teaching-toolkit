// S13 the operation panel: a non-modal drawer that minimises to a bar, and the buttons
// that open it. Preview is the filled button; a gated op's verb unlocks only after this
// session previewed it. The outcome sentence is the engine's summary, verbatim.

import { useEffect, useRef } from 'preact/hooks';
import { useEnv } from '../env';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import type { Outcome } from '../model/types';
import { Check, Eye, Fail, Skip } from '../ui/icons';
import { Prop, runUrl } from '../ui/bits';
import type { Result } from './adapter';
import { modeOf, opSpec } from './registry';
import type { Current, OpDef, OpsSession } from './session';

const TONE: Record<string, string> = { done: 'ok', previewed: 'dry', failed: 'fail', skipped: 'skip', nothing_to_do: 'skip' };

function Mark({ tone }: { tone: string }) {
  return <span class={`outcome-mark ${tone}`}>{tone === 'ok' ? <Check /> : tone === 'dry' ? <Eye /> : tone === 'fail' ? <Fail /> : <Skip />}</span>;
}

function runLink(def: OpDef, runId: number | null | undefined, url?: string) {
  if (!runId) return null;
  const href = url || runUrl(`${def.courseOrg}/.github`, runId);
  return <p class="footnote">Ran in {def.courseOrg}/.github as run #{runId}. <a href={href} target="_blank" rel="noopener">Open run</a></p>;
}

export function OutcomeView({ result, def, url }: { result: Result; def: OpDef; url?: string }) {
  const o: Outcome | null = result.outcome;
  if (!o)
    return (
      <div class="outcome">
        <Mark tone="fail" />
        <p class="outcome-sentence">The run ended without reporting what it did.</p>
        <p class="footnote">It may have failed before it started the operation; the maintainer is told when that happens. Open the run to see.</p>
      </div>
    );
  const tone = TONE[o.conclusion] ?? 'skip';
  const counts = Object.entries(o.counts ?? {});
  return (
    <div class="outcome">
      <Mark tone={tone} />
      <p class="outcome-sentence">{o.summary}</p>
      {counts.length ? <div class="outcome-counts">{counts.map(([k, v]) => <div><div class="n">{v}</div><div class="l">{k.replace(/_/g, ' ')}</div></div>)}</div> : null}
      {result.leaked.length ? (
        <p class="check-line bad"><span>The public record of this run names {result.leaked.length === 1 ? 'a person' : `${result.leaked.length} people`}. Tell the lab: the console reports it so it can be fixed.</span></p>
      ) : null}
      {(o.reasons ?? []).length || result.people.length ? (
        <details class="fold reasons">
          <summary>Details</summary>
          <div class="fold-body">
            {(o.reasons ?? []).length ? (
              <table><tbody>{(o.reasons ?? []).map((r) => (
                <tr><td><code>{r.code}</code></td><td>{r.text}{r.fix?.screen ? <> <a href={`#${r.fix.screen}${r.fix.entry ? `-${r.fix.entry}` : ''}`}>Fix</a></> : null}</td></tr>
              ))}</tbody></table>
            ) : null}
            {result.people.length ? (
              <>
                <p class="footnote">Per person (private; not in the public run log):</p>
                <table><tbody>{result.people.map((p) => <tr><td class="mono">{p.handle}</td><td>{p.text}</td></tr>)}</tbody></table>
              </>
            ) : null}
          </div>
        </details>
      ) : null}
      {runLink(def, o.run_id, url)}
    </div>
  );
}

function Steps({ c }: { c: Current }) {
  const steps = c.progress?.steps ?? [];
  return (
    <>
      <div class="op-mode">{c.running === 'preview' ? <><Eye /><span>Preview: nothing changes</span></> : <span>{c.def.running}</span>}</div>
      <ol class="steps">
        {!c.handle ? <li class="running"><span class="sm" /><span class="st-t">Asking GitHub to start it</span></li> : null}
        {c.handle && !steps.length ? <li class="running"><span class="sm" /><span class="st-t">Waiting for GitHub to start it</span><span class="st-d">Usually a few seconds</span></li> : null}
        {steps.map((s) => (
          <li class={s.state === 'done' || s.state === 'skipped' ? 'done' : s.state === 'running' ? 'running' : s.state === 'failed' ? 'done' : 'waiting'}>
            <span class="sm">{s.state === 'done' ? <Check /> : null}</span>
            <span class="st-t">{s.name}</span>
            {s.state === 'failed' ? <span class="st-d">Did not finish</span> : null}
          </li>
        ))}
      </ol>
      <p class="footnote">Closing this panel does not stop it; it carries on in the bar at the bottom.</p>
    </>
  );
}

function Options({ c, ops }: { c: Current; ops: OpsSession }) {
  const d = c.def;
  if (!d.options && !d.fixed && !d.needsCheck) return null;
  const schema = opSpec(d.op).args_schema;
  return (
    <details class="fold" open>
      <summary>Options</summary>
      <div class="fold-body">
        {d.fixed?.length ? (
          <div class="channels">
            {d.fixed.map((f) => (
              <label class="check locked"><input type="checkbox" checked disabled /><span>{f.label}<span class="always">{f.note}</span></span></label>
            ))}
          </div>
        ) : null}
        {d.options ? <SchemaForm id="op" schema={schema} tiers={d.options} values={c.values} onChange={(v) => ops.setValues(v)} readOnly={c.phase === 'running'} /> : null}
        {d.needsCheck ? (
          <label class="check">
            <input type="checkbox" checked={c.checked} disabled={c.phase === 'running'} onChange={(e) => ops.setChecked((e.target as HTMLInputElement).checked)} />
            <span><b>{d.needsCheck.label}</b><br /><span class="footnote">{d.needsCheck.sub}</span></span>
          </label>
        ) : null}
      </div>
    </details>
  );
}

/** The panel and its minimised bar. Renders nothing when no operation is open. */
export function OpPanel() {
  const env = useEnv();
  const ops = env?.ops;
  const c = ops?.current.value ?? null;
  const head = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (c && !c.min) head.current?.focus();
  }, [c?.def.key, c?.def.op, c?.min]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && ops?.current.value && ops.close();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [ops]);
  if (!ops || !c) return null;
  const d = c.def;
  if (c.min) {
    const text = c.phase === 'running' ? `${d.running}…` : c.result?.outcome?.summary ?? (c.dry ? `Preview ready: ${d.title}` : d.title);
    return (
      <div class="opbar" role="status">
        {c.phase === 'running' ? <span class="spin" aria-hidden="true" /> : null}
        <span class="t">{text}</span>
        <button type="button" onClick={() => ops.show()}>Show</button>
      </div>
    );
  }
  const mode = modeOf(d.op);
  const values = d.options ? effective(d.options, c.values) : c.values;
  const invalid = d.options ? Object.keys(fieldErrors(opSpec(d.op).args_schema, d.options, c.values)).length > 0 : false;
  let body, foot;
  if (d.info) {
    body = d.info;
    foot = <div class="actions"><button class="btn quiet" type="button" onClick={() => ops.close()}>Close</button></div>;
  } else if (c.phase === 'running') {
    body = <Steps c={c} />;
    foot = <div class="actions"><button class="btn small quiet" type="button" onClick={() => void ops.cancel()}>{d.cancel}</button></div>;
  } else if (c.phase === 'done' && c.result) {
    body = <><OutcomeView result={c.result} def={d} url={c.progress?.htmlUrl} /><p class="footnote">Written to <a href="#operations">All operations</a>.</p></>;
    foot = (
      <div class="actions">
        {(d.after ?? []).map((a) => <a class="btn outline" href={a.href}>{a.label}</a>)}
        <button class="btn quiet" type="button" onClick={() => ops.close()}>Close</button>
      </div>
    );
  } else {
    const allowed = ops.canRun({ ...c, values }) && !invalid;
    const gatedShut = mode === 'gated' && !ops.isPreviewed(d, c.values, c.checked);
    body = (
      <>
        <p class="op-intro">{d.intro}</p>
        {d.confirm ? <p class="op-confirm">{d.confirm}</p> : null}
        <Options c={c} ops={ops} />
        {c.error ? <p class="check-line bad"><span>{c.error}</span></p> : null}
        {c.dry ? <OutcomeView result={c.dry} def={d} url={c.progress?.htmlUrl} /> : null}
      </>
    );
    const verb = (
      <button class={mode === 'direct' || mode === 'previewOnly' ? 'btn' : 'btn outline'} type="button" disabled={!allowed} onClick={() => void ops.start(mode === 'previewOnly' ? 'preview' : 'run')}>
        {d.verb}
      </button>
    );
    foot =
      mode === 'direct' || mode === 'previewOnly' ? (
        <div class="actions">{verb}<button class="btn quiet" type="button" onClick={() => ops.close()}>Cancel</button></div>
      ) : (
        <>
          <div class="actions">
            <button class="btn" type="button" disabled={invalid} onClick={() => void ops.start('preview')}>{c.dry ? 'Preview again' : 'Preview'}</button>
            {d.previewProposed ? <Prop /> : null}
            {verb}
          </div>
          {gatedShut ? <p class="gate-hint">{d.verb} unlocks when the preview finishes.</p> : null}
        </>
      );
  }
  return (
    <aside class="drawer" aria-labelledby="drawer-title">
      <div class="drawer-head">
        <div>
          <div class="eyebrow">{d.name}, {d.where}{d.proposed ? <Prop /> : null}</div>
          <h2 id="drawer-title" tabindex={-1} ref={head}>{d.title}</h2>
        </div>
        <div class="dh-btns">
          <button class="x" type="button" aria-label="Minimise" title="Minimise" onClick={() => ops.minimise()}>&#8211;</button>
          <button class="x" type="button" aria-label="Close" title={c.phase === 'running' ? 'Close; keeps running' : 'Close'} onClick={() => ops.close()}>&times;</button>
        </div>
      </div>
      <div class="drawer-body" aria-live="polite">
        {ops.notice.value ? <p class="note">{ops.notice.value}</p> : null}
        {body}
      </div>
      <div class="drawer-foot">{foot}</div>
    </aside>
  );
}

/**
 * The buttons a screen shows for an operation: Preview (filled) and the verb, which a gated
 * op keeps disabled until this session has previewed it; a direct op shows only the verb.
 */
export function OpButtons({ def, small, label, previewLabel = 'Preview', verbCls, hint = true }: { def: OpDef; small?: boolean; label?: string; previewLabel?: string; verbCls?: string; hint?: boolean }) {
  const env = useEnv();
  const ops = env?.ops;
  const mode = modeOf(def.op);
  const sz = small ? ' small' : '';
  const verb = label ?? def.verb;
  if (mode === 'direct' || mode === 'previewOnly')
    return <button class={verbCls ?? `btn${sz}`} type="button" onClick={() => ops?.open(def)}>{verb}</button>;
  const ok = mode !== 'gated' || !!ops?.isPreviewed(def);
  return (
    <>
      <button class={`btn${sz}`} type="button" onClick={() => ops?.open(def, 'preview')}>{previewLabel}</button>
      <button class={verbCls ?? `btn${sz} outline`} type="button" disabled={!ok} title={ok ? undefined : 'Preview first'} onClick={() => ops?.open(def, 'run')}>{verb}</button>
      {!ok && hint ? <span class="gate-hint">Preview first</span> : null}
    </>
  );
}

/** A single button that opens the panel on `def` (no preview shortcut). */
export function OpOpen({ def, cls = 'btn', label }: { def: OpDef; cls?: string; label?: string }) {
  const env = useEnv();
  return <button class={cls} type="button" onClick={() => env?.ops.open(def)}>{label ?? def.name}</button>;
}

