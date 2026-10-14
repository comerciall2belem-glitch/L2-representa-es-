import {
  collections,
  emptySnapshot,
  type Entity,
  type Kind,
  type Pending,
  type Receipt,
  type Snapshot,
} from "./types";
export interface SQL {
  execAsync(sql: string): Promise<void>;
  runAsync(sql: string, ...params: any[]): Promise<any>;
  getFirstAsync<T>(sql: string, ...params: any[]): Promise<T | null>;
  getAllAsync<T>(sql: string, ...params: any[]): Promise<T[]>;
  withExclusiveTransactionAsync(fn: (tx: SQL) => Promise<void>): Promise<void>;
}
export const schema = `
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS queue(change_id TEXT PRIMARY KEY,kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 payload TEXT NOT NULL,base TEXT,state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',remote TEXT);
CREATE INDEX IF NOT EXISTS queue_ready ON queue(state,next_at);
CREATE TABLE IF NOT EXISTS drafts(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_files(key TEXT PRIMARY KEY,content TEXT NOT NULL,downloaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,expires_at REAL);
PRAGMA user_version=1;`;

export class LocalStore {
  constructor(
    public db: SQL,
    public user: string,
  ) {}
  async initialize() {
    const version = await this.db.getFirstAsync<{ user_version: number }>(
      "PRAGMA user_version",
    );
    if ((version?.user_version || 0) > 1)
      throw new Error("Versão local mais nova; atualize o aplicativo.");
    await this.db.execAsync(schema);
    const owner = await this.db.getFirstAsync<{ value: string }>(
      "SELECT value FROM meta WHERE key=?",
      "owner",
    );
    if (owner && owner.value !== this.user)
      throw new Error("Banco pertence a outro usuário.");
    await this.db.runAsync(
      "INSERT OR IGNORE INTO meta VALUES(?,?)",
      "owner",
      this.user,
    );
  }
  async snapshot(): Promise<Snapshot> {
    const row = await this.db.getFirstAsync<{ value: string }>(
      "SELECT value FROM meta WHERE key=?",
      "snapshot",
    );
    return row ? JSON.parse(row.value) : emptySnapshot();
  }
  async view(): Promise<Snapshot> {
    const s = await this.snapshot();
    // Business totals use confirmed snapshot. Pending entities are shown separately.
    return s;
  }
  async pending(): Promise<Pending[]> {
    const rows = await this.db.getAllAsync<any>(
      "SELECT * FROM queue WHERE state!='acknowledged' ORDER BY rowid",
    );
    return rows.map((r) => ({
      changeId: r.change_id,
      type: r.kind,
      data: JSON.parse(r.payload),
      base: r.base ? JSON.parse(r.base) : null,
      state: r.state,
      attempts: r.attempts,
      nextAt: r.next_at,
      error: r.error,
      remote: r.remote ? JSON.parse(r.remote) : null,
    }));
  }
  async queue(type: Kind, data: Entity, changeId: string) {
    if (!data.id) throw new Error("Registro sem identificação.");
    await this.db.withExclusiveTransactionAsync(async (tx) => {
      const existing = await tx.getFirstAsync<any>(
        "SELECT 1 FROM queue WHERE kind=? AND entity_id=? AND state!='acknowledged'",
        type,
        data.id,
      );
      if (existing)
        throw new Error(
          "Este registro tem alteração pendente; resolva antes de editar.",
        );
      const saved = await tx.getFirstAsync<{ value: string }>(
        "SELECT value FROM meta WHERE key=?",
        "snapshot",
      );
      const s: Snapshot = saved ? JSON.parse(saved.value) : emptySnapshot();
      const base =
        (s[collections[type]] || []).find((x: Entity) => x.id === data.id) ||
        null;
      await tx.runAsync(
        "INSERT INTO queue(change_id,kind,entity_id,payload,base) VALUES(?,?,?,?,?)",
        changeId,
        type,
        data.id,
        JSON.stringify(data),
        base ? JSON.stringify(base) : null,
      );
    });
  }
  async apply(snapshot: Snapshot, receipts: Receipt[] = []) {
    if (!Array.isArray(snapshot.clients) || !Array.isArray(snapshot.orders))
      throw new Error("Resposta de sincronização inválida.");
    await this.db.withExclusiveTransactionAsync(async (tx) => {
      for (const receipt of receipts) {
        if (!["acknowledged", "conflict", "rejected"].includes(receipt.state))
          throw new Error("Recibo desconhecido.");
        const known = await tx.getFirstAsync<any>(
          "SELECT * FROM queue WHERE change_id=?",
          receipt.changeId,
        );
        if (!known) throw new Error("Recibo não pertence à fila local.");
        await tx.runAsync(
          "UPDATE queue SET state=?,error=?,remote=? WHERE change_id=?",
          receipt.state,
          receipt.error || "",
          receipt.remote ? JSON.stringify(receipt.remote) : null,
          receipt.changeId,
        );
      }
      await tx.runAsync(
        "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        "snapshot",
        JSON.stringify(snapshot),
      );
      await tx.runAsync(
        "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        "lastSync",
        new Date().toISOString(),
      );
    });
  }
  async retry(changeId: string, now: number, error: string) {
    const row = await this.db.getFirstAsync<{ attempts: number }>(
      "SELECT attempts FROM queue WHERE change_id=?",
      changeId,
    );
    const n = (row?.attempts || 0) + 1;
    await this.db.runAsync(
      "UPDATE queue SET attempts=?,next_at=?,error=? WHERE change_id=?",
      n,
      now + Math.min(300000, 2000 * 2 ** Math.min(n, 7)),
      error,
      changeId,
    );
  }
  async retainError(changeId: string, error: string) {
    await this.db.runAsync(
      "UPDATE queue SET error=? WHERE change_id=?",
      error,
      changeId,
    );
  }
  async resolve(changeId: string, data: Entity | null, newId: string) {
    await this.db.withExclusiveTransactionAsync(async (tx) => {
      const row = await tx.getFirstAsync<any>(
        "SELECT * FROM queue WHERE change_id=?",
        changeId,
      );
      if (!row || !["conflict", "rejected"].includes(row.state))
        throw new Error(
          "Só erros definitivos ou conflitos podem ser revisados.",
        );
      if (data) {
        const remote = row.remote ? JSON.parse(row.remote) : null;
        await tx.runAsync(
          "INSERT INTO queue(change_id,kind,entity_id,payload,base) VALUES(?,?,?,?,?)",
          newId,
          row.kind,
          row.entity_id,
          JSON.stringify(data),
          remote ? JSON.stringify(remote) : row.base,
        );
      }
      await tx.runAsync(
        "UPDATE queue SET state='acknowledged',error='Resolvido pelo usuário' WHERE change_id=?",
        changeId,
      );
    });
  }
  async draft(key: string, payload: any) {
    await this.db.runAsync(
      "INSERT INTO drafts VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
      key,
      JSON.stringify(payload),
    );
  }
  async readDraft<T>(key: string): Promise<T | null> {
    const r = await this.db.getFirstAsync<{ payload: string }>(
      "SELECT payload FROM drafts WHERE key=?",
      key,
    );
    return r ? JSON.parse(r.payload) : null;
  }
  async removeDraft(key: string) {
    await this.db.runAsync("DELETE FROM drafts WHERE key=?", key);
  }
  async saveCatalog(key: string, content: string) {
    await this.db.runAsync(
      "INSERT INTO catalog_files VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,downloaded_at=excluded.downloaded_at",
      key,
      content,
      new Date().toISOString(),
    );
  }
  async readCatalog(
    key: string,
  ): Promise<{ content: string; downloaded_at: string } | null> {
    return this.db.getFirstAsync(
      "SELECT content,downloaded_at FROM catalog_files WHERE key=?",
      key,
    );
  }
  async acquire(owner: string, now: number): Promise<boolean> {
    let success = false;
    await this.db.withExclusiveTransactionAsync(async (tx) => {
      const lease = await tx.getFirstAsync<{
        owner: string;
        expires_at: number;
      }>("SELECT * FROM lease WHERE id=1");
      if (lease && lease.expires_at > now) return;
      await tx.runAsync(
        "INSERT INTO lease VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at",
        owner,
        now + 120000,
      );
      success = true;
    });
    return success;
  }
  async release(owner: string) {
    await this.db.runAsync("DELETE FROM lease WHERE id=1 AND owner=?", owner);
  }
  async renew(owner: string, now: number) {
    await this.db.runAsync(
      "UPDATE lease SET expires_at=? WHERE id=1 AND owner=?",
      now + 120000,
      owner,
    );
  }
}
