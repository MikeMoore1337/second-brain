/// <reference types="vite-plugin-pwa/client" />

type UpdateListener = () => void;

const listeners = new Set<UpdateListener>();
let updateServiceWorker: ((reloadPage?: boolean) => Promise<void>) | undefined;
let updateAvailable = false;
let registrationStarted = false;

export function registerPwa(): void {
  if (
    !import.meta.env.PROD ||
    typeof window === "undefined" ||
    !("serviceWorker" in navigator) ||
    registrationStarted
  ) {
    return;
  }

  registrationStarted = true;
  void import("virtual:pwa-register")
    .then(({ registerSW }) => {
      updateServiceWorker = registerSW({
        immediate: true,
        onNeedRefresh: () => {
          updateAvailable = true;
          listeners.forEach((listener) => listener());
        },
        onRegisteredSW: (_swUrl, registration) => {
          if (!registration) return;
          const checkForUpdate = () => {
            if (document.visibilityState !== "visible" || !navigator.onLine) return;
            void registration.update().catch(() => undefined);
          };
          window.addEventListener("online", checkForUpdate);
          document.addEventListener("visibilitychange", checkForUpdate);
        },
      });
    })
    .catch(() => {
      registrationStarted = false;
    });
}

export function hasPwaUpdate(): boolean {
  return updateAvailable;
}

export function subscribeToPwaUpdate(listener: UpdateListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export async function applyPwaUpdate(): Promise<void> {
  if (updateServiceWorker === undefined) return;
  await updateServiceWorker(true);
}
