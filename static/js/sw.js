self.addEventListener('push', function (event) {
  let data = { title: 'PartFlow ERP', body: '', link: '/notifications' };
  try {
    if (event.data) data = event.data.json();
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }

  const options = {
    body: data.body || '',
    icon: '/static/img/logo.svg',
    badge: '/static/img/logo.svg',
    data: { link: data.link || '/notifications' },
  };

  event.waitUntil(self.registration.showNotification(data.title || 'PartFlow ERP', options));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || '/notifications';
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(function (clientList) {
      for (const client of clientList) {
        if (client.url.includes(link) && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(link);
    })
  );
});
