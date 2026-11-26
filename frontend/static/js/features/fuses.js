/* EVbro — Más → Coches → «Fusibles»: the two fuse boxes of the car, every position a square to tap (its rating as its colour and
 * number); tapping one says what it feeds. A search box finds the fuse of something that stopped working («bocina», «USB»…).
 * Data: GET /api/vehicles/{vin}/fuses (backend services/fuse_boxes.py). The squares are in the order of their numbers, NOT in
 * their place inside the box: the cover of the box is what shows where each one sits, and the page says so.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  let data = null;          // the answer for the car on screen
  let dataVin = null;
  let boxKey = null;
  let picked = null;
  let byNumber = false;     // false: each fuse in its place in the box (when the box's arrangement is known); true: in number order

  const plain = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  const amps = (a) => (a === null || a === undefined ? '' : `${Number(a).toLocaleString((window.I18n && I18n.locale) || 'es-ES')} A`);
  const box = () => (data ? data.boxes.find((b) => b.key === boxKey) || data.boxes[0] : null);

  /** The groups of a box, in the order they are drawn: big fuses, the rest by number, the high-current ones, relays. */
  function groups(items) {
    const is = (prefix) => (i) => i.id.startsWith(prefix);
    const fuses = items.filter((i) => i.kind !== 'relay');
    const big = fuses.filter((i) => is('RSB')(i) || is('SB')(i));
    const rest = fuses.filter((i) => !big.includes(i));
    const high = rest.filter((i) => (i.amps || 0) >= 50);
    return [
      [t('Fusibles grandes'), big],
      [t('Fusibles'), rest.filter((i) => !high.includes(i))],
      [t('Alta intensidad'), high],
      [t('Relés'), items.filter((i) => i.kind === 'relay')],
    ].filter(([, list]) => list.length);
  }

  function showDetail(item) {
    picked = item ? item.id : null;
    document.querySelectorAll('#fzGrid .fz-cell').forEach((c) => c.classList.toggle('is-picked', c.dataset.id === picked));
    const panel = $('fzDetail');
    panel.replaceChildren();
    if (!item) {
      panel.textContent = t('Toca un fusible para ver qué alimenta.');
      panel.classList.add('is-empty');
      return;
    }
    panel.classList.remove('is-empty');
    const head = document.createElement('div');
    head.className = 'fz-detail-head';
    const id = document.createElement('strong');
    id.textContent = item.id;
    const rating = document.createElement('span');
    rating.className = 'fz-detail-amps';
    rating.textContent = item.kind === 'relay' ? t('Relé') : (amps(item.amps) || (item.kind === 'empty' ? t('Sin fusible') : t('Amperaje dudoso')));
    head.append(id, rating);
    const text = document.createElement('p');
    text.textContent = item.kind === 'empty' ? t('Esta posición no lleva fusible.') : item.text;
    panel.append(head, text);
    if (item.doubt) {
      const doubt = document.createElement('p');
      doubt.className = 'fz-doubt';
      doubt.textContent = item.doubt;
      panel.append(doubt);
    }
  }

  function draw() {
    const current = box();
    if (!current) return;
    $('fzWhere').textContent = current.where;
    document.querySelectorAll('#fzBoxes button').forEach((b) => {
      const on = b.dataset.box === current.key;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const query = plain($('fzSearch').value).trim();
    const grid = $('fzGrid');
    grid.replaceChildren();
    let hits = 0;
    let firstHit = null;
    /** One fuse as a square to tap. */
    const cellFor = (item) => {
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = `fz-cell fz-${item.kind}`;
      cell.dataset.id = item.id;
      if (item.amps !== null && item.amps !== undefined) cell.dataset.a = String(item.amps);
      if (item.doubt) cell.classList.add('has-doubt');
      const match = !query || plain(`${item.id} ${item.text} ${amps(item.amps)}`).includes(query);
      if (!match) cell.classList.add('is-dim');
      else if (query) { hits += 1; firstHit = firstHit || cell; }
      if (item.id === picked) cell.classList.add('is-picked');
      const name = document.createElement('span');
      name.className = 'fz-id';
      name.textContent = item.id;
      const value = document.createElement('span');
      value.className = 'fz-a';
      value.textContent = item.kind === 'relay' ? '' : (item.amps !== null && item.amps !== undefined ? String(item.amps).replace('.', ',') : (item.kind === 'empty' ? '—' : '?'));
      cell.append(name, value);
      cell.setAttribute('aria-label', `${item.id} ${amps(item.amps)} ${item.text}`.trim());
      cell.addEventListener('click', () => showDetail(item));
      return cell;
    };
    const group = (title, list) => {
      const heading = document.createElement('h4');
      heading.className = 'fz-group';
      heading.textContent = title;
      const cells = document.createElement('div');
      cells.className = 'fz-cells';
      list.forEach((item) => cells.appendChild(cellFor(item)));
      grid.append(heading, cells);
    };
    const layout = current.layout;
    const views = $('fzViews');
    views.hidden = !layout;
    views.querySelectorAll('button').forEach((b) => b.classList.toggle('is-active', (b.dataset.view === 'number') === byNumber));
    $('fzView').textContent = layout && !byNumber ? layout.view : (layout ? '' : t('De esta caja todavía no se sabe dónde va cada fusible: van por orden de número.'));
    if (layout && !byNumber) {
      // each fuse where it sits in the box; the drawing is wider than a phone, so it scrolls
      const scroll = document.createElement('div');
      scroll.className = 'fz-map-scroll';
      const map = document.createElement('div');
      map.className = 'fz-map';
      map.style.aspectRatio = String(layout.ratio);                  // CSSOM: allowed by the CSP (no style attribute)
      // wide enough for its narrowest fuse to be tapped, and (a box taller than wide) no taller than a screen
      const narrowest = Math.min(...Object.values(layout.cells).map(([, , width, height]) => Math.min(width, height / layout.ratio)));
      const least = Math.ceil(2600 / narrowest);
      map.style.minWidth = `${least}px`;
      map.style.maxWidth = `${Math.max(least, Math.round(780 * layout.ratio))}px`;
      const place = (el, [left, top, width, height]) => {
        el.style.left = `${left}%`; el.style.top = `${top}%`; el.style.width = `${width}%`; el.style.height = `${height}%`;
        return (width * layout.ratio) / height;                       // its shape on screen: below 1, taller than wide
      };
      (layout.marks || []).forEach((mark) => {
        const el = document.createElement('div');
        el.className = 'fz-mark';
        el.textContent = mark.label;
        place(el, mark.at);
        map.appendChild(el);
      });
      const placed = current.items.filter((i) => layout.cells[i.id]);
      placed.forEach((item) => {
        const cell = cellFor(item);
        cell.classList.add('fz-at');
        const shape = place(cell, layout.cells[item.id]);
        if (shape < 0.7) cell.classList.add('is-tall');
        else if (shape < 1.6) cell.classList.add('is-big');
        map.appendChild(cell);
      });
      scroll.appendChild(map);
      grid.appendChild(scroll);
      // on a phone the drawing does not fit: say that it moves
      if (scroll.scrollWidth > scroll.clientWidth + 4) $('fzView').textContent = `${layout.view} ${t('Desliza el dibujo para verla entera.')}`;
      const relays = current.items.filter((i) => !layout.cells[i.id] && i.kind === 'relay');
      if (relays.length) group(t('Relés (no están en esta cara)'), relays);
    } else {
      groups(current.items).forEach(([title, list]) => group(title, list));
    }
    if (firstHit && firstHit.classList.contains('fz-at')) firstHit.scrollIntoView({ block: 'nearest', inline: 'center' });
    const found = $('fzFound');
    found.hidden = !query;
    found.textContent = query ? (hits ? t('{p0} coincidencias en esta caja.', { p0: hits }) : t('Nada con ese nombre en esta caja: mira en la otra.')) : '';
    // one match: show it straight away
    if (query && hits === 1) showDetail(current.items.find((i) => plain(`${i.id} ${i.text} ${amps(i.amps)}`).includes(query)));
  }

  function setView(view) {
    byNumber = view === 'number';
    draw();
  }

  function pickBox(key) {
    boxKey = key;
    showDetail(null);
    draw();
  }

  async function open() {
    if (typeof currentVin === 'undefined' || !currentVin) return;
    const modal = $('fusesModal');
    if (!modal) return;
    if (dataVin !== currentVin) {
      try {
        const lang = (window.I18n && I18n.lang) || 'es';
        const res = await fetch(`/api/vehicles/${encodeURIComponent(currentVin)}/fuses?lang=${encodeURIComponent(lang)}`);
        const body = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(body.detail || t('No se pudieron cargar los fusibles.'), 'error'); return; }
        data = body;
        dataVin = currentVin;
        boxKey = data.boxes[0].key;
      } catch (e) {
        showToast(t('No se pudieron cargar los fusibles.'), 'error');
        return;
      }
    }
    $('fzTitle').textContent = t('Fusibles · {p0}', { p0: data.model });
    $('fzSource').textContent = data.source;
    $('fzWarning').hidden = !!data.verified;
    const tabs = $('fzBoxes');
    tabs.replaceChildren();
    data.boxes.forEach((b) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'vp-seg-btn';
      btn.setAttribute('role', 'tab');
      btn.dataset.box = b.key;
      btn.textContent = b.name;
      btn.addEventListener('click', () => pickBox(b.key));
      tabs.appendChild(btn);
    });
    $('fzSearch').value = '';
    showDetail(null);
    draw();
    modal.classList.add('open');
  }

  function close() {
    const modal = $('fusesModal');
    if (modal) modal.classList.remove('open');
  }

  window.Fuses = { open, close, search: draw, setView };
})();
