import * as path from "path";
import * as vscode from "vscode";
import { runUasm } from "./uasm";
import { isDiagnosticIgnored, scanIgnoreDirectives } from "./ignoreDirectives";
import { UasmStatusBar } from "./statusBar";

/** One entry of `uasm verify --json`'s `diagnostics` list.
 *
 * THE SHAPE IS THE COMPILER'S OWN, and this file used to describe a
 * different one -- a bare array of `{phase, line, col}` -- against a
 * `uasm --check --json` that never existed. `severity` is exact where
 * `phase` was guessed at, and `at` is null for a diagnostic with no
 * position, which a bad flag has.
 */
interface RawPlace {
  file: string;
  line: number;
  column: number;
  end_line: number;
  end_column: number;
  bytes: [number, number];
}

interface RawDiagnostic {
  code: string;
  severity: string;
  message: string;
  at: RawPlace | null;
  notes: string[];
  helps: string[];
}

interface RawReport {
  ok: boolean;
  errors: number;
  warnings: number;
  diagnostics: RawDiagnostic[];
}

/**
 * Runs `uasm verify --json` on .py files and republishes the result
 * as native VS Code diagnostics. Debounced per-document so typing doesn't
 * spawn a process per keystroke; cancels an in-flight check if the document
 * changes again before it finishes.
 */
export class UasmDiagnostics implements vscode.Disposable {
  private readonly collection: vscode.DiagnosticCollection;
  private readonly timers = new Map<string, NodeJS.Timeout>();
  private readonly inFlight = new Map<string, vscode.CancellationTokenSource>();

  constructor(
    private readonly output: vscode.OutputChannel,
    private readonly statusBar?: UasmStatusBar
  ) {
    this.collection = vscode.languages.createDiagnosticCollection("uasm");
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
      .getConfiguration("uasm", doc)
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
   * for `doc` (used on editor-focus-change, without re-running verify). */
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
      this.statusBar.set("error", `uasm: ${existing.length} diagnostic${plural}`);
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
    const result = await runUasm(
      ["verify", "--json", doc.uri.fsPath],
      cwd,
      this.output,
      cts.token
    );
    this.inFlight.delete(key);
    if (cts.token.isCancellationRequested || !result) {
      return;
    }

    // Exit code 0 with no JSON on stdout (e.g. uasm missing) -- leave
    // existing diagnostics alone rather than silently clearing real ones.
    const stdout = result.stdout.trim();
    if (!stdout) {
      if (result.code !== 0) {
        this.output.appendLine(`[uasm] verify produced no output (exit ${result.code}): ${result.stderr}`);
        if (this.isActive(doc)) {
          this.statusBar?.set("idle");
        }
      }
      return;
    }

    let raw: RawDiagnostic[];
    try {
      const report: RawReport = JSON.parse(stdout);
      raw = report.diagnostics ?? [];
    } catch {
      this.output.appendLine(`[uasm] could not parse verify --json output: ${stdout}`);
      return;
    }

    // `# uasm: ignoreall` / `# uasm: ignore` directives in the
    // source suppress matching diagnostics before they're ever published,
    // same as `# noqa` / `# type: ignore` conventions elsewhere.
    const ignores = scanIgnoreDirectives(doc);
    const filtered = ignores.ignoreAll
      ? []
      : raw.filter(
          (d) => !isDiagnosticIgnored(ignores, Math.max(0, (d.at?.line ?? 1) - 1))
        );

    const diagnostics = filtered.map((d) => this.toVscodeDiagnostic(doc, d));
    this.collection.set(doc.uri, diagnostics);

    if (this.isActive(doc)) {
      if (diagnostics.length === 0) {
        this.statusBar?.set("ok");
      } else {
        const plural = diagnostics.length === 1 ? "" : "s";
        this.statusBar?.set("error", `uasm: ${diagnostics.length} diagnostic${plural}`);
      }
    }
  }

  private toVscodeDiagnostic(doc: vscode.TextDocument, d: RawDiagnostic): vscode.Diagnostic {
    // uasm positions are 1-based; VS Code Positions are 0-based.
    const line = Math.max(0, (d.at?.line ?? 1) - 1);
    const col = Math.max(0, (d.at?.column ?? 1) - 1);
    // THE COMPILER'S OWN END, not the rest of the line. It underlines
    // exactly what it pointed at, and squiggling to the end of the line
    // instead was the old shape's workaround for not being told.
    const endLine = Math.max(0, (d.at?.end_line ?? d.at?.line ?? 1) - 1);
    const endCol = Math.max(col + 1, d.at?.end_column ?? col + 1);
    const range = new vscode.Range(line, col, endLine, endCol);

    const severity =
      d.severity === "warning"
        ? vscode.DiagnosticSeverity.Warning
        : d.severity === "note" || d.severity === "help"
        ? vscode.DiagnosticSeverity.Information
        : vscode.DiagnosticSeverity.Error;

    // THE NOTES AND HELPS BELONG IN THE HOVER. They are most of what makes
    // a uasm diagnostic useful -- "= help: pass --backend jvm" is the
    // answer, and the message alone is only the complaint.
    const extra = [...(d.notes ?? []), ...(d.helps ?? [])];
    const message = extra.length
      ? `${d.message}\n${extra.join("\n")}`
      : d.message;
    const diag = new vscode.Diagnostic(range, message, severity);
    diag.source = "uasm";
    diag.code = d.code;
    return diag;
  }
}
