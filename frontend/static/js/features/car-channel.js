/* EVBro — Car MQTT channel (status in Ajustes → Sistema).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

const CAR_CHANNEL_BADGES = {
  disabled: ['asleep', 'Desactivado'],
  no_account: ['parked', 'Sin cuenta'],
  no_certs: ['parked', 'Sin certificados'],
  connecting: ['charging', 'Conectando'],
  connected: ['charging', 'Conectando'],
  subscribed: ['driving', 'Escuchando'],
  rejected: ['parked', 'Rechazado'],
  error: ['parked', 'Error'],
  conflict: ['parked', 'Parado'],
};

async function loadCarChannelStatus() {
  try {
    const res = await fetch('/api/car-events/status');
    if (!res.ok) return;
    const s = await res.json();
    const [cls, label] = CAR_CHANNEL_BADGES[s.state] || ['asleep', s.state];
    const badge = document.getElementById('carChannelBadge');
    if (badge) { badge.className = `status-badge ${cls}`; badge.innerText = label; }
    const set = (id, text) => { const el = document.getElementById(id); if (el) el.innerText = text; };
    set('carChannelDetail', s.detail || '—');
    set('carChannelMessages', String(s.messages ?? 0));
    set('carChannelLast', s.last_message_at ? serverDate(s.last_message_at).toLocaleString(I18n.locale) : '—');
    set('carChannelAwake', s.state === 'subscribed' ? (s.moving ? 'En marcha' : (s.awake ? 'Despierto' : 'Dormido')) : '—');
    const btn = document.getElementById('btnCarReconnect');
    if (btn) btn.hidden = s.state === 'disabled';
  } catch (e) {
    console.warn('Car channel status:', e);
  }
}

async function reconnectCarChannel() {
  const btn = document.getElementById('btnCarReconnect');
  if (btn) btn.disabled = true;
  try {
    await fetch('/api/car-events/reconnect', { method: 'POST' });
    await loadCarChannelStatus();
  } finally {
    if (btn) btn.disabled = false;
  }
}
