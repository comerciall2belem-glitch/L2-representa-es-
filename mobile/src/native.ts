import * as SQLite from "expo-sqlite";
import * as SecureStore from "expo-secure-store";
import * as Crypto from "expo-crypto";
import * as TaskManager from "expo-task-manager";
import * as BackgroundTask from "expo-background-task";
import { LocalStore, type SQL } from "./store";
import { L2API } from "./api";
import { SyncEngine } from "./sync";
import type { Session } from "./types";

export const TASK = "L2_OFFLINE_SYNC";
const SESSION_KEY = "l2.active.session";
export type SavedSession = Session & { base: string; offlineUntil: number };
const stores = new Map<string, Promise<LocalStore>>();
export const id = () => Crypto.randomUUID();
export async function remember(session: SavedSession) {
  await SecureStore.setItemAsync(SESSION_KEY, JSON.stringify(session));
}
export async function restored(): Promise<SavedSession | null> {
  const value = await SecureStore.getItemAsync(SESSION_KEY);
  return value ? JSON.parse(value) : null;
}
export async function forget() {
  await SecureStore.deleteItemAsync(SESSION_KEY);
  await BackgroundTask.unregisterTaskAsync(TASK).catch(() => {});
}
export async function openStore(session: SavedSession): Promise<LocalStore> {
  const key = session.base + "|" + session.user;
  if (!stores.has(key))
    stores.set(
      key,
      (async () => {
        const hash = await Crypto.digestStringAsync(
          Crypto.CryptoDigestAlgorithm.SHA256,
          key,
        );
        let secret = await SecureStore.getItemAsync("l2.db." + hash);
        if (!secret) {
          secret = await Crypto.digestStringAsync(
            Crypto.CryptoDigestAlgorithm.SHA256,
            id() + id(),
          );
          await SecureStore.setItemAsync("l2.db." + hash, secret);
        }
        const db = await SQLite.openDatabaseAsync("l2_" + hash + ".db");
        await db.execAsync(`PRAGMA key = "x'${secret}'";`);
        const cipher = await db.getFirstAsync<any>("PRAGMA cipher_version");
        if (!cipher || !Object.values(cipher).some(Boolean)) {
          await db.closeAsync();
          throw new Error(
            "Use o development build ou APK com SQLCipher. Expo Go não oferece esta proteção.",
          );
        }
        await db.execAsync("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;");
        const store = new LocalStore(db as unknown as SQL, session.user);
        await store.initialize();
        return store;
      })().catch((error) => {
        stores.delete(key);
        throw error;
      }),
    );
  return stores.get(key)!;
}
TaskManager.defineTask(TASK, async () => {
  try {
    const session = await restored();
    if (!session || session.offlineUntil < Date.now())
      return BackgroundTask.BackgroundTaskResult.Success;
    const store = await openStore(session);
    await new SyncEngine(
      store,
      new L2API(session.base, session.token),
      id,
    ).run();
    return BackgroundTask.BackgroundTaskResult.Success;
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});
export async function registerBackground() {
  if (
    (await BackgroundTask.getStatusAsync()) ===
    BackgroundTask.BackgroundTaskStatus.Available
  ) {
    await BackgroundTask.registerTaskAsync(TASK, { minimumInterval: 15 });
  }
}
