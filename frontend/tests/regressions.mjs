import assert from 'node:assert/strict'
import { test } from 'node:test'
import { build } from 'vite'

// Known failures run on every invocation. Unexpected exceptions/passes fail the
// suite. Set RUN_PENDING_REGRESSIONS=1 to see their ordinary failing assertions.
export function pendingRegression(name, finding, body) {
  test(name, async (t) => {
    if (process.env.RUN_PENDING_REGRESSIONS === '1') return body(t)
    try {
      await body(t)
    } catch (error) {
      if (error.code !== 'ERR_ASSERTION') throw error
      t.todo(`${finding}: awaiting its assigned implementation phase`)
      t.diagnostic(error.message)
      return
    }
    assert.fail(`${finding} now passes: remove the pendingRegression marker`)
  })
}

export async function bundleEntry(entry) {
  const bundle = await build({
    configFile: false,
    logLevel: 'silent',
    esbuild: { jsx: 'automatic' },
    define: { 'import.meta.env.VITE_API_BASE_URL': '""' },
    build: {
      write: false, minify: false, lib: { entry, formats: ['es'] },
      rollupOptions: {
        external: ['react', 'react/jsx-runtime'],
        output: { paths: (id) => import.meta.resolve(id) },
      },
    },
  })
  const output = (Array.isArray(bundle) ? bundle[0] : bundle).output.find((item) => item.type === 'chunk')
  return `data:text/javascript;base64,${Buffer.from(output.code).toString('base64')}`
}
