export function cleanText(text: string): string {
  return text
    .split(/\r?\n/)
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n");
}
export function extractText(text: string) {
  const cnpj =
    text
      .match(/\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b/)?.[0]
      .replace(/\D/g, "") || "";
  const email = text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i)?.[0] || "";
  const sku =
    text.match(/(?:SKU|C[ÓO]DIGO)\s*[:#-]?\s*([A-Z0-9._-]+)/i)?.[1] || "";
  return { cnpj, email, sku };
}
export function catalogLink(value: string): string {
  const url = new URL(value);
  if (url.protocol !== "https:")
    throw new Error("Use um link público HTTPS de catálogo.");
  if (
    url.pathname.startsWith("/api/") ||
    [...url.searchParams.keys()].some((k) =>
      /token|senha|secret|password|authorization/i.test(k),
    )
  )
    throw new Error("Não transforme links privados ou credenciais em QR Code.");
  return url.toString();
}
