/** Keep forbidden type escapes from quietly creeping back into strict code. */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'

const violations = []
function checkSource(file, source) {
  if (/@ts-(?:ignore|expect-error|nocheck)\b/.test(source)) violations.push(`${file}: TypeScript suppression is forbidden`)
  const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  function visit(node) {
    if (node.kind === ts.SyntaxKind.AnyKeyword) violations.push(`${file}: explicit any is forbidden`)
    if ((ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) && (ts.isAsExpression(node.expression) || ts.isTypeAssertionExpression(node.expression))) violations.push(`${file}: chained type assertions are forbidden`)
    ts.forEachChild(node, visit)
  }
  visit(ast)
}
function checkDirectory(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = join(directory, entry.name)
    if (entry.isDirectory()) checkDirectory(file)
    else if (file.endsWith('.ts')) checkSource(file, readFileSync(file, 'utf8'))
    else if (file.endsWith('.vue')) {
      const source = readFileSync(file, 'utf8')
      const { descriptor, errors } = parse(source, { filename: file })
      if (errors.length) violations.push(`${file}: invalid Vue syntax`)
      for (const block of [descriptor.script, descriptor.scriptSetup]) if (block) checkSource(file, block.content)
      // Template expressions are checked by vue-tsc; prohibit suppression text.
      if (/@ts-(?:ignore|expect-error|nocheck)\b/.test(source)) violations.push(`${file}: TypeScript suppression is forbidden`)
    }
  }
}
checkDirectory('src')
if (violations.length) {
  process.stderr.write([...new Set(violations)].join('\n') + '\n')
  process.exitCode = 1
} else process.stdout.write('Strict type policy passed.\n')
