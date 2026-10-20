// Optional helper (task 2.2). Paste into the browser console (F12 > Console) on the CampusChina list page
// https://www.campuschina.org/universities/regions.html?key=%7CMaster%7CEnglish   (repeat for Doctoral / Bachelor)
// It clicks through the pager like a person would, collects the <li> markup of each page and downloads one .html file.
// Put the downloaded files into pipeline/inputs/campuschina/ . UNTESTED against the live pager: if it stops, send me the
// text it prints (it shows the pager markup it could not use).
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const items = () => [...document.querySelectorAll('li')].filter(li => li.querySelector('a[href*="universities_art/index.html?key="]'));
  const keyOf = li => (li.innerHTML.match(/key=([0-9A-F]+)/i) || [])[1] || '';
  const seen = new Map();
  const grab = () => items().forEach(li => { const k = keyOf(li); if (k && !seen.has(k)) seen.set(k, li.outerHTML); });
  const firstKey = () => (items()[0] ? keyOf(items()[0]) : '');
  const pagers = () => [...document.querySelectorAll('a, span, li, button')].filter(e => /^(next|下一页|>|›|»)$/i.test((e.textContent || '').trim()) && e.offsetParent);
  grab();
  for (let page = 1; page < 80; page++) {
    const before = firstKey();
    const next = pagers()[0];
    if (!next) { console.log('No usable "next" control. Pager markup:\n', (document.querySelector('[class*=page]') || document.body).outerHTML.slice(0, 1500)); break; }
    next.click();
    let waited = 0;
    while (firstKey() === before && waited < 15000) { await sleep(300); waited += 300; }
    if (firstKey() === before) { console.log('Page did not change after clicking next; stopping at page', page); break; }
    await sleep(800);
    grab();
    console.log('page', page + 1, 'collected so far:', seen.size);
  }
  const blob = new Blob(['<ul>\n' + [...seen.values()].join('\n') + '\n</ul>'], { type: 'text/html' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'campuschina_list_' + Date.now() + '.html';
  document.body.appendChild(a); a.click(); a.remove();
  console.log('DONE. Universities collected:', seen.size);
})();
