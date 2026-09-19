async function refreshStatus() {
  const realm = document.querySelector('#realm-state');
  const grid = document.querySelector('#status-grid');
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('status request failed');
    const data = await response.json();
    realm.classList.toggle('online', data.online);
    realm.classList.toggle('offline', !data.online);
    realm.querySelector('span:last-child').textContent = data.online ? 'Server online' : 'Server offline';
    document.querySelectorAll('.status-text').forEach((element) => {
      element.textContent = data.online ? 'Online' : 'Offline';
      element.classList.toggle('online-text', data.online);
    });
    if (!grid) return;
    const status = grid.querySelector('#server-status');
    const players = grid.querySelector('#players-online');
    status.classList.toggle('online', data.online);
    status.classList.toggle('offline', !data.online);
    status.querySelector('b').textContent = data.online ? 'Online' : 'Offline';
    players.querySelector('b').textContent = Number.isSafeInteger(data.players_online)
      ? data.players_online.toLocaleString('id-ID') : '—';
  } catch (_) {
    realm.className = 'realm-state offline';
    realm.querySelector('span:last-child').textContent = 'Status tidak tersedia';
    document.querySelectorAll('.status-text').forEach((element) => {
      element.textContent = 'Tidak tersedia';
    });
    if (grid) {
      grid.querySelector('#server-status').querySelector('b').textContent = 'Tidak tersedia';
      grid.querySelector('#players-online').querySelector('b').textContent = '—';
    }
  }
}

refreshStatus();
setInterval(refreshStatus, 30000);

const adminNavToggle = document.querySelector('#admin-nav-toggle');
document.querySelectorAll('.admin-nav a').forEach((link) => {
  link.addEventListener('click', () => {
    if (adminNavToggle) adminNavToggle.checked = false;
    document.querySelectorAll('.admin-nav a').forEach((item) => item.classList.remove('active'));
    link.classList.add('active');
  });
});

function updateSafeShutdownCountdown() {
  const countdown = document.querySelector('[data-shutdown-at]');
  if (!countdown) return;
  const executeAt = Number.parseInt(countdown.dataset.shutdownAt, 10);
  if (!Number.isFinite(executeAt)) return;
  const remaining = Math.max(0, executeAt - Math.floor(Date.now() / 1000));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const seconds = remaining % 60;
  countdown.textContent = hours > 0
    ? `${hours}j ${minutes}m ${seconds}d tersisa`
    : minutes > 0 ? `${minutes}m ${seconds}d tersisa` : `${seconds} detik tersisa`;
  countdown.classList.toggle('countdown-critical', remaining <= 30);
}

updateSafeShutdownCountdown();
setInterval(updateSafeShutdownCountdown, 1000);
