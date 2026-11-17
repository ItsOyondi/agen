const vscode = require("vscode");

const shown = new Map();

function importsIn(text) {
  const found = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/#.*$/, "").trim();
    const selected = line.match(/^@at\s+(\S+)\s+@import\s+(.+)$/);
    if (selected) {
      found.push(`@at ${selected[1]} @import ${selected[2].trim()}`);
      continue;
    }
    const whole = line.match(/^@import\s+(\S+)\s*$/);
    if (whole) found.push(`@import ${whole[1]}`);
  }
  return found;
}

function markdown(text) {
  const found = importsIn(text);
  const body = found.length ? found.map((line) => `- \`${line}\``).join("\n") : "This file has no imports yet.";
  return new vscode.MarkdownString(`**Imports in this file**\n\n${body}`);
}

function showImports(document) {
  const found = importsIn(document.getText());
  const file = document.fileName.split(/[\\/]/).pop();
  vscode.window.showQuickPick(found.length ? found : ["This file has no imports yet"], {
    title: `Imports in ${file}`,
    placeHolder: found.length ? `${found.length} import${found.length === 1 ? "" : "s"}` : "No imports yet",
  });
}

function ensureAppearance() {
  const defaults = require("./package.json").configurationDefaults || {};
  const rules = (defaults["editor.tokenColorCustomizations"] || {}).textMateRules || [];
  const config = vscode.workspace.getConfiguration();
  const updates = [];
  if (config.get("workbench.iconTheme") !== "agen-seti") {
    updates.push(config.update("workbench.iconTheme", "agen-seti", vscode.ConfigurationTarget.Global));
  }
  const colors = Object.assign({}, config.get("editor.tokenColorCustomizations") || {});
  const existing = Array.isArray(colors.textMateRules) ? colors.textMateRules.slice() : [];
  const seen = new Set(existing.map((rule) => String(rule.scope)));
  let changed = false;
  for (const rule of rules) {
    if (!seen.has(String(rule.scope))) {
      existing.push(rule);
      seen.add(String(rule.scope));
      changed = true;
    }
  }
  if (changed) {
    colors.textMateRules = existing;
    updates.push(config.update("editor.tokenColorCustomizations", colors, vscode.ConfigurationTarget.Global));
  }
  return Promise.all(updates);
}

function activate(context) {
  ensureAppearance().catch(() => {});
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (event.document.languageId !== "agen") return;
      for (const change of event.contentChanges) {
        const lineNo = change.range.start.line;
        const line = event.document.lineAt(lineNo).text.replace(/#.*$/, "").trim();
        if (line !== "@imported") continue;
        const key = `${event.document.uri.toString()}:${lineNo}`;
        if (shown.get(key) === line) continue;
        shown.set(key, line);
        showImports(event.document);
      }
    }),
    vscode.languages.registerHoverProvider("agen", {
      provideHover(document, position) {
        const line = document.lineAt(position).text;
        if (!/@imported\b/.test(line)) return undefined;
        return new vscode.Hover(markdown(document.getText()));
      },
    }),
    vscode.languages.registerCompletionItemProvider("agen", {
      provideCompletionItems(document, position) {
        const text = document.lineAt(position).text.slice(0, position.character);
        const at = text.lastIndexOf("@");
        if (at < 0 || text.slice(at).includes(" ")) return undefined;
        const range = new vscode.Range(position.line, at, position.line, position.character);
        const items = ["@import", "@at", "@imported"].map((label) => {
          const item = new vscode.CompletionItem(label, vscode.CompletionItemKind.Keyword);
          item.range = range;
          if (label === "@imported") {
            item.detail = "Show the imports in this file";
            item.documentation = markdown(document.getText());
          }
          return item;
        });
        return items;
      },
    }, "@")
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
