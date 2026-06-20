import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { resolveCommand, runAsmpython } from "./asmpython";
import { AsmpythonDiagnostics } from "./diagnostics";

function activePythonFile(): vscode.TextDocument | undefined {
  const doc = vscode.window.activeTextEditor?.document;
  if (!doc || doc.languageId !== "python") {
    vscode.window.showWarningMessage("ASMPython: no active Python file.");
    return undefined;
  }
  return doc;
}

function outputPathFor(doc: vscode.TextDocument): { exe: string; cwd: string } {
  const folder = vscode.workspace.getWorkspaceFolder(doc.uri);
  const cwd = folder?.uri.fsPath ?? path.dirname(doc.uri.fsPath);
  const outDir = vscode.workspace
    .getConfiguration("asmpython", doc)
    .get<string>("outputDirectory", "build");
  const stem = path.basename(doc.uri.fsPath, path.extname(doc.uri.fsPath));
  const ext = process.platform === "win32" ? ".exe" : "";
  const exe = path.isAbsolute(outDir)
    ? path.join(outDir, stem + ext)
    : path.join(cwd, outDir, stem + ext);
  return { exe, cwd };
}

function compileArgs(doc: vscode.TextDocument, outPath: string): string[] {
  const cfg = vscode.workspace.getConfiguration("asmpython", doc);
  const target = cfg.get<string>("target", "").trim();
  const extra = cfg.get<string[]>("extraCompileArgs", []);
  const args = [doc.uri.fsPath, "-o", outPath];
  if (target) {
    args.push("--target", target);
  }
  args.push(...extra);
  return args;
}

export async function cmdCompile(
  output: vscode.OutputChannel,
  diagnostics: AsmpythonDiagnostics
): Promise<void> {
  const doc = activePythonFile();
  if (!doc) {
    return;
  }
  await doc.save();
  await diagnostics.checkNow(doc);

  const { exe, cwd } = outputPathFor(doc);
  fs.mkdirSync(path.dirname(exe), { recursive: true });

  output.show(true);
  output.appendLine(`[asmpython] compiling ${path.basename(doc.uri.fsPath)} -> ${exe}`);
  const result = await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: "ASMPython: compiling...",
      cancellable: true,
    },
    (_progress, token) => {
      const cts = new vscode.CancellationTokenSource();
      token.onCancellationRequested(() => cts.cancel());
      return runAsmpython(compileArgs(doc, exe), cwd, output, cts.token);
    }
  );

  if (!result) {
    return;
  }
  if (result.stdout) {
    output.append(result.stdout);
  }
  if (result.stderr) {
    output.append(result.stderr);
  }
  if (result.code === 0) {
    vscode.window.showInformationMessage(`ASMPython: compiled ${path.basename(exe)}`);
  } else {
    vscode.window.showErrorMessage(
      `ASMPython: compile failed (exit ${result.code ?? "?"}). See "ASMPython" output for details.`
    );
  }
}

export async function cmdRun(
  output: vscode.OutputChannel,
  diagnostics: AsmpythonDiagnostics
): Promise<void> {
  const doc = activePythonFile();
  if (!doc) {
    return;
  }
  await doc.save();
  await diagnostics.checkNow(doc);

  const { exe, cwd } = outputPathFor(doc);
  fs.mkdirSync(path.dirname(exe), { recursive: true });

  output.show(true);
  output.appendLine(`[asmpython] compiling ${path.basename(doc.uri.fsPath)} -> ${exe}`);
  const compileResult = await runAsmpython(compileArgs(doc, exe), cwd, output);
  if (!compileResult) {
    return;
  }
  if (compileResult.stdout) {
    output.append(compileResult.stdout);
  }
  if (compileResult.stderr) {
    output.append(compileResult.stderr);
  }
  if (compileResult.code !== 0) {
    vscode.window.showErrorMessage(
      `ASMPython: compile failed (exit ${compileResult.code ?? "?"}). See "ASMPython" output for details.`
    );
    return;
  }

  // Run the compiled binary in an integrated terminal, not captured output:
  // the program may read stdin or want a real TTY (color, prompts, etc).
  const terminal = vscode.window.createTerminal({ name: "ASMPython", cwd });
  terminal.show(true);
  const quoted = process.platform === "win32" ? `& "${exe}"` : `"${exe}"`;
  terminal.sendText(quoted);
}

export async function cmdCheck(
  output: vscode.OutputChannel,
  diagnostics: AsmpythonDiagnostics
): Promise<void> {
  const doc = activePythonFile();
  if (!doc) {
    return;
  }
  await doc.save();
  await diagnostics.checkNow(doc);
  output.show(true);
}

export async function cmdEmitAsm(output: vscode.OutputChannel): Promise<void> {
  const doc = activePythonFile();
  if (!doc) {
    return;
  }
  await doc.save();
  const { cwd } = outputPathFor(doc);
  const asmPath = doc.uri.fsPath.replace(/\.py$/i, ".asm");

  output.show(true);
  output.appendLine(`[asmpython] emitting assembly for ${path.basename(doc.uri.fsPath)}`);
  const result = await runAsmpython(
    [doc.uri.fsPath, "--emit-asm", "-o", asmPath.replace(/\.asm$/i, "")],
    cwd,
    output
  );
  if (!result) {
    return;
  }
  if (result.stdout) {
    output.append(result.stdout);
  }
  if (result.stderr) {
    output.append(result.stderr);
  }
  if (result.code === 0 && fs.existsSync(asmPath)) {
    const asmDoc = await vscode.workspace.openTextDocument(asmPath);
    await vscode.window.showTextDocument(asmDoc, { preview: false });
  } else if (result.code !== 0) {
    vscode.window.showErrorMessage(
      `ASMPython: --emit-asm failed (exit ${result.code ?? "?"}). See "ASMPython" output for details.`
    );
  }
}

export async function cmdExplainCode(output: vscode.OutputChannel): Promise<void> {
  const code = await vscode.window.showInputBox({
    title: "ASMPython: Explain Error Code",
    placeHolder: "e.g. E001, L003, P002",
    validateInput: (v) => (/^[A-Za-z]\d{3,}$/.test(v.trim()) ? null : "Expected a code like E001"),
  });
  if (!code) {
    return;
  }
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? process.cwd();
  const result = await runAsmpython(["--explain", code.trim()], folder, output);
  if (!result) {
    return;
  }
  const text = (result.stdout + result.stderr).trim();
  output.show(true);
  output.appendLine(text);
  vscode.window.showInformationMessage(text.length > 0 ? text : `No explanation found for ${code}`);
}

/** Re-resolve the asmpython command (useful after the user edits
 * asmpython.executablePath, or installs the toolchain after activation). */
export async function cmdShowOutput(output: vscode.OutputChannel): Promise<void> {
  output.show(true);
  const resolved = await resolveCommand(output);
  if (resolved) {
    output.appendLine(`[asmpython] using: ${resolved.cmd} ${resolved.baseArgs.join(" ")}`.trim());
  }
}
