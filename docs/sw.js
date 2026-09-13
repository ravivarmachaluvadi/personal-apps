/* sw.js — a minimal service worker so the pages can show notifications on Android.
 *
 * Chrome for Android refuses `new Notification()` from a page; it only shows notifications raised
 * through a service worker registration (`registration.showNotification`). Spine Bell and Tally Board
 * register this file and use it for their banners. There is no fetch handler, so pages, scripts and
 * the Drive calls load straight from the network exactly as before; nothing is cached here.
 *
 * Limits worth knowing: this does not keep a page alive in the background. A phone browser still
 * pauses a page that is not on screen, so a countdown cannot ring from the background; Spine Bell's
 * "hand off to the Clock app" link (Android) is the reliable route for that.
 */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || './';
  var base = url.split('#')[0];
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url.split('#')[0] === base) return list[i].focus();
    }
    return self.clients.openWindow(url);
  }));
});
