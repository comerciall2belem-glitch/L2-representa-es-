import type { Entity, Snapshot } from "./types";

export function workDate(at = new Date()): string {
  return new Date(at.getTime() - 3 * 60 * 60 * 1000).toISOString().slice(0, 10);
}
export function cents(value: unknown): number {
  const text = String(value ?? "").trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text))
    throw new Error("Preço inválido na tabela.");
  const [units, decimals = ""] = text.split(".");
  const n = Number(units) * 100 + Number(decimals.padEnd(2, "0"));
  if (!Number.isSafeInteger(n)) throw new Error("Preço excede o limite.");
  return n;
}
export const currency = (n: number) =>
  (n / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
export function orderTotal(items: Entity[]): number {
  return items.reduce((sum, item) => {
    const qty = Number(item.quantity);
    if (!Number.isInteger(qty) || qty < 1 || qty > 100000)
      throw new Error("Quantidade deve ser inteira e positiva.");
    const subtotal = cents(item.unitPrice) * qty;
    if (!Number.isSafeInteger(sum + subtotal))
      throw new Error("Total excede o limite.");
    return sum + subtotal;
  }, 0);
}
export function makeOrder(
  id: string,
  client: Entity,
  seller: string,
  items: Entity[],
  user: string,
  notes: string,
  at = new Date(),
): Entity {
  if (!items.length) throw new Error("Adicione produtos ao carrinho.");
  if (!["PA", "AP"].includes(String(client.state).toUpperCase()))
    throw new Error("Cliente precisa ter UF PA ou AP.");
  if (!seller.trim()) throw new Error("Informe o vendedor responsável.");
  const states = new Set(items.map((i) => i.state));
  if (states.size !== 1)
    throw new Error("O ERP atual exige uma UF de tabela comum no pedido.");
  const priceTable = String(items[0].state);
  if (client.state === "PA" && priceTable !== "PA")
    throw new Error("Cliente do Pará utiliza tabela PA.");
  const brands = [...new Set(items.map((i) => i.brand))];
  return {
    id,
    clientId: client.id,
    sellerResponsible: seller,
    enteredBy: user,
    user,
    date: workDate(at),
    brand: brands.length === 1 ? brands[0] : "Multimarcas",
    status: "Pedido confirmado",
    priceTable,
    items: items.map((i) => ({
      ...i,
      subtotal: ((cents(i.unitPrice) * i.quantity) / 100).toFixed(2),
    })),
    amount: orderTotal(items) / 100,
    notes,
    source: "maleta_mobile",
    createdAt: at.toISOString(),
  };
}
export function template(
  text: string,
  client: Entity,
  user: string,
  brand = "",
  snapshot?: Snapshot,
): string {
  const orders = (snapshot?.orders ?? [])
    .filter((o) => o.clientId === client.id)
    .sort((a, b) => String(b.date).localeCompare(String(a.date)));
  const values: Record<string, string> = {
    nome_cliente: String(client.tradeName || client.name || ""),
    nome_comprador: String(client.contact || ""),
    vendedor: user,
    marca: brand,
    ultima_compra: orders[0]?.date || "sem compra registrada",
    link_catalogo: String(client.catalogUrl || ""),
  };
  const out = text.replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => {
    if (!values[key]) throw new Error(`Preencha ${key} antes de compartilhar.`);
    return values[key];
  });
  return out;
}
export function phone(raw: unknown): string {
  let p = String(raw || "").replace(/\D/g, "");
  if (p.length === 10 || p.length === 11) p = "55" + p;
  if (!/^55\d{10,11}$/.test(p))
    throw new Error("Cadastre telefone brasileiro com DDD.");
  return p;
}
export function dailyMetrics(s: Snapshot, user: string, date = workDate()) {
  const orders = s.orders.filter(
    (o) => (o.sellerResponsible || o.user) === user && o.date === date,
  );
  const confirmed = orders.filter(
    (o) => !["Rascunho", "Cancelado", "Proposta enviada"].includes(o.status),
  );
  return {
    visits: s.visits.filter(
      (v) => v.user === user && String(v.date).startsWith(date) && v.checkOut,
    ).length,
    confirmed: confirmed.reduce(
      (sum, o) => sum + Math.round(Number(o.amount || 0) * 100),
      0,
    ),
    clients: new Set(confirmed.map((o) => o.clientId)).size,
    tasks: s.tasks.filter(
      (t) =>
        t.user === user && t.status !== "Concluída" && String(t.date) <= date,
    ).length,
  };
}
