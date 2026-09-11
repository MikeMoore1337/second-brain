/// <reference lib="webworker" />

import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<string | { revision: string | null; url: string }>;
};

const OFFLINE_URL = "/offline.html";
const OFFLINE_RESPONSE = new Response(
  "<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\"><title>Second Brain — нет соединения</title><body><h1>Сеть недоступна</h1><p>Повторите попытку после восстановления соединения.</p></body>",
  {
    status: 503,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  },
);

// The injected list contains only Vite assets, branded public assets, and the
// non-private offline shell. No API/auth/vault response can enter precache.
precacheAndRoute(self.__WB_MANIFEST);
cleanupOutdatedCaches();

self.addEventListener("message", (event) => {
  if (event.data && typeof event.data === "object" && event.data.type === "SKIP_WAITING") {
    void self.skipWaiting();
  }
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function isApiOrAuthPath(pathname: string): boolean {
  return (
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname === "/login" ||
    pathname.startsWith("/auth/")
  );
}

function isNavigationRequest(request: Request): boolean {
  return request.mode === "navigate" || request.destination === "document";
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  // API and OAuth traffic is deliberately left to the browser's network path.
  // It is never wrapped in respondWith(), cache.match(), or cache.put().
  if (isApiOrAuthPath(url.pathname)) return;
  if (!isNavigationRequest(request)) return;

  // Documents are network-only. An offline visit receives a safe, non-private
  // status page instead of a cached authenticated shell or stale user data.
  event.respondWith(
    fetch(request).catch(async () => {
      const cachedOffline = await caches.match(OFFLINE_URL, { ignoreSearch: true });
      return cachedOffline ?? OFFLINE_RESPONSE.clone();
    }),
  );
});
