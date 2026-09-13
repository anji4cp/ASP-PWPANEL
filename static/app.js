async function refreshStatus() {
  const realm = document.querySelector('#realm-state');
  const grid = document.querySelector('#status-grid');
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('status request failed');
    const data = await response.json();
    realm.classList.toggle('online', data.online);
    realm.classList.toggle('offline', !data.online);
    realm.querySelector('span:last-child').textContent = data.online ? 'Server online' : 'Server belum lengkap';
    document.querySelectorAll('.status-text').forEach((element) => {
      element.textContent = data.online ? 'Online' : 'Maintenance';
      element.classList.toggle('online-text', data.online);
    });
    if (!grid) return;
    grid.replaceChildren(...Object.entries(data.services).map(([name, online]) => {
      const item = document.createElement('div');
      item.className = `service ${online ? 'online' : 'offline'}`;
      const label = document.createElement('span');
      label.textContent = name;
      const value = document.createElement('b');
      value.textContent = online ? 'Online' : 'Offline';
      item.append(label, value);
      return item;
    }));
  } catch (_) {
    realm.className = 'realm-state offline';
    realm.querySelector('span:last-child').textContent = 'Status tidak tersedia';
    document.querySelectorAll('.status-text').forEach((element) => {
      element.textContent = 'Tidak tersedia';
    });
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
