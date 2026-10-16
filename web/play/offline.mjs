/** Cache only this app's public static assets. Audio recordings never enter fetch. */
export async function registerOffline() {
  if (!('serviceWorker' in navigator) || !globalThis.isSecureContext) return null;
  try {
    return await navigator.serviceWorker.register(new URL('../sw.js', import.meta.url),
      { scope: new URL('../', import.meta.url).pathname });
  } catch { return null; } // Storage limits must never block making a clip.
}
