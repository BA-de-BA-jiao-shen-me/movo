import { IsolatedPluginRuntimePool } from './isolated-plugin-runtime.mjs'
import { ContainerPluginRuntimePool } from './container-plugin-runtime.mjs'

export class SandboxUnavailableError extends Error {
  constructor(reason = 'sandbox-manager is not configured') {
    super(`Plugin sandbox is unavailable: ${reason}`)
    this.code = 'sandbox_unavailable'
  }
}

// Inside a sandboxed plugin child the host never creates nested plugin
// runtimes (see runtime-http-server: MOVO_DSH_PLUGIN_CHILD routes to the local
// manager), so this pool only has to refuse.
class RefusingPluginRuntimePool {
  async create() { throw new SandboxUnavailableError('nested plugin runtimes are not allowed') }
  inventory() { return [] }
  findByIsolation() { return undefined }
  get() { return undefined }
  async dispose() {}
  async disposeAll() {}
}

/**
 * P0 fail-closed selection of where DSH plugins run.
 * - MOVO_SANDBOX_MANAGER_URL set      -> Kata containers via sandbox-manager
 * - MOVO_DSH_PLUGIN_CHILD=1           -> already inside a sandbox container
 * - MOVO_DEV_INPROCESS_PLUGINS=1      -> developer-only local child processes
 * - otherwise                         -> refuse to start
 */
export function selectPluginRuntimePool({ storageRoot, env = process.env }) {
  if (env.MOVO_SANDBOX_MANAGER_URL) {
    return new ContainerPluginRuntimePool({
      managerUrl: env.MOVO_SANDBOX_MANAGER_URL,
      managerToken: env.MOVO_SANDBOX_MANAGER_TOKEN,
    })
  }
  if (env.MOVO_DSH_PLUGIN_CHILD === '1') return new RefusingPluginRuntimePool()
  if (env.MOVO_DEV_INPROCESS_PLUGINS === '1') return new IsolatedPluginRuntimePool(storageRoot)
  throw new SandboxUnavailableError(
    'MOVO_SANDBOX_MANAGER_URL is required (set MOVO_DEV_INPROCESS_PLUGINS=1 only for local development)')
}
