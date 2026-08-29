import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { ServerModal } from './App'
import type { ServerProfile } from './types'

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
})
