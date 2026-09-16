import * as vscode from "vscode";
import {
  cmdCheck,
  cmdCompile,
  cmdEmitAsm,
  cmdExplainCode,
  cmdRun,
  cmdShowOutput,
} from "./commands";
import { UasmDiagnostics } from "./diagnostics";
import { resetResolvedCommand } from "./uasm";
import { UasmStatusBar } from "./statusBar";
import { UasmTaskProvider } from "./tasks";

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("uasm");
  const statusBar = new UasmStatusBar();
  const diagnostics = new UasmDiagnostics(output, statusBar);

  context.subscriptions.push(output, statusBar, diagnostics);

  context.subscriptions.push(
    vscode.commands.registerCommand("uasm.compile", () => cmdCompile(output, diagnostics)),
    vscode.commands.registerCommand("uasm.run", () => cmdRun(output, diagnostics)),
    vscode.commands.registerCommand("uasm.check", () => cmdCheck(output, diagnostics)),
    vscode.commands.registerCommand("uasm.emitAsm", () => cmdEmitAsm(output)),
    vscode.commands.registerCommand("uasm.explainCode", () => cmdExplainCode(output)),
    vscode.commands.registerCommand("uasm.showOutput", () => cmdShowOutput(output))
  );

  context.subscriptions.push(
    vscode.tasks.registerTaskProvider("uasm", new UasmTaskProvider())
  );

  // Document lifecycle -> diagnostics.
  context.subscriptions.push(
    vscode.workspace.onDidOpenTextDocument((doc) => {
      if (doc.languageId === "python") {
        void diagnostics.checkNow(doc);
      }
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (doc.languageId === "python") {
        void diagnostics.checkNow(doc);
      }
    }),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.document.languageId !== "python") {
        return;
      }
      const checkOnType = vscode.workspace
        .getConfiguration("uasm", e.document)
        .get<boolean>("checkOnType", true);
      if (checkOnType) {
        diagnostics.scheduleCheck(e.document);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      if (doc.languageId === "python") {
        diagnostics.clear(doc);
      }
    }),
    vscode.window.onDidChangeActiveTextEditor((editor) => {
      diagnostics.syncStatusBarFor(editor?.document);
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("uasm.executablePath")) {
        resetResolvedCommand();
      }
    })
  );

  // Check whatever's already open at activation time.
  for (const doc of vscode.workspace.textDocuments) {
    if (doc.languageId === "python") {
      void diagnostics.checkNow(doc);
    }
  }
  diagnostics.syncStatusBarFor(vscode.window.activeTextEditor?.document);
}

export function deactivate(): void {
  // All resources are owned by context.subscriptions; nothing to do here.
}
