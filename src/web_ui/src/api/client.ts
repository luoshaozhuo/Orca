import { decodeController } from './decode'

export class ApiError extends Error {
  constructor(public readonly status: number | null, message: string) { super(message); this.name = 'ApiError' }
}

function rootUrl(): string | null {
  const configured = String(import.meta.env.VITE_CONTROLLER_API_BASE_URL ?? '').trim()
  return configured ? configured.replace(/\/+$/, '') : null
}

export function isApiConfigured(): boolean { return rootUrl() !== null }

async function request<T>(path: string, decoder: (value: unknown) => T, options: RequestInit = {}): Promise<T> {
  const root = rootUrl()
  if (!root) throw new ApiError(null, '尚未配置 VITE_CONTROLLER_API_BASE_URL')
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 4000)
  try {
    const response = await fetch(`${root}${path}`, {
      ...options, signal: controller.signal, credentials: 'same-origin',
      cache: 'no-store', headers: { Accept: 'application/json', ...options.headers }
    })
    if (!response.ok) throw new ApiError(response.status, `Controller API HTTP ${response.status}`)
    return decoder(await response.json() as unknown)
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(null, error instanceof Error ? error.message : 'Controller API 请求失败')
  } finally {
    clearTimeout(timeout)
  }
}
function query(params: Record<string, string | number | undefined>): string {
  const url = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined) url.set(key, String(value))
  const result = url.toString()
  return result ? `?${result}` : ''
}

/** Versioned contract to be implemented by Controller Web API.
 * This client does not expose device command writes or control interlock bypasses.
 */
export const controllerApi = {
  runtimeInfo: () => request('/v1/system/info',decodeController.runtimeInfo),
  station: () => request('/v1/station/snapshot',decodeController.station),
  functions: () => request('/v1/control/functions',decodeController.functions),
  capabilities: () => request('/v1/control/capabilities',decodeController.capabilities),
  devices: () => request('/v1/devices',decodeController.devices),
  pipeline: (functionId = 'active_power') =>
    request(`/v1/control/pipeline${query({ function_id: functionId })}`,decodeController.pipeline),
  events: (limit = 200) => request(`/v1/events${query({ limit })}`,decodeController.events),
  commandAudit: (id: string) =>
    request(`/v1/commands/${encodeURIComponent(id)}/audit`,decodeController.commandAudit),
  history: (signalIds: string[], from: string, to: string) =>
    request(`/v1/history${query({ signals: signalIds.join(','), from, to })}`,decodeController.history),
  configuration: () => request('/v1/configuration',decodeController.configuration),
  validateConfiguration: (config: import('./contracts').ControllerConfiguration) =>
    request('/v1/configuration/validate',decodeController.validation, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config)
    }),
  experiments: () => request('/v1/engineering/experiments',decodeController.experiments),
  performance: () => request('/v1/analysis/performance',decodeController.performance),
  assessments: () => request('/v1/analysis/assessments',decodeController.assessments),
  identifications: () => request('/v1/analysis/identifications',decodeController.identifications),
  diagnostics: () => request('/v1/analysis/diagnostics',decodeController.diagnostics)
}
