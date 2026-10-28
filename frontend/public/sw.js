/*
 * 前端外壳缓存（v3.11）：只缓存静态资源与页面外壳，绝不缓存 /api/ 数据。
 *
 * 设计取舍：
 * - 导航请求 network-first：有网时永远拿最新 index.html；断网才回退缓存外壳；
 * - 静态资源 cache-first：Vite 产物带内容哈希，缓存命中不会产生陈旧代码；
 * - /api/ 一律直连网络，不写缓存——业务数据必须实时且带鉴权边界。
 *
 * 离线时页面仍会提示“需要联网”，不做离线写入队列（避免与多端并发冲突）。
 */
const CACHE = 'kjxy-shell-v1'
const SHELL = ['/', '/index.html', '/favicon.svg', '/manifest.webmanifest']

self.addEventListener('install', event => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches
      .keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', event => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(response => {
          const copy = response.clone()
          caches.open(CACHE).then(cache => cache.put('/index.html', copy))
          return response
        })
        .catch(() => caches.match('/index.html').then(hit => hit || caches.match('/'))),
    )
    return
  }

  if (url.pathname.startsWith('/assets/') || SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(request).then(
        hit =>
          hit ||
          fetch(request).then(response => {
            if (response.ok) {
              const copy = response.clone()
              caches.open(CACHE).then(cache => cache.put(request, copy))
            }
            return response
          }),
      ),
    )
  }
})
