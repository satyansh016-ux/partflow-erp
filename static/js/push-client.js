function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const output = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) output[i] = rawData.charCodeAt(i);
  return output;
}

async function pfEnablePushNotifications(btn) {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    alert('Push notifications are not supported on this browser.');
    return;
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    alert('Notifications permission was not granted.');
    return;
  }

  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    const keyRes = await fetch('/push/vapid-public-key');
    const { publicKey } = await keyRes.json();
    if (!publicKey) {
      alert('Push is not configured on the server yet.');
      return;
    }

    let sub = await reg.pushManager.getSubscription();
    if (sub) {
      // Force a fresh subscription every time the button is clicked - an
      // existing one might be bound to an old/mismatched VAPID public key
      // from a prior attempt, which would silently fail every push send.
      await sub.unsubscribe();
    }
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    await fetch('/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });

    localStorage.setItem('pf_push_enabled', '1');
    if (btn) {
      btn.innerHTML = '<i class="bi bi-check-circle"></i> Enabled — click to re-check';
    }
  } catch (err) {
    console.error('Push subscription failed:', err);
    alert('Could not enable push notifications. Please try again.');
  }
}

document.addEventListener('DOMContentLoaded', function () {
  const btn = document.getElementById('pfEnablePushBtn');
  if (!btn) return;
  if (localStorage.getItem('pf_push_enabled') === '1' && Notification.permission === 'granted') {
    btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Re-check Push Notifications';
  }
  btn.addEventListener('click', function () {
    pfEnablePushNotifications(btn);
  });
});

// ── LOCAL NOTIFICATIONS (no server push needed, works immediately) ──
// Fires a real OS notification the instant an action succeeds on THIS
// device, using the same mechanism GarageTrack uses - no VAPID keys, no
// backend push-sending, no subscription needed. This only fires for
// actions YOU take on THIS device (it can't notify other devices/other
// people - that's what the real server push above is for), but it's
// simple and always works once notification permission is granted.
document.addEventListener('DOMContentLoaded', async function () {
  const notifyEl = document.querySelector('[data-pf-local-notify]');
  if (!notifyEl) return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;

  try {
    const reg = await navigator.serviceWorker.register('/sw.js');
    const message = notifyEl.textContent.trim().replace(/\s+/g, ' ');
    await reg.showNotification('PartFlow ERP', {
      body: message,
      icon: '/static/img/logo.svg',
      badge: '/static/img/logo.svg',
      tag: 'pf-local-' + Date.now(),
      vibrate: [200, 100, 200],
    });
  } catch (e) {
    console.warn('Local notification failed:', e);
  }
});
