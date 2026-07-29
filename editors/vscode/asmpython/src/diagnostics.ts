import * as path from "path";
import * as vscode from "vscode";
import { runAsmpython } from "./asmpython";
import { isDiagnosticIgnored, scanIgnoreDirectives } from "./ignoreDirectives";
import { AsmpythonStatusBar } from "./statusBar";

interface RawDiagnostic {
  phase: string;
  message: string;
  line: number;
  col: number;
  code: string | null;
}

/**
 * Runs `asmpython --check --json` on .py files and republishes the result
 * as native VS Code diagnostics. Debounced per-document so typing doesn't
 * spawn a process per keystroke; cancels an in-flight check if the document
 * changes again before it finishes.
 */
export class AsmpythonDiagnostics implements vscode.Disposable {
  private readonly collection: vscode.DiagnosticCollection;
  private readonly timers = new Map<string, NodeJS.Timeout>();
  private readonly inFlight = new Map<string, vscode.CancellationTokenSource>();

  constructor(
    private readonly output: vscode.OutputChannel,
    private readonly statusBar?: AsmpythonStatusBar
  ) {
    this.collection = vscode.languages.createDiagnosticCollection("asmpython");
  }

  dispose(): void {
    this.collection.dispose();
    for (const t of this.timers.values()) {
      clearTimeout(t);
    }
    for (const cts of this.inFlight.values()) {
      cts.cancel();
      cts.dispose();
    }
  }

  /** Check immediately (used on save / explicit command), no debounce. */
  async checkNow(doc: vscode.TextDocument): Promise<void> {
    if (doc.languageId !== "python" || doc.isUntitled) {
      return;
    }
    const key = doc.uri.toString();
    const existingTimer = this.timers.get(key);
    if (existingTimer) {
      clearTimeout(existingTimer);
      this.timers.delete(key);
    }
    await this.runCheck(doc);
  }

  /** Schedule a debounced check (used on document change). */
  scheduleCheck(doc: vscode.TextDocument): void {
    if (doc.languageId !== "python" || doc.isUntitled) {
      return;
    }
    const debounceMs = vscode.workspace
      .getConfiguration("asmpython", doc)
      .get<number>("checkDebounceMs", 400);
    const key = doc.uri.toString();
    const existing = this.timers.get(key);
    if (existing) {
      clearTimeout(existing);
    }
    this.timers.set(
      key,
      setTimeout(() => {
        this.timers.delete(key);
        void this.runCheck(doc);
      }, debounceMs)
    );
  }

  clear(doc: vscode.TextDocument): void {
    this.collection.delete(doc.uri);
    const key = doc.uri.toString();
    const timer = this.timers.get(key);
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(key);
    }
    const cts = this.inFlight.get(key);
    if (cts) {
      cts.cancel();
      cts.dispose();
      this.inFlight.delete(key);
    }
    if (this.isActive(doc)) {
      this.statusBar?.set("idle");
    }
  }

  /** Re-sync the status bar to whatever diagnostics are already published
   * for `doc` (used on editor-focus-change, without re-running --check). */
  syncStatusBarFor(doc: vscode.TextDocument | undefined): void {
    if (!this.statusBar) {
      return;
    }
    if (!doc || doc.languageId !== "python") {
      this.statusBar.hide();
      return;
    }
    this.statusBar.set("idle");
    const existing = this.collection.get(doc.uri);
    if (existing && existing.length > 0) {
      const plural = existing.length === 1 ? "" : "s";
      this.statusBar.set("error", `ASMPython: ${existing.length} diagnostic${plural}`);
    } else if (existing) {
      this.statusBar.set("ok");
    }
  }

  private isActive(doc: vscode.TextDocument): boolean {
    return vscode.window.activeTextEditor?.document.uri.toString() === doc.uri.toString();
  }

  private async runCheck(doc: vscode.TextDocument): Promise<void> {
    const key = doc.uri.toString();
    const prevCts = this.inFlight.get(key);
    prevCts?.cancel();
    prevCts?.dispose();
    const cts = new vscode.CancellationTokenSource();
    this.inFlight.set(key, cts);

    if (this.isActive(doc)) {
      this.statusBar?.set("checking");
    }

    const cwd = vscode.workspace.getWorkspaceFolder(doc.uri)?.uri.fsPath ?? path.dirname(doc.uri.fsPath);
    const result = await runAsmpython(
      ["--check", "--json", doc.uri.fsPath],
      cwd,
      this.output,
      cts.token
    );
    this.inFlight.delete(key);
    if (cts.token.isCancellationRequested || !result) {
      return;
    }

    // Exit code 0 with no JSON on stdout (e.g. asmpython missing) -- leave
    // existing diagnostics alone rather than silently clearing real ones.
    const stdout = result.stdout.trim();
    if (!stdout) {
      if (result.code !== 0) {
        this.output.appendLine(`[asmpython] --check produced no output (exit ${result.code}): ${result.stderr}`);
        if (this.isActive(doc)) {
          this.statusBar?.set("idle");
        }
      }
      return;
    }

    let raw: RawDiagnostic[];
    try {
      raw = JSON.parse(stdout);
    } catch {
      this.output.appendLine(`[asmpython] could not parse --check --json output: ${stdout}`);
      return;
    }

    // `# asmpython: ignoreall` / `# asmpython: ignore` directives in the
    // source suppress matching diagnostics before they're ever published,
    // same as `# noqa` / `# type: ignore` conventions elsewhere.
    const ignores = scanIgnoreDirectives(doc);
    const filtered = ignores.ignoreAll
      ? []
      : raw.filter((d) => !isDiagnosticIgnored(ignores, Math.max(0, (d.line ?? 1) - 1)));

    const diagnostics = filtered.map((d) => this.toVscodeDiagnostic(doc, d));
    this.collection.set(doc.uri, diagnostics);

    if (this.isActive(doc)) {
      if (diagnostics.length === 0) {
        this.statusBar?.set("ok");
      } else {
        const plural = diagnostics.length === 1 ? "" : "s";
        this.statusBar?.set("error", `ASMPython: ${diagnostics.length} diagnostic${plural}`);
      }
    }
  }

  private toVscodeDiagnostic(doc: vscode.TextDocument, d: RawDiagnostic): vscode.Diagnostic {
    // asmpython positions are 1-based; VS Code Positions are 0-based.
    const line = Math.max(0, (d.line ?? 1) - 1);
    const col = Math.max(0, (d.col ?? 1) - 1);
    const lineText = line < doc.lineCount ? doc.lineAt(line).text : "";
    const endCol = Math.max(col + 1, lineText.length);
    const range = new vscode.Range(line, col, line, endCol);

    const severity =
      d.phase === "lexical" || d.phase === "syntax" || d.phase === "semantic"
        ? vscode.DiagnosticSeverity.Error
        : vscode.DiagnosticSeverity.Warning;

    const message = d.code ? `${d.message} [${d.code}]` : d.message;
    const diag = new vscode.Diagnostic(range, message, severity);
    diag.source = `asmpython (${d.phase})`;
    if (d.code) {
      diag.code = d.code;
    }
    return diag;
  }
}
