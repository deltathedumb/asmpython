import * as vscode from "vscode";
import {
  cmdCheck,
  cmdCompile,
  cmdEmitAsm,
  cmdExplainCode,
  cmdRun,
  cmdShowOutput,
} from "./commands";
import { AsmpythonDiagnostics } from "./diagnostics";
import { resetResolvedCommand } from "./asmpython";
import { AsmpythonStatusBar } from "./statusBar";
import { AsmpythonTaskProvider } from "./tasks";

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("ASMPython");
  const statusBar = new AsmpythonStatusBar();
  const diagnostics = new AsmpythonDiagnostics(output, statusBar);

  context.subscriptions.push(output, statusBar, diagnostics);

  context.subscriptions.push(
    vscode.commands.registerCommand("asmpython.compile", () => cmdCompile(output, diagnostics)),
    vscode.commands.registerCommand("asmpython.run", () => cmdRun(output, diagnostics)),
    vscode.commands.registerCommand("asmpython.check", () => cmdCheck(output, diagnostics)),
    vscode.commands.registerCommand("asmpython.emitAsm", () => cmdEmitAsm(output)),
    vscode.commands.registerCommand("asmpython.explainCode", () => cmdExplainCode(output)),
    vscode.commands.registerCommand("asmpython.showOutput", () => cmdShowOutput(output))
  );

  context.subscriptions.push(
    vscode.tasks.registerTaskProvider("asmpython", new AsmpythonTaskProvider())
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
        .getConfiguration("asmpython", e.document)
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
      if (e.affectsConfiguration("asmpython.executablePath")) {
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
