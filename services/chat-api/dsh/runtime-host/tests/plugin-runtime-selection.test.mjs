import assert from 'node:assert/strict'
import test from 'node:test'

import { selectPluginRuntimePool, SandboxUnavailableError } from '../src/plugin-runtime-selection.mjs'
import { ContainerPluginRuntimePool } from '../src/container-plugin-runtime.mjs'
import { IsolatedPluginRuntimePool } from '../src/isolated-plugin-runtime.mjs'

test('no sandbox-manager URL fails closed instead of spawning plugins locally', () => {
  assert.throws(() => selectPluginRuntimePool({ storageRoot: '/tmp', env: {} }), SandboxUnavailableError)
})

test('sandbox-manager URL selects the container pool', () => {
  const pool = selectPluginRuntimePool({
    storageRoot: '/tmp',
    env: { MOVO_SANDBOX_MANAGER_URL: 'http://sandbox-manager:8300', MOVO_SANDBOX_MANAGER_TOKEN: 'x'.repeat(64) },
  })
  assert.ok(pool instanceof ContainerPluginRuntimePool)
})

test('plugin child refuses nested plugin runtimes', async () => {
  const pool = selectPluginRuntimePool({ storageRoot: '/tmp', env: { MOVO_DSH_PLUGIN_CHILD: '1' } })
  assert.deepEqual(pool.inventory(), [])
  await assert.rejects(() => pool.create({}), SandboxUnavailableError)
})

test('in-process plugins require the explicit dev flag', () => {
  const pool = selectPluginRuntimePool({ storageRoot: '/tmp', env: { MOVO_DEV_INPROCESS_PLUGINS: '1' } })
  assert.ok(pool instanceof IsolatedPluginRuntimePool)
})
