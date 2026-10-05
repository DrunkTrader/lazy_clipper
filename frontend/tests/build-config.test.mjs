import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { test } from 'node:test'

const root = resolve(import.meta.dirname, '..')

test('TypeScript is the only tracked Vite config authority', () => {
  assert.ok(existsSync(resolve(root, 'vite.config.ts')))
  assert.equal(existsSync(resolve(root, 'vite.config.js')), false)
  assert.equal(existsSync(resolve(root, 'vite.config.d.ts')), false)
  assert.match(readFileSync(resolve(root, 'package.json'), 'utf8'), /tsc --noEmit .*tsconfig\.node\.json.*vite build/)
})
