// APFN League Hub service worker: shows notifications and opens the page when one is tapped.
// It caches nothing, so the hub is always as fresh as the last build.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));

// The server will send JSON: { title, body, url, tag }. url is relative to the site, e.g. "#recap".
self.addEventListener("push", event => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch (_) { msg = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(msg.title || "APFN Pro League", {
    body: msg.body || "",
    tag: msg.tag || undefined,
    icon: "img/icons/icon-192.png",
    badge: "img/icons/icon-192.png",
    data: { url: new URL(msg.url || "./", self.registration.scope).href },
  }));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url || self.registration.scope;
  event.waitUntil((async () => {
    const open = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const tab = open.find(c => c.url.startsWith(self.registration.scope));
    if (tab) { await tab.focus(); return tab.navigate(url).catch(() => {}); }
    return self.clients.openWindow(url);
  })());
});
