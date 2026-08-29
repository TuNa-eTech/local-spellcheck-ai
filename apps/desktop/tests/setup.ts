// Node 26 exposes a global `localStorage` getter that yields `undefined` unless
// the process was started with `--localstorage-file`. In the jsdom environment
// that global shadows the window implementation, so the UI's persistence guard
// would be exercised against a missing API instead of a working Storage.
// Install a minimal in-memory Storage when the environment does not provide one.
if (!globalThis.localStorage) {
  const entries = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return entries.size;
    },
    clear() {
      entries.clear();
    },
    getItem(key: string) {
      return entries.has(String(key)) ? entries.get(String(key))! : null;
    },
    key(index: number) {
      return [...entries.keys()][index] ?? null;
    },
    removeItem(key: string) {
      entries.delete(String(key));
    },
    setItem(key: string, value: string) {
      entries.set(String(key), String(value));
    },
  };
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get: () => storage,
  });
}
