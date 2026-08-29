import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { DiagnosticPage, ServerModal, ServerNavigationItem } from './App'
import type { PluginPackage, ServerProfile } from './types'

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
})
