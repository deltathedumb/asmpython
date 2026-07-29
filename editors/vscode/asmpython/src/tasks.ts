import * as path from "path";
import * as vscode from "vscode";

interface AsmpythonTaskDefinition extends vscode.TaskDefinition {
  mode: "compile" | "run" | "check" | "emit-asm";
  file?: string;
  target?: string;
}

/**
 * Lets workspaces define `"type": "asmpython"` entries in tasks.json (so
 * a project can pin a specific file/target as its build task, show up in
 * "Run Build Task", etc.) without hand-writing a shell command.
 */
export class AsmpythonTaskProvider implements vscode.TaskProvider {
  provideTasks(): vscode.Task[] {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      return [];
    }
    return [
      this.buildTask(folder, { type: "asmpython", mode: "compile" }, "Compile active file"),
      this.buildTask(folder, { type: "asmpython", mode: "run" }, "Run active file"),
      this.buildTask(folder, { type: "asmpython", mode: "check" }, "Check active file"),
    ];
  }

  resolveTask(task: vscode.Task): vscode.Task | undefined {
    const def = task.definition as AsmpythonTaskDefinition;
    if (!def.mode) {
      return undefined;
    }
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      return undefined;
    }
    return this.buildTask(folder, def, task.name || `asmpython: ${def.mode}`);
  }

  private buildTask(
    folder: vscode.WorkspaceFolder,
    def: AsmpythonTaskDefinition,
    name: string
  ): vscode.Task {
    const file = def.file ?? "${file}";
    const args = ["-m", "asmpython"];
    switch (def.mode) {
      case "check":
        args.push(file, "--check");
        break;
      case "emit-asm":
        args.push(file, "--emit-asm");
        break;
      case "compile":
      case "run":
      default: {
        const outDir = path.join("${workspaceFolder}", "build");
        args.push(file, "-o", outDir + "/${fileBasenameNoExtension}");
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
      "asmpython",
      execution,
      ["$asmpython"]
    );
    task.group =
      def.mode === "compile" || def.mode === "run" ? vscode.TaskGroup.Build : undefined;
    return task;
  }
}
