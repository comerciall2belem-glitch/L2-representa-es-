import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { LocalStore } from "./store";
import type { SavedSession } from "./native";
const alphabet =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
export function encode64(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i],
      b = bytes[i + 1],
      c = bytes[i + 2];
    const n = (a << 16) | ((b || 0) << 8) | (c || 0);
    out +=
      alphabet[(n >> 18) & 63] +
      alphabet[(n >> 12) & 63] +
      (b === undefined ? "=" : alphabet[(n >> 6) & 63]) +
      (c === undefined ? "=" : alphabet[n & 63]);
  }
  return out;
}
export async function downloadCatalog(
  store: LocalStore,
  session: SavedSession,
  clientId: string,
  tableId: string,
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const url =
      session.base +
      "/api/catalog/table.pdf?client_id=" +
      encodeURIComponent(clientId) +
      "&table_id=" +
      encodeURIComponent(tableId);
    const response = await fetch(url, {
      headers: { Authorization: "Bearer " + session.token },
      signal: controller.signal,
    });
    if (!response.ok)
      throw new Error(
        "Catálogo indisponível. Confira cliente, tabela e conexão.",
      );
    if (!response.headers.get("content-type")?.includes("application/pdf"))
      throw new Error("Servidor não retornou PDF.");
    if (Number(response.headers.get("content-length") || 0) > 15 * 1024 * 1024)
      throw new Error("PDF acima de 15 MB.");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (
      bytes.length > 15 * 1024 * 1024 ||
      String.fromCharCode(...bytes.slice(0, 4)) !== "%PDF"
    )
      throw new Error("PDF inválido ou acima de 15 MB.");
    await store.saveCatalog(clientId + "|" + tableId, encode64(bytes));
  } finally {
    clearTimeout(timer);
  }
}
export async function shareCatalog(
  store: LocalStore,
  clientId: string,
  tableId: string,
) {
  const file = await store.readCatalog(clientId + "|" + tableId);
  if (!file) throw new Error("Baixe o catálogo antes de compartilhar offline.");
  if (!(await Sharing.isAvailableAsync()))
    throw new Error("Compartilhamento indisponível neste aparelho.");
  const uri = FileSystem.cacheDirectory + "l2-catalog-" + Date.now() + ".pdf";
  try {
    await FileSystem.writeAsStringAsync(uri, file.content, {
      encoding: FileSystem.EncodingType.Base64,
    });
    await Sharing.shareAsync(uri, {
      mimeType: "application/pdf",
      UTI: "com.adobe.pdf",
      dialogTitle: "Catálogo L2 Representações",
    });
  } finally {
    await FileSystem.deleteAsync(uri, { idempotent: true });
  }
}
