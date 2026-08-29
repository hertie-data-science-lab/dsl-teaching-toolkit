// The half of the two onboarding workflows' github-script bodies that is the same in both.
// Not a workflow of its own: dsl_course.welcome splices it in at their `// {shared_script}`
// line, so a cohort receives one file per workflow as before. It was written out twice, in
// two files, against one file format, with a test comparing the copies byte for byte to
// catch the day they stopped agreeing.
//
// --- CSV, RFC-4180-ish (github-script has no npm deps, so hand-rolled).
// Both CSVs are written by Python's csv module, so a field containing a comma (a roster
// name like "Doe, Jane") arrives QUOTED. A naive line.split(',') shifts every column right
// of it: the wrong enrol_code read, github_handle / github_id written into the wrong cells,
// teams.csv rewritten with mangled rows. Quoted fields may contain commas, newlines and
// "" -escaped quotes; serialiseCsv re-quotes on the same rule as csv.QUOTE_MINIMAL.
const parseCsv = (text) => {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') { quoted = false; }
      else { field += c; }
      continue;
    }
    if (c === '"') { quoted = true; }
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (c !== '\r') { field += c; }
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(v => v !== ''));  // drop blank lines
};
const csvCell = (v) => {
  const s = (v === undefined || v === null) ? '' : String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
};
const serialiseCsv = (rows) => rows.map(r => r.map(csvCell).join(',')).join('\n') + '\n';

// Giving up is a RESULT, not a crash: an uncaught throw leaves a red run with no comment
// and no label, so the student is dropped in silence and nobody triages it. Every terminal
// path in both workflows goes through here.
const fail = async (msg, label) => {
  await github.rest.issues.createComment({
    owner: org, repo: context.repo.repo, issue_number: issue.number, body: msg });
  await github.rest.issues.addLabels({
    owner: org, repo: context.repo.repo, issue_number: issue.number, labels: [label] });
  core.setFailed(msg.replace(/\*\*/g, ''));
};
