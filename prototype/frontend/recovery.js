function csvRows(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; }
      else quoted = !quoted;
    } else if (c === ',' && !quoted) { row.push(cell); cell = ''; }
    else if (c === '\n' && !quoted) { row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (quoted) throw new Error('Incomplete CSV export');
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}
document.querySelector('#restore').addEventListener('click', async () => {
  const button = document.querySelector('#restore');
  const result = document.querySelector('#result');
  const file = document.querySelector('#file').files[0];
  if (!file) { result.textContent = 'Choose a recovery CSV first.'; return; }
  button.disabled = true;
  result.textContent = 'Saving recovery copy and restoring Redis records…';
  try {
    const rows = csvRows(await file.text()); const header = rows.shift();
    const outcomes = [];
    const encodeRow = row => row.map(cell => '"' + cell.replaceAll('"', '""') + '"').join(',');
    for (let i = 0; i < rows.length; i++) {
      result.textContent = `Restoring batch ${i + 1} of ${rows.length}…\n` + JSON.stringify(outcomes, null, 2);
      const csv = encodeRow(header) + '\n' + encodeRow(rows[i]) + '\n';
      const response = await fetch('/api/records/restore', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({csv})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Recovery failed');
      outcomes.push(data);
    }
    result.textContent = 'Recovery completed.\n' + JSON.stringify(outcomes, null, 2);
  } catch (error) { result.textContent = error.message; }
  finally { button.disabled = false; }
});
