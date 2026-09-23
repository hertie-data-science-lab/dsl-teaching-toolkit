// A form rendered from a JSON Schema and a tiers map (design/inputs.md): Ask and Default
// shown in the body, Conditional directly under its trigger, Advanced behind one reveal that
// counts what is not default, Hidden and Derived never a field. Validation is inline, from
// the schema through ajv plus each field's own check; a forced field is read-only with its
// reason.

import Ajv2020, { type ErrorObject, type ValidateFunction } from 'ajv/dist/2020';
import type { ComponentChildren } from 'preact';
import { deepEqual } from '../edit/yamlText';
import { md } from '../model/format';
import type { FieldTier, Tiers, Values } from '../tiers/types';
import { Prop } from '../ui/bits';
import { Alert, Lock } from '../ui/icons';

export interface Item {
  key: string;
  t: FieldTier;
  under: Item[];
}

export interface Layout {
  main: Item[];
  advanced: Item[];
  changed: number;
}

const shown = (t: FieldTier, v: Values) => t.tier !== 'hidden' && t.tier !== 'derived' && (!t.when || t.when(v));

/** Where each visible field goes, and how many Advanced fields are not at their default. */
export function layout(tiers: Tiers, values: Values): Layout {
  const items = new Map<string, Item>();
  const main: Item[] = [], advanced: Item[] = [];
  for (const [key, t] of Object.entries(tiers)) {
    if (!shown(t, values)) continue;
    const item = { key, t, under: [] };
    items.set(key, item);
    const parent = t.under ? items.get(t.under) : undefined;
    if (parent) parent.under.push(item);
    else (t.tier === 'advanced' ? advanced : main).push(item);
  }
  const flat = (list: Item[]): Item[] => list.flatMap((i) => [i, ...flat(i.under)]);
  const changed = flat(advanced).filter((i) => !i.t.forced?.(values) && isChanged(i.t, values[i.key])).length;
  return { main, advanced, changed };
}

function isChanged(t: FieldTier, v: unknown): boolean {
  if (v === undefined || v === '' || v === null) return false;
  return !deepEqual(v, t.default);
}

/** The values a form submits: forced values applied, fields that are not showing dropped. */
export function effective(tiers: Tiers, values: Values): Values {
  const out: Values = { ...values };
  for (const [k, t] of Object.entries(tiers)) {
    const f = t.forced?.(values);
    if (f) out[k] = f.value;
    else if (t.when && !t.when(values)) delete out[k];
  }
  return out;
}

const ajv = new Ajv2020({ allErrors: true, strict: false });
const compiled = new WeakMap<object, ValidateFunction>();

function sentence(e: ErrorObject): string {
  switch (e.keyword) {
    case 'required':
      return 'Needed.';
    case 'enum':
      return `Must be one of ${(e.params as { allowedValues: unknown[] }).allowedValues.filter((x) => x !== '').join(', ')}.`;
    case 'pattern':
      return 'Not in the expected form.';
    case 'type':
      return `Must be ${String((e.params as { type: string }).type).replace(',', ' or ')}.`;
    default:
      return `${e.message ?? 'Not valid'}.`.replace(/^./, (c) => c.toUpperCase());
  }
}

/** Field key -> the first thing wrong with it, for the fields that are showing. */
export function fieldErrors(schema: object | null, tiers: Tiers, values: Values): Record<string, string> {
  const v = effective(tiers, values);
  const out: Record<string, string> = {};
  if (schema) {
    let fn = compiled.get(schema);
    if (!fn) {
      fn = ajv.compile(schema);
      compiled.set(schema, fn);
    }
    const data = Object.fromEntries(Object.entries(v).filter(([, x]) => x !== undefined && x !== ''));
    if (!fn(data))
      for (const e of fn.errors ?? []) {
        const key = e.keyword === 'required' ? (e.params as { missingProperty: string }).missingProperty : e.instancePath.split('/')[1];
        if (key && tiers[key] && shown(tiers[key], values) && !out[key]) out[key] = sentence(e);
      }
  }
  for (const [k, t] of Object.entries(tiers)) {
    if (out[k] || !t.check || !shown(t, values)) continue;
    const msg = t.check(v[k], v);
    if (msg) out[k] = msg;
  }
  return out;
}

export function Invalid({ children }: { children: ComponentChildren }) {
  return <span class="invalid-msg"><Alert /><span>{children}</span></span>;
}

function Label({ t, id, as }: { t: FieldTier; id?: string; as?: 'label' | 'span' }) {
  const inner = (
    <>
      {t.label}
      {t.defaultLabel ? <span class="default"> {t.defaultLabel}</span> : null}
      {t.irreversible ? <span class="default"> cannot be changed after creation</span> : null}
      {t.proposed ? <Prop /> : null}
    </>
  );
  return as === 'span' || !id ? <span class="label">{inner}</span> : <label for={id}>{inner}</label>;
}

interface FieldProps {
  id: string;
  k: string;
  t: FieldTier;
  value: unknown;
  values: Values;
  error?: string;
  set: (k: string, v: unknown) => void;
  readOnly?: boolean;
}

function str(v: unknown): string {
  return v === undefined || v === null ? '' : String(v);
}

export function Field({ id, k, t, value, values, error, set, readOnly }: FieldProps) {
  const forced = t.forced?.(values);
  if (forced)
    return (
      <div class="field">
        <Label t={t} as="span" />
        <div class="readonly"><Lock />{forced.reason}</div>
      </div>
    );
  const w = t.widget ?? 'text';
  const inv = error ? { 'aria-invalid': 'true' as const } : {};
  const why = t.reason ? <p class="why">{t.reason}</p> : null;
  const err = error ? <Invalid>{error}</Invalid> : null;
  if (w === 'checkbox')
    return (
      <div class="field">
        <label class="check">
          <input type="checkbox" id={id} checked={value === true} disabled={readOnly} onChange={(e) => set(k, (e.target as HTMLInputElement).checked)} />
          <span>{t.label}{t.defaultLabel ? <span class="default"> {t.defaultLabel}</span> : null}{t.proposed ? <Prop /> : null}</span>
        </label>
        {why}
        {err}
      </div>
    );
  if (w === 'radio')
    return (
      <div class="field">
        <Label t={t} as="span" />
        <div class="choices" role="radiogroup" aria-label={t.label}>
          {(t.options ?? []).map((o) => (
            <label class={`choice${o.off ? ' off' : ''}`} title={o.off}>
              <input type="radio" name={id} value={o.value} checked={str(value) === o.value} disabled={readOnly || !!o.off} onChange={() => set(k, o.value)} />
              <b>{o.label}</b>
              {o.sub || o.off ? <span>{o.off ?? o.sub}</span> : null}
            </label>
          ))}
        </div>
        {why}
        {err}
      </div>
    );
  let input;
  if (w === 'select')
    input = (
      <select id={id} disabled={readOnly} {...inv} onChange={(e) => set(k, (e.target as HTMLSelectElement).value || undefined)}>
        {(t.options ?? []).map((o) => <option value={o.value} selected={str(value) === o.value} disabled={!!o.off}>{o.label}</option>)}
      </select>
    );
  else if (w === 'textarea' || w === 'markdown')
    input = <textarea id={id} readOnly={readOnly} placeholder={t.placeholder} {...inv} onInput={(e) => set(k, (e.target as HTMLTextAreaElement).value)}>{str(value)}</textarea>;
  else
    input = (
      <input
        type={w}
        id={id}
        value={str(value)}
        readOnly={readOnly}
        placeholder={t.placeholder}
        {...inv}
        onInput={(e) => {
          const raw = (e.target as HTMLInputElement).value;
          set(k, w === 'number' ? (raw === '' ? undefined : Number(raw)) : raw === '' ? undefined : raw);
        }}
      />
    );
  return (
    <div class={`field${w === 'markdown' ? ' md-field' : ''}`}>
      <Label t={t} id={id} />
      {input}
      {w === 'markdown' ? (
        <div class="md-prev" aria-live="polite">
          <span class="lbl">What students see</span>
          {str(value).trim() ? <div dangerouslySetInnerHTML={{ __html: md(str(value)) }} /> : <span class="empty">Nothing</span>}
        </div>
      ) : null}
      {why}
      {err}
    </div>
  );
}

export interface FormProps {
  id: string;
  schema: object | null;
  tiers: Tiers;
  values: Values;
  onChange: (v: Values) => void;
  /** The Derived tier, as one line: "For Machine Learning, Fall 2026". */
  context?: string;
  readOnly?: boolean;
  /** Open the Advanced reveal from the start (a problem points into it). */
  advancedOpen?: boolean;
}

/** The fields of `tiers` as a form over `values`. */
export function SchemaForm({ id, schema, tiers, values, onChange, context, readOnly, advancedOpen }: FormProps) {
  const lay = layout(tiers, values);
  const errors = fieldErrors(schema, tiers, values);
  const set = (k: string, v: unknown) => onChange({ ...values, [k]: v });
  const render = (items: Item[]): ComponentChildren =>
    items.map((i) => (
      <>
        <Field id={`${id}-${i.key}`} k={i.key} t={i.t} value={values[i.key] ?? (i.t.tier === 'default' || i.t.tier === 'advanced' ? i.t.default : undefined)} values={values} error={errors[i.key]} set={set} readOnly={readOnly} />
        {i.under.length ? <div class="cond">{render(i.under)}</div> : null}
      </>
    ));
  return (
    <div class="form">
      {context ? <p class="footnote ctx">{context}</p> : null}
      {render(lay.main)}
      {lay.advanced.length ? (
        <details class="fold" open={advancedOpen || lay.advanced.some((i) => errors[i.key])}>
          <summary>Advanced <span class={`cnt${lay.changed ? ' changed' : ''}`}>({lay.changed ? `${lay.changed} changed` : 'none changed'})</span></summary>
          <div class="fold-body">{render(lay.advanced)}</div>
        </details>
      ) : null}
    </div>
  );
}
