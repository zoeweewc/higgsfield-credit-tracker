// Receives each teammate's Higgsfield credit rows and keeps the team sheet up to date.
// Paste this into Extensions > Apps Script of the team Google Sheet, then deploy as a web app
// (Execute as: Me, Who has access: Anyone). Change SECRET before deploying; it is the password
// every teammate's tracker sends along. Never commit the real one.
const SECRET = 'REPLACE-ME';

const SEP = '\u0001'; // joins parts of an internal key; cannot appear in a project name
const LEDGER = 'Ledger', ADS = 'Final videos', NAMES = 'Project names', HISTORY = 'History';
const TEXT_COLS = { 'Ledger': 3, 'Final videos': 2, 'Project names': 3, 'History': 3 };
const LEDGER_HEAD = ['Key', 'Month', 'Date', 'Person', 'Model', 'Credits', 'Kind', 'Their project', 'Folder'];
const ADS_HEAD = ['Key', 'Month', 'Person', 'Video', 'Project', 'Style', 'Credits', 'Clips made', 'Images made', 'Status'];
const STYLES = ['Animated', 'AI UGC', 'Cinematic', 'Hypermotion'];
const HISTORY_HEAD = ['Key', 'Month', 'Person', 'Their project', 'Credits', 'Clips made', 'Images made'];
const NAMES_HEAD = ['Person', 'Their project name', 'Team project name (type yours here)'];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const body = JSON.parse(e.postData.contents);
    if (!SECRET || SECRET === 'REPLACE-ME' || body.secret !== SECRET) return reply({ ok: false, error: 'bad secret' });
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const rows = body.rows || [], ads = body.ads || [];
    upsert(sheetOf(ss, LEDGER, LEDGER_HEAD), rows.map(r =>
      [r.key, r.month, r.created_at, r.user, r.model, Number(r.credits), r.kind, r.project, r.folder]), 7);
    // a person's videos arrive as their complete current list (name, project, style, credits spent on
    // it, finished or not), so they replace that person's old lines; otherwise a renamed project
    // would show a video under both its names
    if (body.snapshot) {
      replaceAds(sheetOf(ss, ADS, ADS_HEAD), body.user, ads);
      // Months before `cutoff` are kept as one small line per project instead of one per charge, so
      // the sheet never grows without limit. The person's own totals for those months arrive here
      // in full, so their detailed lines for those months can go.
      if (body.history && body.cutoff) {
        replaceHistory(sheetOf(ss, HISTORY, HISTORY_HEAD), body.user, body.history);
        pruneLedger(sheetOf(ss, LEDGER, LEDGER_HEAD), body.user, body.cutoff);
      }
    }
    refresh(ss);
    return reply({ ok: true, rows: rows.length, ads: ads.length });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function replaceAds(sh, user, ads) {
  const keep = body(sh).filter(r => r[2] !== user);
  const mine = ads.map(a => [user + '|' + a.month + '|' + a.name + '|' + a.project, a.month, user, a.name,
                             a.project, a.style || '', Number(a.credits), Number(a.clips), Number(a.images), a.status]);
  const grid = [ADS_HEAD].concat(keep, mine);
  sh.clear();
  sh.getRange(1, 1, sh.getMaxRows(), 2).setNumberFormat('@');
  sh.getRange(1, 1, grid.length, ADS_HEAD.length).setValues(grid);
  sh.setFrozenRows(1);
}

function replaceHistory(sh, user, hist) {
  const keep = body(sh).filter(r => r[2] !== user);
  const mine = hist.map(h => [user + '|' + h.month + '|' + h.project, h.month, user, h.project,
                              Number(h.credits), Number(h.clips), Number(h.images)]);
  const grid = [HISTORY_HEAD].concat(keep, mine);
  sh.clear();
  sh.getRange(1, 1, sh.getMaxRows(), 3).setNumberFormat('@');
  sh.getRange(1, 1, grid.length, HISTORY_HEAD.length).setValues(grid);
  sh.setFrozenRows(1);
}

function pruneLedger(sh, user, cutoff) {
  const all = body(sh);
  const keep = all.filter(r => !(r[3] === user && String(r[1]) < cutoff));
  if (keep.length === all.length) return;
  const grid = [LEDGER_HEAD].concat(keep);
  sh.clear();
  sh.getRange(1, 1, sh.getMaxRows(), 3).setNumberFormat('@');
  sh.getRange(1, 1, grid.length, LEDGER_HEAD.length).setValues(grid);
  sh.setFrozenRows(1);
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Credits').addItem('Refresh summary', 'refreshNow').addToUi();
}
function refreshNow() { refresh(SpreadsheetApp.getActiveSpreadsheet()); }

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// Sheets turns text like "2026-10" into a date and reads it back as one, which would break every
// month total. So the leading text columns are formatted as plain text before anything is written.
function sheetOf(ss, name, head) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, sh.getMaxRows(), TEXT_COLS[name]).setNumberFormat('@');
    sh.getRange(1, 1, 1, head.length).setValues([head]);
    sh.setFrozenRows(1);
  }
  return sh;
}

// Rows are keyed by column A. A known key is overwritten (a renamed project or a recount
// replaces the old row); a new key is appended. `valueFrom` is the 0-based column from which
// an existing row is overwritten, so the key and month never change.
function upsert(sh, rows, valueFrom) {
  if (!rows.length) return;
  const width = rows[0].length;
  const last = sh.getLastRow();
  const keys = new Map();
  if (last > 1) sh.getRange(2, 1, last - 1, 1).getValues().forEach((r, i) => keys.set(String(r[0]), i + 2));
  const fresh = [];
  rows.forEach(r => {
    const at = keys.get(String(r[0]));
    if (at) sh.getRange(at, valueFrom + 1, 1, width - valueFrom).setValues([r.slice(valueFrom)]);
    else { keys.set(String(r[0]), -1); fresh.push(r); }
  });
  if (fresh.length) sh.getRange(sh.getLastRow() + 1, 1, fresh.length, width).setValues(fresh);
}

// Rebuild the read-only tabs from the Ledger (recent charges), History (older months, already
// added up), Final videos and Project names tabs.
function refresh(ss) {
  const ledger = body(sheetOf(ss, LEDGER, LEDGER_HEAD)), ads = body(sheetOf(ss, ADS, ADS_HEAD));
  const history = body(sheetOf(ss, HISTORY, HISTORY_HEAD));
  const namesSheet = sheetOf(ss, NAMES, NAMES_HEAD);

  // every (person, their project) pair gets a line in Project names, left blank for you to fill in
  const names = new Map();
  body(namesSheet).forEach(r => { if (r[0]) names.set(r[0] + SEP + r[1], String(r[2] || '').trim()); });
  const pairs = new Set();
  ledger.forEach(r => pairs.add(r[3] + SEP + r[7]));
  ads.forEach(r => pairs.add(r[2] + SEP + r[4]));
  history.forEach(r => pairs.add(r[2] + SEP + r[3]));
  const added = [...pairs].filter(k => !names.has(k)).sort().map(k => k.split(SEP).concat(['']));
  if (added.length) namesSheet.getRange(namesSheet.getLastRow() + 1, 1, added.length, 3).setValues(added);
  added.forEach(a => names.set(a[0] + SEP + a[1], ''));
  const team = (person, proj) => names.get(person + SEP + proj) || proj;

  const byProject = new Map(), byPerson = new Map();
  const bump = (map, key, f) => { if (!map.has(key)) map.set(key, [0, 0, 0, 0]); f(map.get(key)); };
  ledger.forEach(r => {
    const credits = Number(r[5]), clip = credits > 0 && r[6] === 'video', image = credits > 0 && r[6] === 'image';
    const add = t => { t[0] += credits; if (clip) t[1]++; if (image) t[2]++; };
    bump(byProject, r[1] + SEP + team(r[3], r[7]), add);
    bump(byPerson, r[1] + SEP + r[3], add);
  });
  history.forEach(r => {
    const add = t => { t[0] += Number(r[4]); t[1] += Number(r[5]); t[2] += Number(r[6]); };
    bump(byProject, r[1] + SEP + team(r[2], r[3]), add);
    bump(byPerson, r[1] + SEP + r[2], add);
  });
  ads.filter(r => r[9] === 'Finished').forEach(r => {
    bump(byProject, r[1] + SEP + team(r[2], r[4]), t => { t[3]++; });
    bump(byPerson, r[1] + SEP + r[2], t => { t[3]++; });
  });

  const table = (map, head) => {
    const lines = [...map.entries()].map(([k, t]) => k.split(SEP).concat(t.map(round)))
      .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : b[2] - a[2]));
    return [head].concat(lines);
  };
  const months = new Map();
  byProject.forEach((t, k) => bump(months, k.split(SEP)[0], m => t.forEach((v, i) => { m[i] += v; })));
  const monthRows = [['Month', 'Credits', 'Clips made', 'Images made', 'Finished videos']].concat(
    [...months.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1)).map(([m, t]) => [m].concat(t.map(round))));

  const projects = [...new Set([...byProject.keys()].map(k => k.split(SEP)[1]))];
  const spent = p => [...byProject.entries()].filter(([k]) => k.split(SEP)[1] === p).reduce((a, [, t]) => a + t[0], 0);
  projects.sort((a, b) => spent(b) - spent(a));
  const monthsUp = [...months.keys()].sort();
  const matrix = [['Month'].concat(projects)].concat(monthsUp.map(m =>
    [m].concat(projects.map(p => round((byProject.get(m + SEP + p) || [0])[0])))));
  const totalsGrid = monthRows.concat([[], ['Credits by project, month by month']], matrix);
  const width = Math.max(...totalsGrid.map(r => r.length));
  totalsGrid.forEach(r => { while (r.length < width) r.push(''); });

  // cost per finished video, by style: the table that says which styles are worth making
  const styleOf = r => r[5] || '(no style)';
  const order = s => { const i = STYLES.indexOf(s); return i < 0 ? STYLES.length : i; };
  const done = ads.filter(r => r[9] === 'Finished');
  const stats = rows => ({ n: rows.length, credits: rows.reduce((a, r) => a + Number(r[6]), 0), clips: rows.reduce((a, r) => a + Number(r[7]), 0) });
  const styleNames = [...new Set(ads.map(styleOf).concat(STYLES))].sort((a, b) => order(a) - order(b) || (a < b ? -1 : 1));
  const styleRows = [['Style', 'Finished videos', 'In progress', 'Credits on finished videos', 'Credits per video', 'Clips per video']];
  styleNames.forEach(st => {
    const f = stats(done.filter(r => styleOf(r) === st)), open = ads.filter(r => r[9] !== 'Finished' && styleOf(r) === st).length;
    if (f.n || open || STYLES.indexOf(st) >= 0)
      styleRows.push([st, f.n, open, round(f.credits), f.n ? round(f.credits / f.n) : '', f.n ? round(f.clips / f.n) : '']);
  });
  const byMonthStyle = new Map();
  done.forEach(r => { const k = r[1] + SEP + styleOf(r); if (!byMonthStyle.has(k)) byMonthStyle.set(k, []); byMonthStyle.get(k).push(r); });
  const monthStyle = [...byMonthStyle.entries()].map(([k, rows]) => [...k.split(SEP), stats(rows)])
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : order(a[1]) - order(b[1])))
    .map(([m, st, x]) => [m, st, x.n, round(x.credits), round(x.credits / x.n)]);
  const styleGrid = styleRows.concat([[], ['By month']], [['Month', 'Style', 'Finished videos', 'Credits', 'Credits per video']], monthStyle);
  const styleWidth = Math.max(...styleGrid.map(r => r.length));
  styleGrid.forEach(r => { while (r.length < styleWidth) r.push(''); });
  write(ss, 'By style', styleGrid);

  write(ss, 'Month totals', totalsGrid);
  write(ss, 'Summary', table(byProject, ['Month', 'Project', 'Credits', 'Clips made', 'Images made', 'Finished videos']));
  write(ss, 'By person', table(byPerson, ['Month', 'Person', 'Credits', 'Clips made', 'Images made', 'Finished videos']));
  style(ss, monthRows.length, matrix.length, projects.length, styleRows.length, monthStyle.length);
}

// Colour and a chart, to make the months easy to read at a glance. This is decoration only, so a
// failure here must never stop the numbers from being saved.
function style(ss, totalRows, matrixRows, projectCount, styleRows, monthStyleRows) {
  try {
    const TINTS = ['#e8f0fe', '#fff4e0'];
    ['Summary', 'By person'].forEach(name => {
      const sh = ss.getSheetByName(name), last = sh.getLastRow(), cols = sh.getLastColumn();
      sh.getRange(1, 1, 1, cols).setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
      if (last < 2) return;
      const months = sh.getRange(2, 1, last - 1, 1).getValues().map(r => r[0]);
      let tint = 0;
      const fills = months.map((m, i) => {
        if (i > 0 && m !== months[i - 1]) tint = 1 - tint;
        return new Array(cols).fill(TINTS[tint]);
      });
      sh.getRange(2, 1, last - 1, cols).setBackgrounds(fills);
      months.forEach((m, i) => {  // a line above each new month
        if (i === 0 || m !== months[i - 1]) sh.getRange(i + 2, 1, 1, cols).setBorder(true, null, null, null, null, null, '#5f6368', SpreadsheetApp.BorderStyle.SOLID);
      });
      sh.getRange(2, 3, last - 1, 1).setNumberFormat('#,##0.00').setFontWeight('bold');
      sh.getRange(2, 4, last - 1, 3).setNumberFormat('#,##0');
      sh.setConditionalFormatRules([SpreadsheetApp.newConditionalFormatRule()
        .setGradientMinpoint('#ffffff').setGradientMaxpoint('#57bb8a')
        .setRanges([sh.getRange(2, 3, last - 1, 1)]).build()]);
      sh.setColumnWidth(2, 180);
      sh.autoResizeColumns(3, cols - 2);
    });

    // cheap videos are green, expensive ones red, so the cost of each style stands out
    const bs = ss.getSheetByName('By style');
    bs.getRange(1, 1, 1, 6).setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
    bs.getRange(styleRows + 2, 1).setFontWeight('bold').setFontSize(12);
    bs.getRange(styleRows + 3, 1, 1, 5).setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
    bs.getRange(2, 4, styleRows - 1, 3).setNumberFormat('#,##0.00');
    const spread = rng => SpreadsheetApp.newConditionalFormatRule().setGradientMinpoint('#57bb8a')
      .setGradientMidpointWithValue('#ffffff', SpreadsheetApp.InterpolationType.PERCENTILE, '50')
      .setGradientMaxpoint('#e67c73').setRanges([rng]).build();
    const rules = [spread(bs.getRange(2, 5, styleRows - 1, 1))];
    if (monthStyleRows > 0) {
      bs.getRange(styleRows + 4, 4, monthStyleRows, 2).setNumberFormat('#,##0.00');
      rules.push(spread(bs.getRange(styleRows + 4, 5, monthStyleRows, 1)));
    }
    bs.setConditionalFormatRules(rules);
    bs.autoResizeColumns(1, 6);

    const mt = ss.getSheetByName('Month totals');
    mt.getRange(1, 1, 1, 5).setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
    mt.getRange(2, 2, totalRows - 1, 1).setNumberFormat('#,##0.00').setFontWeight('bold');
    mt.getRange(totalRows + 2, 1).setFontWeight('bold').setFontSize(12);
    const head = totalRows + 3;
    mt.getRange(head, 1, 1, projectCount + 1).setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
    if (matrixRows > 1) mt.getRange(head + 1, 2, matrixRows - 1, projectCount).setNumberFormat('#,##0');
    mt.autoResizeColumns(1, Math.min(projectCount + 1, 8));
    mt.getCharts().forEach(c => mt.removeChart(c));
    if (projectCount > 0 && matrixRows > 1) {
      mt.insertChart(mt.newChart().setChartType(Charts.ChartType.COLUMN)
        .addRange(mt.getRange(head, 1, matrixRows, projectCount + 1)).setNumHeaders(1)
        .setOption('isStacked', true).setOption('title', 'Credits by month, split by project')
        .setOption('legend', { position: 'right' }).setOption('width', 760).setOption('height', 360)
        .setPosition(head + matrixRows + 2, 1, 0, 0).build());
    }
  } catch (err) {
    console.error('style failed: ' + err);
  }
}

const round = v => Math.round(v * 100) / 100;

function body(sh) {
  const last = sh.getLastRow();
  return last > 1 ? sh.getRange(2, 1, last - 1, sh.getLastColumn()).getValues() : [];
}

function write(ss, name, grid) {
  let sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear();
  sh.getRange(1, 1, sh.getMaxRows(), 1).setNumberFormat('@'); // the Month column
  sh.getRange(1, 1, grid.length, grid[0].length).setValues(grid);
  sh.setFrozenRows(1);
}
