import * as vscode from "vscode";

export type CheckStatus = "idle" | "checking" | "ok" | "error";

/** A single status bar item reflecting the most recent --check result for
 * the active editor. Click to show the output channel. */
export class UasmStatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = "uasm.showOutput";
    this.set("idle");
  }

  dispose(): void {
    this.item.dispose();
  }

  set(status: CheckStatus, detail?: string): void {
    switch (status) {
      case "idle":
        this.item.text = "$(circle-outline) uasm";
        this.item.tooltip = "uasm: no check run yet";
        this.item.backgroundColor = undefined;
        break;
      case "checking":
        this.item.text = "$(sync~spin) uasm";
        this.item.tooltip = "uasm: checking...";
        this.item.backgroundColor = undefined;
        break;
      case "ok":
        this.item.text = "$(check) uasm";
        this.item.tooltip = "uasm: no diagnostics";
        this.item.backgroundColor = undefined;
        break;
      case "error":
        this.item.text = "$(error) uasm";
        this.item.tooltip = detail ?? "uasm: diagnostics found";
        this.item.backgroundColor = new vscode.ThemeColor("statusBarItem.errorBackground");
        break;
    }
    this.item.show();
  }

  hide(): void {
    this.item.hide();
  }
}
