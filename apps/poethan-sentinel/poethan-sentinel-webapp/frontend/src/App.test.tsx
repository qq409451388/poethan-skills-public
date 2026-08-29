import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)))
  })

  it('shows a controller startup state while bootstrap is pending', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Poethan Sentinel' })).toBeTruthy()
    expect(screen.getByText('正在启动本机诊断控制器…')).toBeTruthy()
  })
})
