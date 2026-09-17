import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { DiagnosticPage, ServerModal, ServerNavigationItem, SettingsPage, ToolLibrary } from './App'
import type { AIProfile, ApplicationSettings, PluginPackage, ServerProfile } from './types'

const deepseekProfile: AIProfile = { id: 'deepseek', name: 'DeepSeek 生产', endpoint: 'https://api.deepseek.com', model: 'deepseek-flash' }

const baseSettings = (profiles: AIProfile[], activeAiId: string): ApplicationSettings => ({
  pluginDirectory: '/tmp/plugins',
  developerMode: false,
  demoMode: true,
  aiProfiles: profiles,
  activeAiId,
  aiConfigured: Object.fromEntries(profiles.map((profile) => [profile.id, false])),
})

const settingsPageProps = (settings: ApplicationSettings, overrides: Record<string, unknown> = {}) => ({
  settings,
  setSettings: vi.fn(),
  scan: { items: [], validCount: 0, invalidCount: 0 },
  cacheBytes: 0,
  cacheRoot: '/tmp',
  saveSystem: vi.fn(async () => undefined),
  saveAI: vi.fn(async () => undefined),
  busy: '',
  rescan: vi.fn(async () => undefined),
  testAI: vi.fn(async () => ({ ok: true, message: '' })),
  clearCache: vi.fn(async () => undefined),
  showToast: vi.fn(),
  ...overrides,
})

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  })

  afterEach(() => cleanup())

  it('shows a controller startup state while bootstrap is pending', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Poethan Sentinel' })).toBeTruthy()
    expect(screen.getByText('正在启动本机诊断控制器…')).toBeTruthy()
  })

  it('does not pass the React click event into the SSH test callback', () => {
    const test = vi.fn()
    const draft: ServerProfile = {
      id: 'doris-test', name: 'Doris', authentication: 'alias', alias: 'doris',
      host: '', user: '', port: 22, identityFile: '',
    }
    render(<ServerModal
      draft={draft} setDraft={vi.fn()} saving={false} connectionResult=""
      close={vi.fn()} save={vi.fn()} test={test}
    />)
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }))
    expect(test).toHaveBeenCalledWith()
  })

  it('exposes server deletion without selecting the server first', () => {
    const select = vi.fn()
    const remove = vi.fn()
    const server: ServerProfile = {
      id: 'doris-test', name: 'Doris', authentication: 'alias', alias: 'doris',
      host: '', user: '', port: 22, identityFile: '',
    }
    render(<ServerNavigationItem server={server} active={false} deleting={false} onSelect={select} onDelete={remove}/>)

    fireEvent.click(screen.getByRole('button', { name: '删除服务器 Doris' }))

    expect(remove).toHaveBeenCalledOnce()
    expect(select).not.toHaveBeenCalled()
  })

  it('keeps the workflow title, progress and next action in one top dock', () => {
    const setStage = vi.fn()
    const server: ServerProfile = {
      id: 'doris-test', name: 'Doris', authentication: 'alias', alias: 'doris',
      host: '', user: '', port: 22, identityFile: '',
    }
    const plugin: PluginPackage = {
      id: 'network-diagnostic', name: '网络占用', description: '检查网络占用', version: '1.0.0',
      toolType: 'plugin',
      entrypoint: 'run.sh', language: 'python', outputLimit: 200000, defaultMode: 'standard',
      modes: [{ id: 'standard', label: '标准' }], fields: [], permissions: {}, directory: '/plugins/network',
      trust: { status: 'trusted', message: '签名有效' }, valid: true, errors: [],
    }
    const { container } = render(<DiagnosticPage
      stage="select" setStage={setStage} server={server} plugins={[plugin]} selectedPlugin={plugin}
      selectPlugin={vi.fn()} pluginSearch="" setPluginSearch={vi.fn()} mode="standard" setMode={vi.fn()}
      values={{}} setValues={vi.fn()} secrets={{}} setSecrets={vi.fn()} remember={true} setRemember={vi.fn()}
      aiEnabled={false} setAiEnabled={vi.fn()} aiConfigured={false} startRun={vi.fn()} busy="" run={null}
      events={[]} output="" cancel={vi.fn()} report={null} resultTab="conclusion" setResultTab={vi.fn()}
      rawSearch="" setRawSearch={vi.fn()} openReport={vi.fn()} goServer={vi.fn()} showToast={vi.fn()}
    />)

    const dock = container.querySelector('.workflow-dock')
    expect(dock).toBeTruthy()
    expect(dock?.querySelector('h1')?.textContent).toBe('选择检查插件')
    expect(dock?.querySelector('[aria-current="step"]')?.textContent).toContain('选择插件')
    fireEvent.click(screen.getByRole('button', { name: '下一步：配置检查 →' }))
    expect(setStage).toHaveBeenCalledWith('configure')
  })

  it('offers three tool sources and labels the tool type in the list', () => {
    const addLocal = vi.fn()
    const plugin: PluginPackage = {
      id: 'local-network', name: '本机网络采样', description: '检查网络占用', version: '1.0.0',
      toolType: 'local_script', entrypoint: 'run.sh', language: 'bash', outputLimit: 200000,
      defaultMode: 'standard', modes: [{ id: 'standard', label: '标准' }], fields: [], permissions: {},
      directory: '/tools/local-network', trust: { status: 'local', message: '本机管理' }, valid: true, errors: [],
    }
    const { container } = render(<ToolLibrary
      scan={{ items: [{ directory: plugin.directory, valid: true, plugin, errors: [] }], validCount: 1, invalidCount: 0 }}
      selectedIndex={0} setSelectedIndex={vi.fn()} onLocalScript={addLocal} onServerScript={vi.fn()}
      onImportPlugin={vi.fn()} busy=""
    />)

    fireEvent.click(screen.getByText('＋ 新增工具'))
    expect(screen.getByRole('button', { name: /^⌘\s+本机脚本/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^⌁\s+服务器脚本/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^⬡\s+插件/ })).toBeTruthy()
    expect(container.querySelector('.tool-type')?.textContent).toBe('本机脚本')
    fireEvent.click(screen.getByRole('button', { name: /^⌘\s+本机脚本/ }))
    expect(addLocal).toHaveBeenCalledOnce()
  })

  it('keeps settings split into system, AI and cache tabs', () => {
    render(<SettingsPage {...settingsPageProps(baseSettings([deepseekProfile], 'deepseek'))}/>)

    expect(screen.getByText('诊断工具目录')).toBeTruthy()
    expect(screen.queryByLabelText('服务商')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))
    expect(screen.queryByText('诊断工具目录')).toBeNull()
    expect(screen.getByLabelText('服务商')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '缓存' }))
    expect(screen.getByText('本机缓存')).toBeTruthy()
  })

  it('fills endpoint and model when an AI provider preset is selected', () => {
    render(<SettingsPage {...settingsPageProps(baseSettings([deepseekProfile], 'deepseek'))}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    expect((screen.getByLabelText('服务商') as HTMLSelectElement).value).toBe('deepseek')
    fireEvent.change(screen.getByLabelText('服务商'), { target: { value: 'kimi' } })
    expect((screen.getByLabelText('接口地址') as HTMLInputElement).value).toBe('https://api.moonshot.cn/v1')
    expect((screen.getByLabelText('模型') as HTMLInputElement).value).toBe('kimi-k3')
  })

  it('falls back to custom provider when the endpoint does not match any preset', () => {
    const custom: AIProfile = { id: 'custom', name: '内网网关', endpoint: 'https://ai.internal.corp/v1/', model: 'custom-model' }
    render(<SettingsPage {...settingsPageProps(baseSettings([custom], 'custom'))}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    expect((screen.getByLabelText('服务商') as HTMLSelectElement).value).toBe('custom')
  })

  it('shows masked dots for a saved key and never submits the mask', async () => {
    const saveAI = vi.fn(async (_value: { profiles: AIProfile[]; activeAiId: string; apiKeys?: Record<string, string> }) => undefined)
    const settings: ApplicationSettings = { ...baseSettings([deepseekProfile], 'deepseek'), aiConfigured: { deepseek: true } }
    render(<SettingsPage {...settingsPageProps(settings, { saveAI })}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    const input = screen.getByLabelText('API Key') as HTMLInputElement
    expect(input.value).not.toBe('')
    expect(input.value.startsWith('sk-')).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(saveAI).toHaveBeenCalledTimes(1))
    expect(saveAI.mock.calls[0][0].apiKeys ?? {}).toEqual({})

    fireEvent.focus(input)
    expect(input.value).toBe('')
  })

  it('saves a newly typed key for the edited profile', async () => {
    const saveAI = vi.fn(async (_value: { profiles: AIProfile[]; activeAiId: string; apiKeys?: Record<string, string> }) => undefined)
    const settings: ApplicationSettings = { ...baseSettings([deepseekProfile], 'deepseek'), aiConfigured: { deepseek: true } }
    render(<SettingsPage {...settingsPageProps(settings, { saveAI })}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-new-key' } })
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))

    await waitFor(() => expect(saveAI).toHaveBeenCalledTimes(1))
    expect(saveAI.mock.calls[0][0].apiKeys).toEqual({ deepseek: 'sk-new-key' })
  })

  it('saves a new AI profile through its own save button', async () => {
    const saveAI = vi.fn(async (_value: { profiles: AIProfile[]; activeAiId: string; apiKeys?: Record<string, string> }) => undefined)
    render(<SettingsPage {...settingsPageProps(baseSettings([deepseekProfile], 'deepseek'), { saveAI })}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    fireEvent.click(screen.getByRole('button', { name: '＋ 新增配置' }))
    fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: 'Kimi 备用' } })
    fireEvent.change(screen.getByLabelText('服务商'), { target: { value: 'kimi' } })
    fireEvent.click(screen.getByRole('button', { name: '保存新配置' }))

    await waitFor(() => expect(saveAI).toHaveBeenCalledTimes(1))
    const payload = saveAI.mock.calls[0][0]
    expect(payload.profiles).toHaveLength(2)
    expect(payload.profiles[1]).toMatchObject({ name: 'Kimi 备用', endpoint: 'https://api.moonshot.cn/v1', model: 'kimi-k3' })
    expect(payload.activeAiId).toBe('deepseek')
  })

  it('switches the active AI profile from the list', async () => {
    const kimi: AIProfile = { id: 'kimi', name: 'Kimi 备用', endpoint: 'https://api.moonshot.cn/v1', model: 'kimi-k3' }
    const saveAI = vi.fn(async () => undefined)
    render(<SettingsPage {...settingsPageProps(baseSettings([deepseekProfile, kimi], 'deepseek'), { saveAI })}/>)
    fireEvent.click(screen.getByRole('button', { name: 'AI 配置' }))

    fireEvent.click(screen.getByLabelText('启用 Kimi 备用'))

    await waitFor(() => expect(saveAI).toHaveBeenCalledWith(expect.objectContaining({ activeAiId: 'kimi' })))
  })
})
