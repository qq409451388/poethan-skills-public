import type {
  ApplicationSettings,
  DiagnosticReport,
  PluginPackage,
  PluginScanResponse,
  RunRequest,
  RunState,
  ServerProfile,
} from './types'

let bootstrapped = false

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string }
    return payload.detail || `请求失败（${response.status}）`
  } catch {
    return `请求失败（${response.status}）`
  }
}

export async function bootstrap(): Promise<void> {
  if (bootstrapped) return
  const response = await fetch('/api/v1/bootstrap', { credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
  bootstrapped = true
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  await bootstrap()
  const method = (init.method || 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD'].includes(method)) headers.set('X-Poethan-Request', '1')
  const response = await fetch(path, { ...init, headers, credentials: 'include' })
  if (!response.ok) throw new Error(await errorMessage(response))
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  settings: () => request<ApplicationSettings>('/api/v1/settings'),
  saveSettings: (value: ApplicationSettings) => request<ApplicationSettings>('/api/v1/settings', { method: 'PUT', body: JSON.stringify(value) }),
  testAI: (endpoint: string, model: string, apiKey: string) => request<{ ok: boolean; message: string; rawResponse?: string }>('/api/v1/ai/test', { method: 'POST', body: JSON.stringify({ endpoint, model, apiKey }) }),
  servers: () => request<ServerProfile[]>('/api/v1/servers'),
  createServer: (value: ServerProfile) => request<ServerProfile>('/api/v1/servers', { method: 'POST', body: JSON.stringify(value) }),
  updateServer: (value: ServerProfile) => request<ServerProfile>(`/api/v1/servers/${value.id}`, { method: 'PUT', body: JSON.stringify(value) }),
  deleteServer: (id: string) => request<void>(`/api/v1/servers/${id}`, { method: 'DELETE' }),
  testServer: (server: ServerProfile, acceptHostKey = false) => request<{ ok: boolean; message: string; latencyMs?: number; hostKeyRequired?: boolean; fingerprint?: string }>('/api/v1/servers/test', { method: 'POST', body: JSON.stringify({ server, acceptHostKey: acceptHostKey === true }) }),
  plugins: () => request<PluginScanResponse>('/api/v1/plugins'),
  rescanPlugins: () => request<PluginScanResponse>('/api/v1/plugins/rescan', { method: 'POST' }),
  openPluginDirectory: () => request<{ ok: boolean; directory: string }>('/api/v1/plugins/open-directory', { method: 'POST' }),
  importPlugin: async (files: FileList): Promise<PluginPackage> => {
    const form = new FormData()
    const paths: string[] = []
    Array.from(files).forEach((file) => {
      form.append('files', file)
      paths.push(file.webkitRelativePath || file.name)
    })
    form.append('paths', JSON.stringify(paths))
    return request<PluginPackage>('/api/v1/plugins/import', { method: 'POST', body: form })
  },
  createLocalScriptTool: (value: { name: string; description: string; runtime: 'bash' | 'python'; script: File }) => {
    const form = new FormData()
    form.append('name', value.name)
    form.append('description', value.description)
    form.append('runtime', value.runtime)
    form.append('script', value.script)
    return request<PluginPackage>('/api/v1/tools/local-script', { method: 'POST', body: form })
  },
  createServerScriptTool: (value: { name: string; description: string; runtime: 'bash' | 'python'; scriptPath: string }) => request<PluginPackage>('/api/v1/tools/server-script', { method: 'POST', body: JSON.stringify(value) }),
  runConfig: (serverId: string, pluginId: string) => request<{ mode: string; values: Record<string, string> }>(`/api/v1/run-configs/${serverId}/${pluginId}`),
  startRun: (value: RunRequest) => request<RunState>('/api/v1/runs', { method: 'POST', body: JSON.stringify(value) }),
  run: (id: string) => request<RunState>(`/api/v1/runs/${id}`),
  cancelRun: (id: string) => request<{ ok: boolean }>(`/api/v1/runs/${id}/cancel`, { method: 'POST' }),
  reports: () => request<DiagnosticReport[]>('/api/v1/reports'),
  report: (id: string) => request<DiagnosticReport>(`/api/v1/reports/${id}`),
  cache: () => request<{ bytes: number; dataRoot: string }>('/api/v1/cache'),
  clearCache: () => request<{ ok: boolean; bytes: number }>('/api/v1/cache', { method: 'DELETE' }),
}
