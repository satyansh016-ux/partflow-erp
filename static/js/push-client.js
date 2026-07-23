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
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }

    await fetch('/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    });

    localStorage.setItem('pf_push_enabled', '1');
    if (btn) {
      btn.textContent = 'Notifications Enabled ✓';
      btn.disabled = true;
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
    btn.textContent = 'Notifications Enabled ✓';
    btn.disabled = true;
  }
  btn.addEventListener('click', function () {
    pfEnablePushNotifications(btn);
  });
});
