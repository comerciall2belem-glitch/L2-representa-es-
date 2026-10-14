import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { LocalStore, type SQL } from "../src/store";
import { L2API, ApiError } from "../src/api";
import { SyncEngine } from "../src/sync";
import { emptySnapshot, type Snapshot, type Operation } from "../src/types";
import {
  cents,
  makeOrder,
  orderTotal,
  phone,
  template,
  workDate,
} from "../src/domain";
import {cleanText,extractText,catalogLink} from '../src/utilities';
import qrcode from 'qrcode-generator';

class NodeSQL implements SQL {
  db: DatabaseSync;
  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path);
  }
  async execAsync(sql: string) {
    this.db.exec(sql);
  }
  async runAsync(sql: string, ...params: any[]) {
    return this.db.prepare(sql).run(...params);
  }
  async getFirstAsync<T>(sql: string, ...params: any[]): Promise<T | null> {
    return (this.db.prepare(sql).get(...params) as T) || null;
  }
  async getAllAsync<T>(sql: string, ...params: any[]): Promise<T[]> {
    return this.db.prepare(sql).all(...params) as T[];
  }
  async withExclusiveTransactionAsync(fn: (tx: SQL) => Promise<void>) {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      await fn(this);
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }
}

async function fixture() {
  const folder = mkdtempSync(join(tmpdir(), "l2-tests-"));
  const path = join(folder, "local.sqlite");
  const db = new NodeSQL(path);
  const store = new LocalStore(db, "Euler");
  await store.initialize();
  let snapshot: Snapshot = {
    ...emptySnapshot(),
    clients: [{ id: "c1", name: "Loja de teste", state: "PA", owner: "Euler" }],
  };
  let count = 0,
    disconnect = false,
    deny = false,
    invalidReceipt = false;
  const receipts = new Map<string, { op: string; result: any }>();
  const server = createServer(async (req, res) => {
    if (deny) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ detail: "Sessão expirada" }));
      return;
    }
    if (req.url === "/api/mobile/v1/snapshot") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(snapshot));
      return;
    }
    if (req.url === "/api/mobile/v1/sync") {
      let body = "";
      for await (const chunk of req) body += chunk;
      const op: Operation = JSON.parse(body).changes[0];
      let result;
      if (receipts.has(op.changeId)) {
        const old = receipts.get(op.changeId)!;
        if (old.op !== JSON.stringify(op)) {
          res.writeHead(409, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ detail: "UUID alterado" }));
          return;
        }
        result = old.result;
      } else {
        const collection = (
          {
            order: "orders",
            visit: "visits",
            task: "tasks",
            interaction: "interactions",
            closing: "closings",
          } as const
        )[op.type];
        const previous =
          snapshot[collection].find((x: any) => x.id === op.data.id) || null;
        if (JSON.stringify(previous) !== JSON.stringify(op.base))
          result = {
            changeId: op.changeId,
            state: "conflict",
            remote: previous,
            error: "Mudou no servidor",
          };
        else {
          count++;
          snapshot[collection] = [
            ...snapshot[collection].filter((x: any) => x.id !== op.data.id),
            {
              ...op.data,
              orderNumber: op.type === "order" ? count : undefined,
            },
          ];
          result = { changeId: op.changeId, state: "acknowledged" };
        }
        receipts.set(op.changeId, { op: JSON.stringify(op), result });
      }
      if (disconnect) {
        disconnect = false;
        req.socket.destroy();
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({ results: invalidReceipt ? [] : [result], snapshot }),
      );
      return;
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as any;
  const api = new L2API("http://127.0.0.1:" + address.port, "test-only-token");
  const engine = new SyncEngine(store, api, randomUUID);
  return {
    store,
    db,
    api,
    engine,
    path,
    folder,
    getSnapshot: () => snapshot,
    setSnapshot: (s: Snapshot) => (snapshot = s),
    getCount: () => count,
    setDisconnect: () => (disconnect = true),
    setDeny: () => (deny = true),
    setInvalid: () => (invalidReceipt = true),
    async dispose() {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      db.db.close();
      rmSync(folder, { recursive: true, force: true });
    },
  };
}
test("E2E HTTP: pedido offline vai ao ERP simulado e atualização volta ao SQLite", async () => {
  const f = await fixture();
  try {
    await f.store.apply(f.getSnapshot());
    await f.store.queue(
      "order",
      {
        id: "o1",
        clientId: "c1",
        amount: 120,
        items: [{ sku: "x", quantity: 2, unitPrice: "60.00" }],
      },
      randomUUID(),
    );
    assert.equal((await f.store.pending()).length, 1);
    assert.equal((await f.store.view()).orders.length, 0);
    await f.engine.run();
    assert.equal(f.getCount(), 1);
    assert.equal((await f.store.pending()).length, 0);
    assert.equal((await f.store.snapshot()).orders[0].orderNumber, 1);
  } finally {
    await f.dispose();
  }
});
test("Resposta perdida após aceite e reconexão não duplicam pedido", async () => {
  const f = await fixture();
  try {
    await f.store.queue(
      "order",
      { id: "o1", clientId: "c1", amount: 1 },
      randomUUID(),
    );
    f.setDisconnect();
    await assert.rejects(() => f.engine.run());
    assert.equal(f.getCount(), 1);
    assert.equal((await f.store.pending()).length, 1);
    await f.engine.run(Date.now() + 1000000);
    assert.equal(f.getCount(), 1);
    assert.equal((await f.store.pending()).length, 0);
  } finally {
    await f.dispose();
  }
});
test("Reinício recupera fila e carrinho persistidos", async () => {
  const f = await fixture();
  try {
    await f.store.queue(
      "visit",
      { id: "v1", clientId: "c1", next: "Voltar" },
      randomUUID(),
    );
    await f.store.draft("cart", { items: ["SKU1"] });
    const reopened = new NodeSQL(f.path);
    const store = new LocalStore(reopened, "Euler");
    await store.initialize();
    assert.equal((await store.pending()).length, 1);
    assert.deepEqual(await store.readDraft("cart"), { items: ["SKU1"] });
    reopened.db.close();
  } finally {
    await f.dispose();
  }
});
test("Conflito preserva conteúdo local e versão remota", async () => {
  const f = await fixture();
  try {
    await f.store.apply({
      ...f.getSnapshot(),
      tasks: [{ id: "t1", text: "Base", user: "Euler" }],
    });
    await f.store.queue(
      "task",
      { id: "t1", text: "Local", user: "Euler" },
      randomUUID(),
    );
    f.setSnapshot({
      ...f.getSnapshot(),
      tasks: [{ id: "t1", text: "Remota", user: "Euler" }],
    });
    await f.engine.run();
    const q = (await f.store.pending())[0];
    assert.equal(q.state, "conflict");
    assert.equal(q.data.text, "Local");
    assert.equal((await f.store.snapshot()).tasks[0].text, "Remota");
  } finally {
    await f.dispose();
  }
});
test("401 preserva fila e exige sessão, sem reconhecer operação", async () => {
  const f = await fixture();
  try {
    await f.store.queue("task", { id: "t1", text: "Cobrar" }, randomUUID());
    f.setDeny();
    await assert.rejects(
      () => f.engine.run(),
      (e: any) => e instanceof ApiError && e.status === 401,
    );
    assert.equal((await f.store.pending())[0].state, "pending");
    assert.equal(f.getCount(), 0);
  } finally {
    await f.dispose();
  }
});
test("Registro e fila são atômicos quando SQLite recusa escrita", async () => {
  const f = await fixture();
  try {
    await f.db.execAsync(
      "CREATE TRIGGER reject_queue BEFORE INSERT ON queue BEGIN SELECT RAISE(ABORT,'disk full'); END;",
    );
    await assert.rejects(() =>
      f.store.queue("order", { id: "o1" }, randomUUID()),
    );
    assert.equal((await f.store.pending()).length, 0);
  } finally {
    await f.dispose();
  }
});
test("Banco de outro usuário é recusado", async () => {
  const f = await fixture();
  try {
    await assert.rejects(
      () => new LocalStore(f.db, "Éricka").initialize(),
      /outro usuário/,
    );
  } finally {
    await f.dispose();
  }
});
test("Recibo ausente não limpa fila", async () => {
  const f = await fixture();
  try {
    await f.store.queue("order", { id: "o1" }, randomUUID());
    f.setInvalid();
    await assert.rejects(() => f.engine.run(), /Recibo ausente/);
    assert.equal((await f.store.pending()).length, 1);
  } finally {
    await f.dispose();
  }
});
test("Uma segunda edição não altera operação pendente", async () => {
  const f = await fixture();
  try {
    await f.store.queue("task", { id: "t1", text: "Primeira" }, randomUUID());
    await assert.rejects(() =>
      f.store.queue("task", { id: "t1", text: "Segunda" }, randomUUID()),
    );
    assert.equal((await f.store.pending())[0].data.text, "Primeira");
  } finally {
    await f.dispose();
  }
});
test("Lease evita segundo worker simultâneo", async () => {
  const f = await fixture();
  try {
    assert.equal(await f.store.acquire("worker1", 100), true);
    assert.equal(await f.store.acquire("worker2", 101), false);
    await f.store.release("worker1");
    assert.equal(await f.store.acquire("worker2", 102), true);
  } finally {
    await f.dispose();
  }
});
test("Dinheiro usa centavos e rejeita quantidade fracionada", () => {
  assert.equal(cents("0.10"), 10);
  assert.equal(orderTotal([{ id: "a", quantity: 3, unitPrice: "0.10" }]), 30);
  assert.throws(() =>
    orderTotal([{ id: "a", quantity: 1.5, unitPrice: "10.00" }]),
  );
  assert.throws(() => cents("NaN"));
});
test("Data comercial é Belém mesmo em UTC", () => {
  assert.equal(workDate(new Date("2026-10-14T02:00:00Z")), "2026-10-13");
});
test("Carrinho multimarcas preserva vendedor e autoria administrativa", () => {
  const o = makeOrder(
    "o",
    { id: "c", state: "PA" },
    "Euler",
    [
      {
        id: "a",
        brand: "Marca A",
        sku: "1",
        state: "PA",
        quantity: 2,
        unitPrice: "10.00",
      },
      {
        id: "b",
        brand: "Marca B",
        sku: "2",
        state: "PA",
        quantity: 1,
        unitPrice: "5.00",
      },
    ],
    "Laís",
    "",
  );
  assert.equal(o.amount, 25);
  assert.equal(o.brand, "Multimarcas");
  assert.equal(o.sellerResponsible, "Euler");
  assert.equal(o.enteredBy, "Laís");
});
test("Snippet não compartilha variável sem preenchimento", () => {
  assert.throws(() =>
    template("{{nome_comprador}}", { id: "c", name: "Loja" }, "Euler"),
  );
  assert.equal(
    template("Olá {{nome_cliente}}", { id: "c", name: "Loja" }, "Euler"),
    "Olá Loja",
  );
  assert.equal(phone("(91) 99999-9999"), "5591999999999");
});
test('Limpeza de texto preserva valores e SKU',()=>{assert.equal(cleanText('SKU:  BT-01  \n R$  1.250,35\n\n\nPedido'), 'SKU: BT-01\nR$ 1.250,35\n\nPedido');});
test('Extrator encontra dados sem alterar cadastros',()=>{assert.deepEqual(extractText('CNPJ 12.345.678/0001-95 SKU: BT-01 comprador@loja.com'),{cnpj:'12345678000195',email:'comprador@loja.com',sku:'BT-01'});});
test('QR público gera matriz offline e rejeita credenciais no link',()=>{assert.throws(()=>catalogLink('https://exemplo.com/api/catalog?token=secret'));const value=catalogLink('https://exemplo.com/catalogo');const qr=qrcode(0,'M');qr.addData(value);qr.make();assert.ok(qr.getModuleCount()>=21);assert.equal(qr.isDark(0,0),true);});
