import * as cp from "child_process";
import * as vscode from "vscode";

export interface RunResult {
  code: number | null;
  stdout: string;
  stderr: string;
}

export interface ResolvedCommand {
  cmd: string;
  baseArgs: string[];
}

let cachedCommand: ResolvedCommand | undefined;

/** Clear the cached executable resolution (call when the config changes). */
export function resetResolvedCommand(): void {
  cachedCommand = undefined;
}

/**
 * Find a way to invoke uasm: an explicit configured path, a bare
 * `uasm` on PATH, or a `<python> -m uasm` fallback (covers a
 * source checkout with no console-script entry point installed).
 */
export async function resolveCommand(
  output: vscode.OutputChannel
): Promise<ResolvedCommand | undefined> {
  if (cachedCommand) {
    return cachedCommand;
  }

  const configured = vscode
    .workspace.getConfiguration("uasm")
    .get<string>("executablePath", "")
    .trim();

  const candidates: ResolvedCommand[] = [];
  if (configured) {
    candidates.push({ cmd: configured, baseArgs: [] });
  }
  candidates.push({ cmd: "uasm", baseArgs: [] });
  for (const py of ["py", "python3", "python"]) {
    candidates.push({ cmd: py, baseArgs: ["-m", "uasm"] });
  }

  for (const candidate of candidates) {
    if (await probe(candidate)) {
      cachedCommand = candidate;
      return candidate;
    }
  }

  output.appendLine(
    "[uasm] Could not find an uasm executable. Set " +
      "'uasm.executablePath' in settings, or make sure 'uasm' " +
      "(or a Python with the uasm package installed) is on PATH."
  );
  return undefined;
}

function probe(candidate: ResolvedCommand): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = cp.spawn(candidate.cmd, [...candidate.baseArgs, "--version"], {
      shell: process.platform === "win32",
    });
    let settled = false;
    const finish = (ok: boolean) => {
      if (!settled) {
        settled = true;
        resolve(ok);
      }
    };
    proc.on("error", () => finish(false));
    proc.on("exit", (code) => finish(code === 0));
    // Don't let a hung process block extension activation forever.
    setTimeout(() => finish(false), 5000);
  });
}

/** Run uasm with the given args, capturing stdout/stderr. */
export async function runUasm(
  args: string[],
  cwd: string,
  output: vscode.OutputChannel,
  token?: vscode.CancellationToken
): Promise<RunResult | undefined> {
  const resolved = await resolveCommand(output);
  if (!resolved) {
    return undefined;
  }
  const fullArgs = [...resolved.baseArgs, ...args];
  output.appendLine(`$ ${resolved.cmd} ${fullArgs.join(" ")}`);

  return new Promise((resolve) => {
    const proc = cp.spawn(resolved.cmd, fullArgs, {
      cwd,
      shell: process.platform === "win32",
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));

    const onCancel = token?.onCancellationRequested(() => {
      proc.kill();
    });

    proc.on("error", (err) => {
      onCancel?.dispose();
      output.appendLine(`[uasm] failed to launch: ${err.message}`);
      resolve({ code: null, stdout, stderr: stderr + String(err.message) });
    });
    proc.on("exit", (code) => {
      onCancel?.dispose();
      resolve({ code, stdout, stderr });
    });
  });
}
