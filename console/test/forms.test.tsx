import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import gradingSchema from '../schemas/grading_config.schema.json';
import { SchemaForm, effective, fieldErrors, layout } from '../src/forms/Form';
import { opSpec } from '../src/ops/registry';
import { fromConfig, settingsTiers, toConfig } from '../src/tiers/grading';
import { RETURN_MARKS, releaseAdhoc } from '../src/tiers/ops';
import { matches } from '../src/edit/glob';
import { diffRoster, readTable, writeTable } from '../src/edit/csv';

const D = { lateDays: '10', latePct: '10%', teamSize: '5' };

describe('the tiered form', () => {
  const tiers = settingsTiers(D);

  it('puts Ask and Default in the body, Conditional under its trigger, Advanced behind the reveal', () => {
    const solo = layout(tiers, fromConfig({ type: 'individual' }));
    expect(solo.main.map((i) => i.key)).toEqual(['title', 'type', 'submit_via', 'visibility', 'format', 'autograde']);
    expect(solo.advanced.map((i) => i.key)).toEqual(['completion_check', 'grader_pdf', 'late_window_days', 'late_penalty_per_day']);
    const team = layout(tiers, fromConfig({ type: 'group', submit_via: 'external', autograde: true }));
    const type = team.main.find((i) => i.key === 'type')!;
    expect(type.under.map((i) => i.key)).toEqual(['team_formation', 'max_team_size']);
    expect(team.main.find((i) => i.key === 'submit_via')!.under.map((i) => i.key)).toEqual(['submit_url']);
    expect(team.main.find((i) => i.key === 'autograde')!.under.map((i) => i.key)).toEqual(['tests']);
  });

  it('counts only non-default Advanced fields', () => {
    expect(layout(tiers, fromConfig({})).changed).toBe(0);
    expect(layout(tiers, fromConfig({ grader_pdf: true, late_window_days: 3, late_penalty_per_day: '5%' })).changed).toBe(3);
    const out = render(<SchemaForm id="t" schema={null} tiers={tiers} values={fromConfig({ grader_pdf: true })} onChange={() => {}} />);
    expect(out).toContain('Advanced <span class="cnt changed">(1 changed)</span>');
  });

  it('forces what another field decides, read-only with the reason', () => {
    const v = fromConfig({ submit_via: 'shared_dropbox_repo', autograde: true, visibility: 'public' });
    const out = render(<SchemaForm id="t" schema={null} tiers={tiers} values={v} onChange={() => {}} />);
    expect(out).toContain('Private: a shared drop box is always private.');
    expect(out).toContain('so tests cannot run per student');
    expect(effective(tiers, v).visibility).toBe('private');
    expect(toConfig(effective(tiers, v)).autograde).toBeUndefined();
  });

  it('lets visibility change for their own repo and saves it, with the note on existing copies', () => {
    const v = { ...fromConfig({ submit_via: 'assignment_repo', visibility: 'private' }), visibility: 'public' };
    expect(tiers.visibility.forced?.(v)).toBeNull();
    expect(tiers.visibility.options?.map((o) => o.value)).toEqual(['private', 'public', 'student_choice']);
    const out = render(<SchemaForm id="t" schema={null} tiers={tiers} values={v} onChange={() => {}} />);
    expect(out).toContain('Applies to copies handed out after this change; existing copies keep theirs.');
    expect(out).not.toContain('cannot be changed');
    expect(toConfig(effective(tiers, v)).visibility).toBe('public');
    expect(toConfig(effective(tiers, { ...v, submit_via: 'external', submit_url: 'https://x' })).visibility).toBe('private');
  });

  it('validates inline: the bad value in the file, https only, both or neither', () => {
    expect(fieldErrors(null, tiers, fromConfig({ autograde: 'sometimes' })).autograde).toContain('“sometimes”');
    expect(fieldErrors(null, tiers, fromConfig({ submit_via: 'external', submit_url: 'http://x' })).submit_url).toContain('https://');
    expect(fieldErrors(null, tiers, fromConfig({ late_window_days: 3 })).late_window_days).toContain('both or neither');
    expect(fieldErrors(null, tiers, fromConfig({ late_window_days: 3, late_penalty_per_day: '10' })).late_penalty_per_day).toContain('percentage');
  });

  it('round-trips grading_config.yml and the schema accepts the result', async () => {
    const cfg = { title: 'Group project', type: 'group', team_formation: 'assigned', max_team_size: 4, submit_via: 'assignment_repo', visibility: 'private', format: 'ipynb', autograde: true, completion_check: false };
    const back = Object.fromEntries(Object.entries(toConfig(effective(tiers, fromConfig(cfg)))).filter(([, x]) => x !== undefined));
    expect(back).toEqual(cfg);
    const { default: Ajv } = await import('ajv/dist/2020');
    expect(new Ajv({ strict: false }).validate(gradingSchema, back)).toBe(true);
  });

  it('validates op options against the registry schema and hides a conditional option', () => {
    const t = releaseAdhoc(['course-materials-f2026']);
    const errs = fieldErrors(opSpec('release.adhoc').args_schema, t, { course_source_repo: 'course-materials-f2026' });
    expect(errs.course_source_path).toBe('Needed.');
    expect(fieldErrors(opSpec('release.adhoc').args_schema, t, { course_source_repo: 'x', course_source_path: '-bad' }).course_source_path).toBe('Not in the expected form.');
    expect(effective(RETURN_MARKS, { notify: false, include_feedback: true })).toEqual({ notify: false });
    const out = render(<SchemaForm id="o" schema={null} tiers={RETURN_MARKS} values={{ notify: true }} onChange={() => {}} />);
    expect(out).toContain('Include the feedback text in the email');
  });

  it('shows the reason line and a markdown preview', () => {
    const out = render(<SchemaForm id="o" schema={null} tiers={{ d: { tier: 'ask', label: 'Details', widget: 'markdown', reason: 'Shown to students.' } }} values={{ d: 'Some **bold**' }} onChange={() => {}} />);
    expect(out).toContain('<p class="why">Shown to students.</p>');
    expect(out).toContain('What students see');
    expect(out).toContain('<b>bold</b>');
  });
});

describe('pattern previews', () => {
  it('matches like .gitignore, last match winning', () => {
    const pub = ['lectures/**/*.html', 'lectures/**/*.pdf', '!readings/**'];
    expect(matches(pub, 'lectures/01/slides.html')).toBe(true);
    expect(matches(pub, 'lectures/01/notes.md')).toBe(false);
    expect(matches(['solutions/'], 'labs/03/solutions/a.py')).toBe(true);
    expect(matches(['*.key', '!keep.key'], 'a/keep.key')).toBe(false);
    expect(matches(['/SYLLABUS.md'], 'x/SYLLABUS.md')).toBe(false);
    expect(matches(['**'], 'anything/at/all')).toBe(true);
  });
});

describe('roster tables', () => {
  const src = '﻿hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna,enrolled,anna-a,1,CODE1,2026-09-02\nben@x.org,"Baker, Ben",,,,,\n';
  it('keeps every column, the enrol code included, and quotes what needs it', () => {
    const t = readTable(src);
    expect(t.rows[1].name).toBe('Baker, Ben');
    t.rows[0].name = 'Anna Adams';
    const out = writeTable(t);
    expect(out).toBe('hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\nanna@x.org,Anna Adams,enrolled,anna-a,1,CODE1,2026-09-02\nben@x.org,"Baker, Ben",,,,,\n');
  });
  it('diffs a replacement by email, keeping system columns', () => {
    const cur = readTable(src).rows;
    const up = readTable('hertie_email,name,role\nANNA@x.org,Anna A,auditor\ncarla@x.org,Carla,\n').rows;
    const { diff, rows } = diffRoster(cur, up);
    expect(diff.added.map((r) => r.hertie_email)).toEqual(['carla@x.org']);
    expect(diff.changed[0].after).toMatchObject({ name: 'Anna A', role: 'auditor', github_handle: 'anna-a', enrol_code: 'CODE1' });
    expect(diff.removed.map((r) => r.hertie_email)).toEqual(['ben@x.org']);
    expect(rows).toHaveLength(2);
  });
});
