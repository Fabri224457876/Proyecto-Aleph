// Descargas locales (informes en Markdown, JSON de la capa ATT&CK). Las exportaciones de la API usan downloadFile.
export function saveText(name: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function saveJson(name: string, data: unknown): void {
  saveText(name, JSON.stringify(data, null, 2), "application/json");
}
