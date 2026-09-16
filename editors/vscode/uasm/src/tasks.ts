import * as path from "path";
import * as vscode from "vscode";

interface UasmTaskDefinition extends vscode.TaskDefinition {
  mode: "compile" | "run" | "verify" | "emit-asm";
  file?: string;
  target?: string;
}

/**
 * Lets workspaces define `"type": "uasm"` entries in tasks.json (so
 * a project can pin a specific file/target as its build task, show up in
 * "Run Build Task", etc.) without hand-writing a shell command.
 */
export class UasmTaskProvider implements vscode.TaskProvider {
  provideTasks(): vscode.Task[] {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      return [];
    }
    return [
      this.buildTask(folder, { type: "uasm", mode: "compile" }, "Compile active file"),
      this.buildTask(folder, { type: "uasm", mode: "run" }, "Run active file"),
      this.buildTask(folder, { type: "uasm", mode: "verify" }, "Verify active file"),
    ];
  }

  resolveTask(task: vscode.Task): vscode.Task | undefined {
    const def = task.definition as UasmTaskDefinition;
    if (!def.mode) {
      return undefined;
    }
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      return undefined;
    }
    return this.buildTask(folder, def, task.name || `uasm: ${def.mode}`);
  }

  private buildTask(
    folder: vscode.WorkspaceFolder,
    def: UasmTaskDefinition,
    name: string
  ): vscode.Task {
    const file = def.file ?? "${file}";
    // EVERY INVOCATION NAMES A VERB. These pushed the file straight after
    // `-m uasm`, which argparse refuses -- it wants a subcommand first --
    // so every task here failed before it reached the compiler. `--check`
    // was never a flag either; the verb is `verify`.
    const args = ["-m", "uasm"];
    switch (def.mode) {
      case "verify":
        args.push("verify", file);
        break;
      case "emit-asm":
        args.push("build", file, "--emit-asm");
        break;
      case "run":
        args.push("run", file);
        break;
      case "compile":
      default: {
        const outDir = path.join("${workspaceFolder}", "build");
        args.push("build", file, "-o",
                  outDir + "/${fileBasenameNoExtension}");
        break;
      }
    }
    if (def.target) {
      args.push("--target", def.target);
    }

    const execution = new vscode.ShellExecution("py", args, { cwd: folder.uri.fsPath });
    const task = new vscode.Task(
      def,
      folder,
      name,
      "uasm",
      execution,
      ["$uasm"]
    );
    task.group =
      def.mode === "compile" || def.mode === "run" ? vscode.TaskGroup.Build : undefined;
    return task;
  }
}
