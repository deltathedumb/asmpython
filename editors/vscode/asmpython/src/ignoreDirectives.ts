import * as vscode from "vscode";

// `# asmpython: ignoreall` anywhere in the file suppresses every diagnostic
// for that document. `# asmpython: ignore` at the end of (or alone on) a
// line suppresses diagnostics reported on that specific line. Matches
// loosely on whitespace/case around the keyword, same spirit as `# noqa`/
// `# type: ignore`.
const IGNORE_ALL_RE = /#\s*asmpython:\s*ignoreall\b/i;
const IGNORE_LINE_RE = /#\s*asmpython:\s*ignore\b(?!all)/i;

export interface IgnoreDirectives {
  ignoreAll: boolean;
  ignoredLines: Set<number>; // 0-based line numbers
}

export function scanIgnoreDirectives(doc: vscode.TextDocument): IgnoreDirectives {
  const ignoredLines = new Set<number>();
  let ignoreAll = false;

  for (let i = 0; i < doc.lineCount; i++) {
    const text = doc.lineAt(i).text;
    if (!ignoreAll && IGNORE_ALL_RE.test(text)) {
      ignoreAll = true;
    }
    if (IGNORE_LINE_RE.test(text)) {
      ignoredLines.add(i);
    }
  }

  return { ignoreAll, ignoredLines };
}

export function isDiagnosticIgnored(
  directives: IgnoreDirectives,
  diagnosticLine0Based: number
): boolean {
  return directives.ignoreAll || directives.ignoredLines.has(diagnosticLine0Based);
}
