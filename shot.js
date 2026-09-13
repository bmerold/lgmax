const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 1400, height: 1250 } });
  const errs = [];
  p.on('pageerror', e => errs.push('PAGEERROR: ' + String(e)));
  p.on('console', m => { if (m.type() === 'error' && !/net::ERR/.test(m.text())) errs.push(m.text()); });
  await p.goto('file://' + __dirname + '/app.html', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1500);
  await p.click('[data-view="sec"]'); await p.waitForTimeout(300);
  await p.click('[data-sec="13"]'); await p.waitForTimeout(600);
  await p.evaluate(() => { const el=[...document.querySelectorAll('.healnote')][0]; if(el) el.scrollIntoView({block:'center'}); });
  await p.waitForTimeout(400);
  await p.screenshot({ path: 'shot-heal.png' });
  console.log(errs.length ? 'ERRORS:\n' + errs.join('\n') : 'no console errors');
  await b.close();
})();
