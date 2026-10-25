// ExamRadar service worker. It caches nothing, because every page and result must come live from the local
// server. It exists so Chromium offers "Install app", and so desktop reminders can be shown and clicked.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});

// Clicking a reminder brings ExamRadar to the front (or opens it) and shows the schedule.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) {
      if ('focus' in c) { c.postMessage({ type: 'open-schedule' }); return c.focus(); }
    }
    return self.clients.openWindow('/');
  }));
});
