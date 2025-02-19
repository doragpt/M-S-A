const CACHE_NAME = 'store-activity-cache-v1';
const urlsToCache = [
  'index.html',
  'manifest.json'
  // 必要に応じて他のファイルも追加してください
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        return response || fetch(event.request);
      })
  );
});
