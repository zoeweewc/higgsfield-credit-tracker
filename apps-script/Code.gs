// Receives each teammate's Higgsfield credit rows and keeps the team sheet up to date.
// Paste this into Extensions > Apps Script of the team Google Sheet, then deploy as a web app
// (Execute as: Me, Who has access: Anyone). Change SECRET before deploying; it is the password
// every teammate's tracker sends along. Never commit the real one.
const SECRET = 'REPLACE-ME';

const SEP = '\u0001'; // joins parts of an internal key; cannot appear in a project name
const LEDGER = 'Ledger', ADS = 'Finished ads', NAMES = 'Project names';
const LEDGER_HEAD = ['Key', 'Month', 'Date', 'Person', 'Model', 'Credits', 'Kind', 'Their project', 'Folder'];
const ADS_HEAD = ['Key', 'Month', 'Person', 'Their project', 'Finished ads'];
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
    // finished ads arrive as a person's complete current counts, so they replace that person's
    // old lines; otherwise a renamed project would be counted under both its names
    if (body.snapshot) replaceAds(sheetOf(ss, ADS, ADS_HEAD), body.user, ads);
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
  const mine = ads.map(a => [user + '|' + a.month + '|' + a.project, a.month, user, a.project, Number(a.count)]);
  const grid = [ADS_HEAD].concat(keep, mine);
  sh.clear();
  sh.getRange(1, 1, grid.length, ADS_HEAD.length).setValues(grid);
  sh.setFrozenRows(1);
}

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Credits').addItem('Refresh summary', 'refreshNow').addToUi();
}
function refreshNow() { refresh(SpreadsheetApp.getActiveSpreadsheet()); }

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function sheetOf(ss, name, head) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
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

// Rebuild the read-only tabs from the Ledger, Finished ads and Project names tabs.
function refresh(ss) {
  const ledger = body(sheetOf(ss, LEDGER, LEDGER_HEAD)), ads = body(sheetOf(ss, ADS, ADS_HEAD));
  const namesSheet = sheetOf(ss, NAMES, NAMES_HEAD);

  // every (person, their project) pair gets a line in Project names, left blank for you to fill in
  const names = new Map();
  body(namesSheet).forEach(r => { if (r[0]) names.set(r[0] + SEP + r[1], String(r[2] || '').trim()); });
  const pairs = new Set();
  ledger.forEach(r => pairs.add(r[3] + SEP + r[7]));
  ads.forEach(r => pairs.add(r[2] + SEP + r[3]));
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
  ads.forEach(r => {
    const n = Number(r[4]);
    bump(byProject, r[1] + SEP + team(r[2], r[3]), t => { t[3] += n; });
    bump(byPerson, r[1] + SEP + r[2], t => { t[3] += n; });
  });

  const table = (map, head) => {
    const lines = [...map.entries()].map(([k, t]) => k.split(SEP).concat(t.map(round)))
      .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : b[2] - a[2]));
    return [head].concat(lines);
  };
  const months = new Map();
  byProject.forEach((t, k) => bump(months, k.split(SEP)[0], m => t.forEach((v, i) => { m[i] += v; })));
  const monthRows = [['Month', 'Credits', 'Clips made', 'Images made', 'Finished ads']].concat(
    [...months.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1)).map(([m, t]) => [m].concat(t.map(round))));

  write(ss, 'Month totals', monthRows);
  write(ss, 'Summary', table(byProject, ['Month', 'Project', 'Credits', 'Clips made', 'Images made', 'Finished ads']));
  write(ss, 'By person', table(byPerson, ['Month', 'Person', 'Credits', 'Clips made', 'Images made', 'Finished ads']));
}

const round = v => Math.round(v * 100) / 100;

function body(sh) {
  const last = sh.getLastRow();
  return last > 1 ? sh.getRange(2, 1, last - 1, sh.getLastColumn()).getValues() : [];
}

function write(ss, name, grid) {
  let sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear();
  sh.getRange(1, 1, grid.length, grid[0].length).setValues(grid);
  sh.setFrozenRows(1);
}
