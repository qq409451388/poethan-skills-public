export type Authentication = 'alias' | 'key' | 'password' | 'demo'

export interface ServerProfile {
  id: string
  name: string
  authentication: Authentication
  alias: string
  host: string
  user: string
  port: number
  identityFile: string
  password?: string
  createdAt?: string
  updatedAt?: string
}

export interface AISettings {
  endpoint: string
  model: string
  configured: boolean
}

export interface ApplicationSettings {
  pluginDirectory: string
  developerMode: boolean
  demoMode: boolean
  ai: AISettings
  aiApiKey?: string
}

export interface PluginTrust {
  status: 'trusted' | 'local' | 'unsigned' | 'untrusted' | 'invalid'
  publisherId?: string
  keyId?: string
  fingerprint?: string
  lockDigest?: string
  message: string
}

export interface PluginField {
  key: string
  label: string
  type: 'text' | 'path' | 'integer' | 'url' | 'password' | 'boolean' | 'choice'
  section?: string
  default?: string | boolean | number
  placeholder?: string
  required?: boolean
  help?: string
  options?: Array<string | { value: string; label: string }>
}

export interface PluginPackage {
  id: string
  name: string
  description: string
  version: string
  toolType: 'plugin' | 'local_script' | 'server_script'
  entrypoint: string
  language: string
  outputLimit: number
  defaultMode: string
  modes: Array<{ id: string; label: string; help?: string }>
  fields: PluginField[]
  report?: { schema: string; template: string }
  permissions: Record<string, boolean>
  directory: string
  trust: PluginTrust
  valid: boolean
  errors: string[]
}

export interface PluginScanItem {
  directory: string
  valid: boolean
  plugin?: PluginPackage
  errors: string[]
}

export interface PluginScanResponse {
  items: PluginScanItem[]
  validCount: number
  invalidCount: number
}

export interface RunRequest {
  serverId: string
  pluginId: string
  pluginVersion: string
  mode: string
  values: Record<string, string>
  secrets: Record<string, string>
  remember: boolean
  aiEnabled: boolean
}

export interface RunEvent {
  sequence: number
  type: 'stage' | 'output' | 'complete' | 'error'
  stage: string
  message: string
  createdAt: string
}

export interface RunState {
  id: string
  serverId: string
  pluginId: string
  pluginVersion: string
  mode: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  stage: string
  message: string
  startedAt: string
  completedAt?: string
  reportId?: string
  events: RunEvent[]
}

export interface Finding {
  severity: 'critical' | 'warning' | 'info' | 'success'
  title: string
  evidence: string
  recommendation: string
}

export interface DiagnosticReport {
  id: string
  server: { id: string; name: string }
  plugin: { id: string; name: string; version: string; mode: string }
  status: 'completed' | 'failed' | 'cancelled'
  createdAt: string
  durationSeconds: number
  summary: string
  findings: Finding[]
  rawOutput: string
  ai?: { status?: string; content?: string; raw?: unknown; error?: string }
  audit: Record<string, unknown>
}

export type Page = 'server' | 'diagnostic' | 'plugins' | 'reports' | 'settings'
export type DiagnosticStage = 'select' | 'configure' | 'running' | 'result'
