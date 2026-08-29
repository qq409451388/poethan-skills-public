import { useEffect, useMemo, useRef, useState } from 'react'
import { api, bootstrap } from './api'
import type {
  ApplicationSettings,
  DiagnosticReport,
  DiagnosticStage,
  Page,
  PluginField,
  PluginPackage,
  PluginScanItem,
  PluginScanResponse,
  RunEvent,
  RunState,
  ServerProfile,
} from './types'

const emptyServer = (): ServerProfile => ({
  id: crypto.randomUUID(), name: '', authentication: 'alias', alias: '', host: '', user: '', port: 22, identityFile: '', password: '',
})

const formatDate = (value?: string) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '—'
const formatBytes = (bytes: number) => bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`
const pluginLetter = (plugin?: PluginPackage) => plugin?.name.slice(0, 1).toUpperCase() || '?'
const toolTypeLabel = (plugin?: PluginPackage) => plugin?.toolType === 'local_script' ? '本机脚本' : plugin?.toolType === 'server_script' ? '服务器脚本' : '插件'
export interface ScriptToolDraft { kind: 'local_script' | 'server_script'; name: string; description: string; runtime: 'bash' | 'python'; scriptPath: string }
const emptyScriptTool = (kind: ScriptToolDraft['kind']): ScriptToolDraft => ({ kind, name: '', description: '', runtime: 'bash', scriptPath: '' })
const serverTarget = (server?: ServerProfile) => {
  if (!server) return '—'
  if (server.authentication === 'demo') return '本机演示环境'
  if (server.authentication === 'alias') return server.alias || 'SSH 别名未填写'
  return `${server.user || 'user'}@${server.host || 'host'}:${server.port}`
}

function App() {
  const [ready, setReady] = useState(false)
  const [fatal, setFatal] = useState('')
  const [page, setPage] = useState<Page>('server')
  const [servers, setServers] = useState<ServerProfile[]>([])
  const [selectedServerId, setSelectedServerId] = useState('')
  const [plugins, setPlugins] = useState<PluginScanResponse>({ items: [], validCount: 0, invalidCount: 0 })
  const [reports, setReports] = useState<DiagnosticReport[]>([])
  const [settings, setSettings] = useState<ApplicationSettings | null>(null)
  const [cacheBytes, setCacheBytes] = useState(0)
  const [cacheRoot, setCacheRoot] = useState('')
  const [toast, setToast] = useState('')
  const [mobileNav, setMobileNav] = useState(false)

  const [serverModal, setServerModal] = useState(false)
  const [serverDraft, setServerDraft] = useState<ServerProfile>(emptyServer())
  const [serverSaving, setServerSaving] = useState(false)
  const [connectionResult, setConnectionResult] = useState('')

  const [pluginSearch, setPluginSearch] = useState('')
  const [selectedPluginId, setSelectedPluginId] = useState('')
  const [libraryIndex, setLibraryIndex] = useState(0)
  const importRef = useRef<HTMLInputElement>(null)
  const [scriptToolDraft, setScriptToolDraft] = useState<ScriptToolDraft | null>(null)
  const [localScriptFile, setLocalScriptFile] = useState<File | null>(null)

  const [diagnosticStage, setDiagnosticStage] = useState<DiagnosticStage>('select')
  const [mode, setMode] = useState('standard')
  const [values, setValues] = useState<Record<string, string>>({})
  const [secretValues, setSecretValues] = useState<Record<string, string>>({})
  const [remember, setRemember] = useState(true)
  const [aiEnabled, setAiEnabled] = useState(false)
  const [run, setRun] = useState<RunState | null>(null)
  const [runEvents, setRunEvents] = useState<RunEvent[]>([])
  const [liveOutput, setLiveOutput] = useState('')
  const [report, setReport] = useState<DiagnosticReport | null>(null)
  const [resultTab, setResultTab] = useState<'conclusion' | 'raw' | 'ai'>('conclusion')
  const [rawSearch, setRawSearch] = useState('')
  const [busy, setBusy] = useState('')

  const selectedServer = servers.find((item) => item.id === selectedServerId) || servers[0]
  const validPlugins = useMemo(() => plugins.items.flatMap((item) => item.valid && item.plugin ? [item.plugin] : []), [plugins])
  const selectedPlugin = validPlugins.find((item) => item.id === selectedPluginId) || validPlugins[0]
  const recentServerReport = reports.find((item) => item.server.id === selectedServer?.id)

  const showToast = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(''), 2600)
  }

  const refresh = async () => {
    const [nextServers, nextPlugins, nextReports, nextSettings, cache] = await Promise.all([
      api.servers(), api.plugins(), api.reports(), api.settings(), api.cache(),
    ])
    setServers(nextServers)
    setSelectedServerId((current) => current || nextServers[0]?.id || '')
    setPlugins(nextPlugins)
    const firstPlugin = nextPlugins.items.find((item) => item.valid)?.plugin
    setSelectedPluginId((current) => current || firstPlugin?.id || '')
    setReports(nextReports)
    setSettings(nextSettings)
    setAiEnabled(nextSettings.ai.configured)
    setCacheBytes(cache.bytes); setCacheRoot(cache.dataRoot)
  }

  useEffect(() => {
    bootstrap().then(refresh).then(() => setReady(true)).catch((error: Error) => setFatal(error.message))
  }, [])

  const navigate = (next: Page) => {
    setPage(next); setMobileNav(false)
    if (next === 'diagnostic') { setDiagnosticStage('select'); setReport(null); setRun(null); setRunEvents([]); setLiveOutput('') }
  }

  const openServer = (server?: ServerProfile) => {
    setServerDraft(server ? { ...server, password: '' } : emptyServer())
    setConnectionResult(''); setServerModal(true)
  }

  const saveServer = async () => {
    if (!serverDraft.name.trim()) { showToast('请填写服务器名称'); return }
    setServerSaving(true)
    try {
      const exists = servers.some((item) => item.id === serverDraft.id)
      const saved = exists ? await api.updateServer(serverDraft) : await api.createServer(serverDraft)
      const next = exists ? servers.map((item) => item.id === saved.id ? saved : item) : [...servers, saved]
      setServers(next); setSelectedServerId(saved.id); setServerModal(false); showToast(exists ? '服务器配置已更新' : '服务器已添加')
    } catch (error) { showToast((error as Error).message) } finally { setServerSaving(false) }
  }

  const testServer = async (accept = false) => {
    setConnectionResult('正在测试连接…')
    try {
      const result = await api.testServer(serverDraft, accept)
      if (result.hostKeyRequired && !accept) {
        const confirmed = window.confirm(`首次连接，需要信任服务器指纹：\n${result.fingerprint || '未知'}\n\n是否信任并继续？`)
        if (confirmed) return testServer(true)
      }
      setConnectionResult(result.ok ? `连接成功${result.latencyMs ? ` · ${result.latencyMs} ms` : ''}` : result.message)
    } catch (error) { setConnectionResult((error as Error).message) }
  }

  const deleteServer = async (server?: ServerProfile) => {
    if (!server || server.authentication === 'demo') return
    if (!window.confirm(`确定删除“${server.name}”的本机配置？不会改动远端服务器。`)) return
    setBusy(`delete-server:${server.id}`)
    try {
      await api.deleteServer(server.id)
      const next = servers.filter((item) => item.id !== server.id)
      setServers(next)
      if (selectedServer?.id === server.id) {
        setSelectedServerId(next[0]?.id || '')
        setPage('server')
      }
      if (serverDraft.id === server.id) setServerModal(false)
      showToast(`已删除服务器“${server.name}”`)
    } catch (error) { showToast((error as Error).message) } finally { setBusy('') }
  }

  const choosePlugin = async (plugin: PluginPackage) => {
    setSelectedPluginId(plugin.id)
    setMode(plugin.defaultMode)
    const defaults = Object.fromEntries(plugin.fields.filter((field) => field.type !== 'password').map((field) => [field.key, String(field.default ?? '')]))
    try {
      const saved = selectedServer ? await api.runConfig(selectedServer.id, plugin.id) : { mode: plugin.defaultMode, values: {} }
      setMode(saved.mode || plugin.defaultMode); setValues({ ...defaults, ...saved.values }); setSecretValues({})
    } catch { setValues(defaults); setSecretValues({}) }
  }

  useEffect(() => {
    if (selectedPlugin && selectedServer) void choosePlugin(selectedPlugin)
    // Selecting a new server intentionally reloads that server's saved plugin values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedServerId, selectedPlugin?.id])

  const startRun = async () => {
    if (!selectedServer || !selectedPlugin) return
    for (const field of selectedPlugin.fields) {
      const fieldValue = field.type === 'password' ? secretValues[field.key] : values[field.key]
      if (field.required && !fieldValue && field.type !== 'password') { showToast(`请填写：${field.label}`); return }
    }
    setBusy('run')
    try {
      const nextRun = await api.startRun({ serverId: selectedServer.id, pluginId: selectedPlugin.id, pluginVersion: selectedPlugin.version, mode, values, secrets: secretValues, remember, aiEnabled })
      setRun(nextRun); setRunEvents(nextRun.events); setLiveOutput(''); setDiagnosticStage('running')
      subscribeRun(nextRun.id)
    } catch (error) { showToast((error as Error).message) } finally { setBusy('') }
  }

  const subscribeRun = (runId: string) => {
    const source = new EventSource(`/api/v1/runs/${runId}/events`, { withCredentials: true })
    const revealLocalReport = async () => {
      try {
        const state = await api.run(runId)
        if (state.reportId) {
          const nextReport = await api.report(state.reportId)
          setRun(state); setReport(nextReport); setDiagnosticStage('result')
        }
      } catch { /* The final event will surface a persistent error if this retry fails. */ }
    }
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent
      setRunEvents((current) => current.some((item) => item.sequence === event.sequence) ? current : [...current, event])
      if (event.type === 'output') setLiveOutput((current) => current + event.message)
      if (event.stage === 'report_ready') void revealLocalReport()
    }
    ;['stage', 'output'].forEach((name) => source.addEventListener(name, receive as EventListener))
    const finish = async (message: MessageEvent<string>) => {
      receive(message); source.close()
      try {
        const state = await api.run(runId); setRun(state)
        if (state.reportId) { const nextReport = await api.report(state.reportId); setReport(nextReport); setReports((items) => [nextReport, ...items.filter((item) => item.id !== nextReport.id)]); setDiagnosticStage('result') }
        else showToast(state.message)
      } catch (error) { showToast((error as Error).message) }
    }
    source.addEventListener('complete', finish as unknown as EventListener)
    source.addEventListener('error', (message) => {
      if (message instanceof MessageEvent && message.data) void finish(message as MessageEvent<string>)
      else { source.close(); void api.run(runId).then((state) => { setRun(state); if (state.reportId) return api.report(state.reportId).then((value) => { setReport(value); setDiagnosticStage('result') }) }) }
    })
  }

  const openReport = async (item: DiagnosticReport) => {
    setReport(item); setSelectedServerId(item.server.id); setSelectedPluginId(item.plugin.id); setMode(item.plugin.mode); setDiagnosticStage('result'); setPage('diagnostic'); setResultTab('conclusion')
  }

  const importPlugin = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy('import')
    try { const plugin = await api.importPlugin(files); showToast(`已导入插件 ${plugin.name} ${plugin.version}`); const next = await api.rescanPlugins(); setPlugins(next); setSelectedPluginId(plugin.id); setLibraryIndex(Math.max(0, next.items.findIndex((item) => item.plugin?.id === plugin.id))) }
    catch (error) { showToast(`导入失败：${(error as Error).message}`) }
    finally { setBusy(''); if (importRef.current) importRef.current.value = '' }
  }

  const createScriptTool = async () => {
    if (!scriptToolDraft?.name.trim()) { showToast('请填写工具名称'); return }
    if (scriptToolDraft.kind === 'local_script' && !localScriptFile) { showToast('请选择本机脚本文件'); return }
    if (scriptToolDraft.kind === 'server_script' && !scriptToolDraft.scriptPath.trim()) { showToast('请填写服务器脚本路径'); return }
    setBusy('create-tool')
    try {
      const created = scriptToolDraft.kind === 'local_script'
        ? await api.createLocalScriptTool({ name: scriptToolDraft.name, description: scriptToolDraft.description, runtime: scriptToolDraft.runtime, script: localScriptFile! })
        : await api.createServerScriptTool({ name: scriptToolDraft.name, description: scriptToolDraft.description, runtime: scriptToolDraft.runtime, scriptPath: scriptToolDraft.scriptPath })
      const next = await api.rescanPlugins()
      setPlugins(next); setSelectedPluginId(created.id); setLibraryIndex(Math.max(0, next.items.findIndex((item) => item.plugin?.id === created.id)))
      setScriptToolDraft(null); setLocalScriptFile(null); showToast(`已新增${toolTypeLabel(created)}“${created.name}”`)
    } catch (error) { showToast(`新增失败：${(error as Error).message}`) } finally { setBusy('') }
  }

  const saveApplicationSettings = async () => {
    if (!settings) return
    setBusy('settings')
    try { const saved = await api.saveSettings(settings); setSettings(saved); setPlugins(await api.rescanPlugins()); showToast('设置已保存') }
    catch (error) { showToast((error as Error).message) } finally { setBusy('') }
  }

  if (fatal) return <div className="fatal"><h1>Poethan Sentinel 无法启动</h1><p>{fatal}</p><button className="button primary" onClick={() => location.reload()}>重新加载</button></div>
  if (!ready || !settings) return <div className="boot"><div className="brand-mark"><BrandIcon /></div><h1>Poethan Sentinel</h1><p>正在启动本机诊断控制器…</p></div>

  return <>
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? 'open' : ''}`} aria-label="主导航">
        <div className="brand"><div className="brand-mark"><BrandIcon /></div><div><strong>Poethan Sentinel</strong><span>远程诊断工作台</span></div></div>
        <button className="button primary add-server" onClick={() => openServer()}><span>＋</span>新建服务器</button>
        <nav className="sidebar-nav">
          <p className="nav-heading">服务器</p>
          {servers.map((server) => <ServerNavigationItem
            key={server.id} server={server} active={page === 'server' && server.id === selectedServer?.id}
            deleting={busy === `delete-server:${server.id}`}
            onSelect={() => { setSelectedServerId(server.id); navigate('server') }}
            onDelete={() => void deleteServer(server)}
          />)}
          <p className="nav-heading tools-heading">工具</p>
          <button className={`nav-item compact ${page === 'plugins' ? 'active' : ''}`} onClick={() => navigate('plugins')}><span className="nav-icon">⬡</span><span><b>诊断工具库</b><small>{plugins.validCount} 个可用{plugins.invalidCount ? ` · ${plugins.invalidCount} 个失败` : ''}</small></span></button>
          <button className={`nav-item compact ${page === 'reports' ? 'active' : ''}`} onClick={() => navigate('reports')}><span className="nav-icon">≡</span><span><b>历史报告</b><small>{reports.length} 份本机报告</small></span></button>
        </nav>
        <button className={`nav-item compact settings-link ${page === 'settings' ? 'active' : ''}`} onClick={() => navigate('settings')}><span className="nav-icon">⚙</span><span><b>设置</b><small>AI、插件目录与缓存</small></span></button>
      </aside>

      <main className="workspace">
        <header className="topbar"><button className="icon-button mobile-menu" onClick={() => setMobileNav(true)}>☰</button><div className="breadcrumb"><span>Poethan Sentinel</span><b>{page === 'server' ? selectedServer?.name : page === 'diagnostic' ? '新建诊断' : page === 'plugins' ? '诊断工具库' : page === 'reports' ? '历史报告' : '设置'}</b></div><div className="top-actions"><span className="connection-chip"><i/>本机控制器已连接</span></div></header>
        <div className="pages">
          {page === 'server' && <ServerPage
            server={selectedServer} report={recentServerReport} busy={busy}
            onStart={() => navigate('diagnostic')} onEdit={() => openServer(selectedServer)}
            onDelete={() => void deleteServer(selectedServer)} onOpenReport={openReport}
            onTest={async () => {
              if (!selectedServer) return
              setBusy('test-server')
              try { const result = await api.testServer(selectedServer); showToast(result.message) }
              catch (error) { showToast((error as Error).message) }
              finally { setBusy('') }
            }}
          />}
          {page === 'diagnostic' && <DiagnosticPage
            stage={diagnosticStage} setStage={setDiagnosticStage} server={selectedServer}
            plugins={validPlugins} selectedPlugin={selectedPlugin} selectPlugin={choosePlugin}
            pluginSearch={pluginSearch} setPluginSearch={setPluginSearch} mode={mode} setMode={setMode}
            values={values} setValues={setValues} secrets={secretValues} setSecrets={setSecretValues}
            remember={remember} setRemember={setRemember} aiEnabled={aiEnabled} setAiEnabled={setAiEnabled}
            aiConfigured={settings.ai.configured} startRun={startRun} busy={busy} run={run}
            events={runEvents} output={liveOutput} cancel={async () => run && api.cancelRun(run.id)}
            report={report} resultTab={resultTab} setResultTab={setResultTab}
            rawSearch={rawSearch} setRawSearch={setRawSearch} openReport={openReport}
            goServer={() => navigate('server')} showToast={showToast}
          />}
          {page === 'plugins' && <ToolLibrary
            scan={plugins} selectedIndex={libraryIndex} setSelectedIndex={setLibraryIndex}
            onLocalScript={() => { setScriptToolDraft(emptyScriptTool('local_script')); setLocalScriptFile(null) }}
            onServerScript={() => { setScriptToolDraft(emptyScriptTool('server_script')); setLocalScriptFile(null) }}
            onImportPlugin={() => importRef.current?.click()} busy={busy}
          />}
          {page === 'reports' && <ReportsPage
            reports={reports}
            openReport={openReport}
          />}
          {page === 'settings' && <SettingsPage
            settings={settings} setSettings={setSettings} scan={plugins}
            cacheBytes={cacheBytes} cacheRoot={cacheRoot} save={saveApplicationSettings} busy={busy}
            rescan={async () => setPlugins(await api.rescanPlugins())}
            testAI={async (key) => {
              setBusy('ai-test')
              try {
                const result = await api.testAI(settings.ai.endpoint, settings.ai.model, key)
                showToast(result.message || 'AI 连接成功')
                return result
              } catch (error) {
                showToast((error as Error).message)
                throw error
              } finally { setBusy('') }
            }}
            clearCache={async () => {
              if (!confirm('清空本机诊断报告和 AI 缓存？服务器和插件配置不会删除。')) return
              await api.clearCache(); setCacheBytes(0); setReports([]); showToast('缓存已清空')
            }}
            showToast={showToast}
          />}
        </div>
      </main>
    </div>
    <div className={`scrim ${mobileNav ? 'open' : ''}`} onClick={() => setMobileNav(false)}/>
    {serverModal && <ServerModal
      draft={serverDraft} setDraft={setServerDraft} saving={serverSaving}
      connectionResult={connectionResult} close={() => setServerModal(false)}
      save={saveServer} test={testServer}
    />}
    {scriptToolDraft && <ScriptToolModal
      draft={scriptToolDraft} setDraft={setScriptToolDraft} file={localScriptFile} setFile={setLocalScriptFile}
      saving={busy === 'create-tool'} close={() => { setScriptToolDraft(null); setLocalScriptFile(null) }} save={createScriptTool}
    />}
    <input ref={importRef} className="visually-hidden" type="file" multiple {...({ webkitdirectory: '' } as React.InputHTMLAttributes<HTMLInputElement>)} onChange={(event) => void importPlugin(event.target.files)}/>
    <div className={`toast ${toast ? 'show' : ''}`}><span>✓</span><p>{toast}</p></div>
  </>
}

function BrandIcon() {
  return <svg viewBox="0 0 40 40"><rect x="5" y="7" width="30" height="8" rx="3"/><rect x="5" y="17" width="30" height="8" rx="3"/><rect x="5" y="27" width="30" height="8" rx="3"/><path d="M10 11h9m-9 10h15m-15 10h12"/></svg>
}

export function ServerNavigationItem({ server, active, deleting, onSelect, onDelete }: { server: ServerProfile; active: boolean; deleting: boolean; onSelect: () => void; onDelete: () => void }) {
  return <div className="server-nav-row">
    <button className={`nav-item ${active ? 'active' : ''}`} onClick={onSelect}><i className="dot online"/><span><b>{server.name}</b><small>{serverTarget(server)}</small></span></button>
    {server.authentication !== 'demo' && <button className="server-delete" disabled={deleting} aria-label={`删除服务器 ${server.name}`} title="删除服务器" onClick={onDelete}>{deleting ? '…' : '×'}</button>}
  </div>
}

function ServerPage({ server, report, onStart, onEdit, onDelete, onOpenReport, onTest, busy }: { server?: ServerProfile; report?: DiagnosticReport; onStart: () => void; onEdit: () => void; onDelete: () => void; onOpenReport: (report: DiagnosticReport) => void; onTest: () => void; busy: string }) {
  const issueCount = report?.findings.filter((item) => ['critical', 'warning'].includes(item.severity)).length || 0
  return <section className="page active"><div className="page-content server-page">
    <header className="page-heading server-heading"><div><span className="eyebrow">服务器概览</span><h1>{server?.name || '尚未添加服务器'}</h1><p className="address"><i className="dot online"/><span>{serverTarget(server)}</span></p></div><div className="heading-actions"><button className="button quiet" onClick={onEdit}>编辑服务器</button>{server?.authentication !== 'demo' && <button className="button danger subtle" onClick={onDelete}>删除</button>}</div></header>
    <section className="diagnostic-hero"><div className="signal-bars">{Array.from({ length: 7 }, (_, i) => <i key={i}/>)}</div><div><span className="eyebrow">准备就绪</span><h2>从一个明确的问题开始诊断</h2><p>选择一个插件，确认检查范围，然后由 Sentinel 完成远程执行、结果收集和报告生成。</p></div><button className="button primary large" disabled={!server} onClick={onStart}>开始新诊断 <span>→</span></button></section>
    <div className="overview-grid"><section className="panel recent-run"><div className="panel-heading"><div><span className="eyebrow">最近一次诊断</span><h3>{report?.plugin.name || '暂无诊断记录'}</h3></div>{report && <span className={`severity ${issueCount ? 'warning' : 'success'}`}>{issueCount ? `${issueCount} 项需要关注` : '全部正常'}</span>}</div><p>{report?.summary || '完成一次检查后，结论和原始输出会保存在这里。'}</p>{report && <div className="metadata"><div><span>完成时间</span><b>{formatDate(report.createdAt)}</b></div><div><span>运行模式</span><b>{report.plugin.mode}</b></div><div><span>耗时</span><b>{report.durationSeconds.toFixed(1)} 秒</b></div><div><span>AI 分析</span><b>{report.ai?.status === 'completed' ? '已完成' : report.ai ? '失败' : '未启用'}</b></div></div>}{report && <button className="text-button" onClick={() => onOpenReport(report)}>查看完整报告 →</button>}</section>
      <section className="panel server-health"><div className="panel-heading"><div><span className="eyebrow">连接配置</span><h3>{server?.authentication === 'demo' ? '演示模式可用' : '等待连接测试'}</h3></div><span className="status-symbol success">✓</span></div><div className="fact-list"><div><span>登录方式</span><b>{server?.authentication || '—'}</b></div><div><span>连接目标</span><b>{serverTarget(server)}</b></div><div><span>工具执行</span><b>远端版本缓存</b></div><div><span>主机密钥</span><b>严格校验</b></div></div><button className="button quiet full" disabled={!server || busy === 'test-server'} onClick={onTest}>{busy === 'test-server' ? '测试中…' : '测试连接'}</button></section></div>
  </div></section>
}

interface DiagnosticProps {
  stage: DiagnosticStage; setStage: (value: DiagnosticStage) => void; server?: ServerProfile; plugins: PluginPackage[]; selectedPlugin?: PluginPackage; selectPlugin: (plugin: PluginPackage) => void; pluginSearch: string; setPluginSearch: (value: string) => void; mode: string; setMode: (value: string) => void; values: Record<string, string>; setValues: React.Dispatch<React.SetStateAction<Record<string, string>>>; secrets: Record<string, string>; setSecrets: React.Dispatch<React.SetStateAction<Record<string, string>>>; remember: boolean; setRemember: (value: boolean) => void; aiEnabled: boolean; setAiEnabled: (value: boolean) => void; aiConfigured: boolean; startRun: () => void; busy: string; run: RunState | null; events: RunEvent[]; output: string; cancel: () => void; report: DiagnosticReport | null; resultTab: 'conclusion' | 'raw' | 'ai'; setResultTab: (value: 'conclusion' | 'raw' | 'ai') => void; rawSearch: string; setRawSearch: (value: string) => void; openReport: (report: DiagnosticReport) => void; goServer: () => void; showToast: (message: string) => void
}

export function DiagnosticPage(props: DiagnosticProps) {
  const stages: DiagnosticStage[] = ['select', 'configure', 'running', 'result']
  const progress = stages.indexOf(props.stage)
  const titles: Record<DiagnosticStage, string> = {
    select: '选择检查插件', configure: '配置检查', running: `正在诊断 ${props.server?.name || ''}`, result: '查看诊断结果',
  }
  const steps: Array<[DiagnosticStage, string]> = [['select','选择插件'],['configure','配置检查'],['running','远程执行'],['result','查看结果']]
  return <section className="page active diagnostic-page"><header className="workflow-dock"><div className="workflow-dock-inner">
    <div className="workflow-identity"><button className="workflow-back" aria-label="返回服务器" title="返回服务器" onClick={props.goServer}>←</button><div><span>{props.server?.name || '未选择服务器'} · 步骤 {progress + 1} / 4</span><h1>{titles[props.stage]}</h1></div></div>
    <div className="workflow-progress" aria-label="诊断进度"><div className="workflow-track"><i style={{ width: `${progress / 3 * 100}%` }}/></div>{steps.map(([key, label], index) => <div key={key} className={`workflow-step ${index === progress ? 'active' : index < progress ? 'done' : ''}`} aria-current={index === progress ? 'step' : undefined}><b>{index < progress ? '✓' : index + 1}</b><span>{label}</span></div>)}</div>
    <div className="workflow-actions">
      {props.stage === 'select' && <button className="button primary" disabled={!props.selectedPlugin} onClick={() => props.setStage('configure')}>下一步：配置检查 →</button>}
      {props.stage === 'configure' && <><button className="button quiet" onClick={() => props.setStage('select')}>上一步</button><button className="button primary" disabled={props.busy === 'run'} onClick={props.startRun}>{props.busy === 'run' ? '正在准备…' : '开始检查 →'}</button></>}
      {props.stage === 'running' && <button className="button danger" onClick={props.cancel}>停止检查</button>}
      {props.stage === 'result' && <><button className="button quiet" onClick={() => props.setStage('configure')}>再次检查</button>{props.report && <a className="button primary" target="_blank" rel="noreferrer" href={`/api/v1/reports/${props.report.id}/html`}>打开报告</a>}</>}
    </div>
  </div></header>
    <div className="diagnostic-body">
      {props.stage === 'select' && <section className="stage active"><header className="stage-heading stage-intro"><p>一次只运行一个诊断工具，让配置、输出和报告保持清晰。</p><label className="search"><span>⌕</span><input value={props.pluginSearch} onChange={(e) => props.setPluginSearch(e.target.value)} placeholder="搜索诊断工具"/></label></header><div className="plugin-options" role="radiogroup">{props.plugins.filter((plugin) => `${plugin.name}${plugin.description}${plugin.id}${toolTypeLabel(plugin)}`.toLowerCase().includes(props.pluginSearch.toLowerCase())).map((plugin) => <label key={plugin.id} className={`plugin-option ${props.selectedPlugin?.id === plugin.id ? 'selected' : ''}`}><input type="radio" name="plugin" checked={props.selectedPlugin?.id === plugin.id} onChange={() => props.selectPlugin(plugin)}/><i className="radio"/><span className="plugin-mark">{pluginLetter(plugin)}</span><span><b>{plugin.name}</b><small>{plugin.description || '由工具清单定义的诊断流程'}</small><em>{toolTypeLabel(plugin)} · {plugin.version} · {plugin.language} · {plugin.modes.length} 种模式</em></span><strong>{plugin.trust.status === 'trusted' ? '签名有效' : plugin.trust.status === 'local' ? '本机创建' : '开发模式'}</strong></label>)}</div>{!props.plugins.length && <Empty title="没有可用诊断工具" text="请先到诊断工具库新增脚本或导入插件。"/>}</section>}
      {props.stage === 'configure' && props.selectedPlugin && (
        <ConfigureStage {...props}/>
      )}
      {props.stage === 'running' && (
        <RunningStage {...props}/>
      )}
      {props.stage === 'result' && props.report && (
        <ResultStage {...props} report={props.report}/>
      )}
    </div>
  </section>
}

function ConfigureStage(props: DiagnosticProps) {
  const plugin = props.selectedPlugin!
  const grouped = plugin.fields.reduce<Record<string, PluginField[]>>((result, field) => {
    const section = field.section || '检查参数'
    result[section] = [...(result[section] || []), field]
    return result
  }, {})
  return <section className="stage active"><header className="stage-heading stage-intro"><p>字段和默认值均来自插件包的 plugin.yaml。</p><div className="manifest-chip"><span className="plugin-mark small">{pluginLetter(plugin)}</span><span><b>{plugin.name}</b><small>plugin.yaml · {plugin.version}</small></span></div></header><form className="configuration-form" onSubmit={(e) => { e.preventDefault(); props.startRun() }}>
    <section className="form-section"><header><b>运行模式</b><small>一次只执行一种模式</small></header><div className="mode-options">{plugin.modes.map((item) => <label key={item.id} className={props.mode === item.id ? 'selected' : ''}><input type="radio" name="mode" checked={props.mode === item.id} onChange={() => props.setMode(item.id)}/><span><b>{item.label}</b><small>{item.help}</small></span></label>)}</div></section>
    {Object.entries(grouped).map(([section, fields]) => <section className="form-section" key={section}><header><b>{section}</b><small>来自 plugin.yaml</small></header><div className="form-grid">{fields.map((field) => <DynamicField key={field.key} field={field} value={(field.type === 'password' ? props.secrets : props.values)[field.key] || ''} setValue={(value) => field.type === 'password' ? props.setSecrets((current) => ({ ...current, [field.key]: value })) : props.setValues((current) => ({ ...current, [field.key]: value }))}/>)}</div></section>)}
    <section className="form-section"><header><b>报告增强</b><small>可选</small></header><div className="form-grid one-column"><label className={`switch-row ${!props.aiConfigured ? 'disabled' : ''}`}><span><b>使用 AI 增强分析</b><small>{props.aiConfigured ? '本地报告先生成，AI 失败也不会丢失原始结果' : '请先到设置中配置并测试 AI'}</small></span><input type="checkbox" disabled={!props.aiConfigured} checked={props.aiEnabled} onChange={(e) => props.setAiEnabled(e.target.checked)}/><i/></label><label className="switch-row"><span><b>记住这台服务器的配置</b><small>敏感字段只保存到系统钥匙串</small></span><input type="checkbox" checked={props.remember} onChange={(e) => props.setRemember(e.target.checked)}/><i/></label></div></section>
    <details className="execution-preview"><summary>查看实际执行信息</summary><div><code>/opt/poethan-sentinel/plugins/{plugin.id}/{plugin.version}/{plugin.entrypoint} {props.mode}</code><p>只有版本或签名摘要发生变化时才同步插件；运行配置通过权限为 600 的临时文件传入。</p></div></details>
  </form></section>
}

function DynamicField({ field, value, setValue }: { field: PluginField; value: string; setValue: (value: string) => void }) {
  if (field.type === 'boolean') return <label className="switch-row field-switch"><span><b>{field.label}</b><small>{field.help}</small></span><input type="checkbox" checked={value === 'true'} onChange={(e) => setValue(String(e.target.checked))}/><i/></label>
  if (field.type === 'choice') return <label><span>{field.label}{field.required && <sup>*</sup>}</span><select value={value} onChange={(e) => setValue(e.target.value)}>{field.options?.map((option) => { const item = typeof option === 'string' ? { value: option, label: option } : option; return <option key={item.value} value={item.value}>{item.label}</option> })}</select>{field.help && <small>{field.help}</small>}</label>
  return <label><span>{field.label}{field.required && <sup>*</sup>}</span><input type={field.type === 'password' ? 'password' : field.type === 'integer' ? 'number' : field.type === 'url' ? 'url' : 'text'} value={value} onChange={(e) => setValue(e.target.value)} placeholder={field.type === 'password' ? '留空则使用已保存值' : field.placeholder}/>{field.help && <small>{field.help}</small>}</label>
}

function RunningStage(props: DiagnosticProps) {
  const stageOrder = ['connection', 'sync', 'execute', 'download', 'report', 'ai', 'complete']
  const current = stageOrder.indexOf(props.events.at(-1)?.stage || 'connection')
  const definitions = [['connection','检查 SSH 连接'],['sync','确认插件版本'],['execute','执行诊断脚本'],['download','下载检查结果'],['report','生成诊断报告'], ...(props.aiEnabled ? [['ai','AI 增强分析']] : [])]
  return <section className="stage active"><header className="run-heading"><div className="orbit"><span/><i/></div><div><span className="eyebrow">实时采集</span><h2>{props.selectedPlugin?.name}</h2><p>{props.mode} 模式 · 实时事件 {props.events.length} 条</p></div></header><div className="run-grid"><ol className="run-steps">{definitions.map(([key, label]) => { const index = stageOrder.indexOf(key); const event = [...props.events].reverse().find((item) => item.stage === key); return <li key={key} className={index < current ? 'done' : index === current ? 'current' : ''}><i/><span><b>{label}</b><small>{event?.message || '等待执行'}</small></span><time>{event ? formatDate(event.createdAt).split(' ').at(-1) : '—'}</time></li> })}</ol><details className="live-output" open><summary><span>实时输出</span><small>{props.output.split('\n').filter(Boolean).length} 行</small></summary><pre>{props.output || props.events.map((item) => `[${item.stage}] ${item.message}`).join('\n')}</pre></details></div></section>
}

function ResultStage(props: DiagnosticProps & { report: DiagnosticReport }) {
  const { report } = props
  const counts = { critical: 0, warning: 0, success: 0, info: 0 }
  report.findings.forEach((item) => counts[item.severity]++)
  const sections = (report.rawOutput.match(/^===== SECTION:/gm) || []).length
  const filteredRaw = props.rawSearch ? report.rawOutput.split('\n').filter((line) => line.toLowerCase().includes(props.rawSearch.toLowerCase())).join('\n') : report.rawOutput
  const aiPending = props.aiEnabled && !report.ai
  return <section className="stage active"><header className="result-heading"><span className={`result-symbol ${counts.critical ? 'critical' : counts.warning ? 'warning' : 'success'}`}>{counts.critical || counts.warning ? '!' : '✓'}</span><div><span className="eyebrow">诊断完成 · 用时 {report.durationSeconds.toFixed(1)} 秒</span><h2>{report.plugin.name}</h2><p>{report.server.name} · {report.plugin.mode} · {formatDate(report.createdAt)}</p></div></header><div className="result-summary"><div><span>严重</span><b className="danger-text">{counts.critical}</b></div><div><span>警告</span><b className="warning-text">{counts.warning}</b></div><div><span>正常</span><b className="success-text">{counts.success}</b></div><div><span>采集区段</span><b>{sections}</b></div></div><div className="tabs"><button className={props.resultTab === 'conclusion' ? 'active' : ''} onClick={() => props.setResultTab('conclusion')}>诊断结论</button><button className={props.resultTab === 'raw' ? 'active' : ''} onClick={() => props.setResultTab('raw')}>原始输出</button><button className={props.resultTab === 'ai' ? 'active' : ''} onClick={() => props.setResultTab('ai')}>AI 分析 {aiPending && <i>生成中</i>}</button></div>
    {props.resultTab === 'conclusion' && <div className="tab-panel active">{report.findings.map((item, index) => <article className={`finding ${item.severity}`} key={`${item.title}-${index}`}><span>{item.severity === 'success' ? '✓' : '!'}</span><div><header><h3>{item.title}</h3><i>{item.severity === 'critical' ? '严重' : item.severity === 'warning' ? '警告' : item.severity === 'success' ? '正常' : '信息'}</i></header><p>{item.evidence}</p>{item.recommendation && <aside><b>建议</b>{item.recommendation}</aside>}</div></article>)}</div>}
    {props.resultTab === 'raw' && <div className="tab-panel active"><div className="raw-toolbar"><label className="search"><span>⌕</span><input value={props.rawSearch} onChange={(e) => props.setRawSearch(e.target.value)} placeholder="筛选原始输出"/></label><button className="button quiet" onClick={() => navigator.clipboard.writeText(report.rawOutput).then(() => props.showToast('原始输出已复制'))}>复制全部</button></div><pre className="raw-report">{filteredRaw}</pre></div>}
    {props.resultTab === 'ai' && <div className="tab-panel active">{aiPending ? <div className="ai-loading"><div className="ai-scanner"><i/></div><h3>AI 正在关联诊断证据</h3><p>本地报告已经可用，AI 完成后此处会自动更新。</p></div> : report.ai?.status === 'failed' ? <Empty title="AI 分析失败" text={report.ai.error || '请检查接口配置，原始诊断报告不受影响。'}/> : report.ai ? <div className="ai-report"><header><span>AI</span><div><h3>增强分析结果</h3><p>这是基于诊断事实的推断，请结合业务窗口确认。</p></div></header><pre>{report.ai.content || JSON.stringify(report.ai.raw, null, 2)}</pre></div> : <Empty title="本次未启用 AI" text="确定性结论和原始输出仍是完整报告。"/>}</div>}
  </section>
}

export function ToolLibrary({ scan, selectedIndex, setSelectedIndex, onLocalScript, onServerScript, onImportPlugin, busy }: { scan: PluginScanResponse; selectedIndex: number; setSelectedIndex: (value: number) => void; onLocalScript: () => void; onServerScript: () => void; onImportPlugin: () => void; busy: string }) {
  const item = scan.items[selectedIndex] || scan.items[0]
  const closeMenu = (event: React.MouseEvent<HTMLButtonElement>, action: () => void) => { event.currentTarget.closest('details')?.removeAttribute('open'); action() }
  return <section className="page active"><div className="page-content library-page"><header className="page-heading"><div><span className="eyebrow">可扩展诊断能力</span><h1>诊断工具库</h1><p>统一管理本机脚本、服务器已有脚本和完整插件包。</p></div><details className="tool-add-menu"><summary className="button primary">＋ 新增工具</summary><div className="tool-add-popover"><button onClick={(event) => closeMenu(event, onLocalScript)}><span>⌘</span><b>本机脚本</b><small>选择 Mac 上的 Bash 或 Python 文件</small></button><button onClick={(event) => closeMenu(event, onServerScript)}><span>⌁</span><b>服务器脚本</b><small>登记目标服务器上已有的绝对路径</small></button><button disabled={busy === 'import'} onClick={(event) => closeMenu(event, onImportPlugin)}><span>⬡</span><b>插件</b><small>导入包含 plugin.yaml 的完整目录</small></button></div></details></header><div className="library-layout"><div className="library-list"><p>诊断工具 · {scan.items.length}</p>{scan.items.map((entry, index) => <button key={entry.directory} className={`library-item ${index === selectedIndex ? 'active' : ''} ${entry.valid ? '' : 'invalid'}`} onClick={() => setSelectedIndex(index)}><span className={`plugin-mark ${entry.valid ? '' : 'error'}`}>{entry.valid ? pluginLetter(entry.plugin) : '!'}</span><span><b>{entry.plugin?.name || entry.directory.split('/').at(-1)}</b><small>{entry.plugin ? `${entry.plugin.id} · ${entry.plugin.version}` : '工具校验失败'}</small></span><em className={`tool-type ${entry.plugin?.toolType || 'invalid'}`}>{entry.valid ? toolTypeLabel(entry.plugin) : '失败'}</em></button>)}</div><div className="library-detail">{item ? <ToolDetail item={item}/> : <Empty title="诊断工具库为空" text="点击“新增工具”，添加本机脚本、服务器脚本或插件。"/>}</div></div></div></section>
}

function ToolDetail({ item }: { item: PluginScanItem }) {
  if (!item.valid || !item.plugin) return <><span className="eyebrow">校验失败</span><h2>{item.directory.split('/').at(-1)}</h2><div className="validation-error"><b>该目录不会出现在诊断工具选择中</b>{item.errors.map((error) => <p key={error}>{error}</p>)}</div><dl className="plugin-facts"><div><dt>目录</dt><dd>{item.directory}</dd></div></dl></>
  const plugin = item.plugin
  return <><span className="eyebrow">工具详情</span><h2>{plugin.name}</h2><p>{plugin.description || '该工具未填写说明。'}</p><div className={`trust-banner ${plugin.trust.status}`}><b>{plugin.trust.status === 'trusted' ? '✓ 插件数字签名有效' : plugin.trust.status === 'local' ? `✓ ${toolTypeLabel(plugin)}由本机管理` : '⚠ 开发者模式插件'}</b><span>{plugin.trust.message}</span></div><dl className="plugin-facts"><div><dt>工具类型</dt><dd>{toolTypeLabel(plugin)}</dd></div><div><dt>工具 ID</dt><dd>{plugin.id}</dd></div><div><dt>版本</dt><dd>{plugin.version}</dd></div><div><dt>入口</dt><dd>{plugin.entrypoint}</dd></div><div><dt>运行环境</dt><dd>{plugin.language}</dd></div><div><dt>发布者</dt><dd>{plugin.toolType === 'plugin' ? plugin.trust.publisherId || '未签名' : '当前 Mac'}</dd></div><div><dt>配置字段</dt><dd>{plugin.fields.length} 个</dd></div><div><dt>报告模板</dt><dd>{plugin.report ? 'Schema + HTML' : '使用应用默认模板'}</dd></div><div><dt>目录</dt><dd>{plugin.directory}</dd></div></dl><h3 className="detail-heading">运行模式</h3><div className="mode-list">{plugin.modes.map((mode) => <div key={mode.id}><b>{mode.label}</b><small>{mode.help}</small></div>)}</div></>
}

function ReportsPage({ reports, openReport }: { reports: DiagnosticReport[]; openReport: (report: DiagnosticReport) => void }) {
  const [search, setSearch] = useState('')
  const filtered = reports.filter((report) => `${report.server.name}${report.plugin.name}${report.summary}`.toLowerCase().includes(search.toLowerCase()))
  return <section className="page active"><div className="page-content"><header className="page-heading"><div><span className="eyebrow">诊断记录</span><h1>历史报告</h1><p>所有结果保存在本机，可查看结论、原始输出和 AI 分析。</p></div><label className="search"><span>⌕</span><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索报告"/></label></header>{filtered.length ? <div className="report-list panel">{filtered.map((report) => { const count = report.findings.filter((item) => ['warning','critical'].includes(item.severity)).length; return <button key={report.id} className="report-row" onClick={() => openReport(report)}><span className={`result-symbol ${count ? 'warning' : 'success'} small`}>{count ? '!' : '✓'}</span><span><b>{report.plugin.name}</b><small>{report.server.name} · {formatDate(report.createdAt)}</small></span><strong>{count ? `${count} 项需关注` : '全部正常'}</strong><em>{report.durationSeconds.toFixed(1)} 秒</em><i>查看 →</i></button> })}</div> : <Empty title="暂无匹配报告" text="运行一次诊断后，报告会自动出现在这里。"/>}</div></section>
}

function SettingsPage({ settings, setSettings, scan, cacheBytes, cacheRoot, save, busy, rescan, testAI, clearCache, showToast }: { settings: ApplicationSettings; setSettings: (value: ApplicationSettings) => void; scan: PluginScanResponse; cacheBytes: number; cacheRoot: string; save: () => void; busy: string; rescan: () => Promise<void>; testAI: (key: string) => Promise<{ ok: boolean; message: string; rawResponse?: string }>; clearCache: () => Promise<void>; showToast: (message: string) => void }) {
  const [key, setKey] = useState('')
  const [aiTestResult, setAiTestResult] = useState('')
  return <section className="page active"><div className="page-content settings-page"><header className="page-heading"><div><span className="eyebrow">应用设置</span><h1>设置</h1><p>管理插件来源、AI 接口和可重新生成的本机缓存。</p></div><button className="button primary" disabled={busy === 'settings'} onClick={save}>{busy === 'settings' ? '保存中…' : '保存设置'}</button></header>
    <section className="panel setting-card"><header><span>⬡</span><div><h3>诊断工具目录</h3><p>本机脚本、服务器脚本配置和手动放入的插件统一从这里读取。</p></div></header><label className="path-input"><b>目录</b><input value={settings.pluginDirectory} onChange={(e) => setSettings({ ...settings, pluginDirectory: e.target.value })}/><button onClick={() => navigator.clipboard.writeText(settings.pluginDirectory).then(() => showToast('工具目录已复制'))}>复制</button></label><div className="setting-switches"><label className="switch-row"><span><b>开发者模式</b><small>允许本机调试未签名插件；公开使用时建议关闭</small></span><input type="checkbox" checked={settings.developerMode} onChange={(e) => setSettings({ ...settings, developerMode: e.target.checked })}/><i/></label><label className="switch-row"><span><b>演示模式</b><small>保留不连接真实服务器的演示工作流</small></span><input type="checkbox" checked={settings.demoMode} onChange={(e) => setSettings({ ...settings, demoMode: e.target.checked })}/><i/></label></div><footer><button className="button quiet" onClick={() => api.openPluginDirectory().then(() => showToast('已打开诊断工具目录')).catch((error: Error) => showToast(error.message))}>打开工具目录</button><button className="button quiet" onClick={async () => { await rescan(); showToast('诊断工具库扫描完成') }}>重新扫描工具库</button><span className={scan.invalidCount ? 'inline-warning' : 'inline-success'}>{scan.validCount} 个有效，{scan.invalidCount} 个失败</span></footer></section>
    <section className="panel setting-card"><header><span>AI</span><div><h3>AI 增强分析</h3><p>兼容 OpenAI Chat Completions / Responses 接口，DeepSeek 可直接使用。</p></div></header><div className="settings-form"><label><span>接口地址</span><input value={settings.ai.endpoint} onChange={(e) => setSettings({ ...settings, ai: { ...settings.ai, endpoint: e.target.value } })}/></label><label><span>模型</span><input value={settings.ai.model} onChange={(e) => setSettings({ ...settings, ai: { ...settings.ai, model: e.target.value } })}/></label><label><span>API Key</span><input type="password" value={key} onChange={(e) => { setKey(e.target.value); setSettings({ ...settings, aiApiKey: e.target.value }) }} placeholder={settings.ai.configured ? '已安全保存；留空不修改' : 'sk-…'}/></label></div>{aiTestResult && <details className="ai-raw-response" open><summary>AI 原始响应</summary><pre>{aiTestResult}</pre></details>}<footer><button className="button quiet" disabled={busy === 'ai-test'} onClick={() => { setAiTestResult('等待模型回复…'); testAI(key).then((result) => setAiTestResult(result.rawResponse || JSON.stringify(result, null, 2))).catch((error: Error) => setAiTestResult(error.message)) }}>{busy === 'ai-test' ? '测试中…' : '保存前测试连接'}</button><span className={settings.ai.configured ? 'inline-success' : 'inline-warning'}>{settings.ai.configured ? '已配置' : '尚未配置'}</span></footer></section>
    <section className="panel setting-card"><header><span>↺</span><div><h3>本机缓存</h3><p>诊断结果、AI JSON 和生成的 HTML 报告；服务器与插件配置不会清除。</p></div></header><div className="cache-row"><span><small>当前占用</small><b>{formatBytes(cacheBytes)}</b><em>{cacheRoot}</em></span><button className="button danger" onClick={clearCache}>清空缓存</button></div></section>
  </div></section>
}

export function ServerModal({ draft, setDraft, saving, connectionResult, close, save, test }: { draft: ServerProfile; setDraft: (value: ServerProfile) => void; saving: boolean; connectionResult: string; close: () => void; save: () => void; test: () => void }) {
  const update = (key: keyof ServerProfile, value: string | number) => setDraft({ ...draft, [key]: value })
  return <div className="modal open" role="dialog" aria-modal="true"><div className="modal-card"><header><div><span className="eyebrow">服务器</span><h2>{draft.name ? '编辑服务器' : '新建服务器'}</h2></div><button className="icon-button" onClick={close}>×</button></header><form onSubmit={(e) => { e.preventDefault(); save() }}><label><span>服务器名称</span><input value={draft.name} onChange={(e) => update('name', e.target.value)} placeholder="例如：Doris 生产机" required/></label><fieldset><legend>登录方式</legend><div className="segmented">{([['alias','SSH 别名'],['key','密钥'],['password','密码']] as const).map(([value, label]) => <label key={value}><input type="radio" name="auth" checked={draft.authentication === value} onChange={() => update('authentication', value)}/><span>{label}</span></label>)}</div></fieldset>{draft.authentication === 'alias' ? <label><span>SSH 别名</span><input value={draft.alias} onChange={(e) => update('alias', e.target.value)} placeholder="例如：doris" required/></label> : <><div className="modal-grid"><label><span>服务器域名或 IP</span><input value={draft.host} onChange={(e) => update('host', e.target.value)} required/></label><label><span>端口</span><input type="number" value={draft.port} onChange={(e) => update('port', Number(e.target.value))}/></label></div><label><span>用户名</span><input value={draft.user} onChange={(e) => update('user', e.target.value)} required/></label>{draft.authentication === 'key' ? <label><span>私钥路径</span><input value={draft.identityFile} onChange={(e) => update('identityFile', e.target.value)} placeholder="~/.ssh/id_ed25519"/></label> : <label><span>密码</span><input type="password" value={draft.password || ''} onChange={(e) => update('password', e.target.value)} placeholder="保存在系统钥匙串"/></label>}</>}<p className={`connection-result ${connectionResult.includes('成功') ? 'success-text' : ''}`}>{connectionResult}</p></form><footer><button className="button quiet" onClick={() => test()}>测试连接</button><span/><button className="button quiet" onClick={close}>取消</button><button className="button primary" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存'}</button></footer></div></div>
}

export function ScriptToolModal({ draft, setDraft, file, setFile, saving, close, save }: { draft: ScriptToolDraft; setDraft: (value: ScriptToolDraft) => void; file: File | null; setFile: (value: File | null) => void; saving: boolean; close: () => void; save: () => void }) {
  const update = <K extends keyof ScriptToolDraft>(key: K, value: ScriptToolDraft[K]) => setDraft({ ...draft, [key]: value })
  const local = draft.kind === 'local_script'
  return <div className="modal open" role="dialog" aria-modal="true"><div className="modal-card"><header><div><span className="eyebrow">新增诊断工具</span><h2>{local ? '本机脚本' : '服务器脚本'}</h2></div><button className="icon-button" aria-label="关闭" onClick={close}>×</button></header><form onSubmit={(event) => { event.preventDefault(); save() }}><label><span>工具名称</span><input value={draft.name} onChange={(event) => update('name', event.target.value)} placeholder={local ? '例如：自定义网络采样' : '例如：线上巡检脚本'} required/></label><label><span>说明</span><input value={draft.description} onChange={(event) => update('description', event.target.value)} placeholder="这个工具检查什么，可选"/></label><fieldset><legend>运行环境</legend><div className="segmented runtime-segmented">{([['bash','Bash'],['python','Python 3']] as const).map(([value, label]) => <label key={value}><input type="radio" name="runtime" checked={draft.runtime === value} onChange={() => update('runtime', value)}/><span>{label}</span></label>)}</div></fieldset>{local ? <label><span>脚本文件</span><input className="file-input" type="file" accept=".sh,.bash,.py,text/x-shellscript,text/x-python" onChange={(event) => { const selected = event.target.files?.[0] || null; setFile(selected); if (selected) setDraft({ ...draft, name: draft.name || selected.name.replace(/\.[^.]+$/, ''), runtime: selected.name.toLowerCase().endsWith('.py') ? 'python' : 'bash' }) }}/><small>{file ? `已选择：${file.name}` : '支持单个 .sh 或 .py 文件，最多 10 MB'}</small></label> : <label><span>服务器脚本绝对路径</span><input value={draft.scriptPath} onChange={(event) => update('scriptPath', event.target.value)} placeholder={draft.runtime === 'python' ? '/opt/diagnostics/check.py' : '/opt/diagnostics/check.sh'} required/><small>保存后仍可在每台服务器的检查配置中覆盖。</small></label>}<div className="tool-scope-note"><b>{local ? '运行方式' : '不会上传脚本'}</b><span>{local ? 'Sentinel 会把脚本版本化缓存到目标服务器，再收集输出生成报告。' : 'Sentinel 只上传轻量执行包装器，并调用服务器上已有路径。'}</span></div></form><footer><button className="button quiet" onClick={close}>取消</button><button className="button primary" disabled={saving} onClick={save}>{saving ? '正在新增…' : '新增工具'}</button></footer></div></div>
}

function Empty({ title, text }: { title: string; text: string }) { return <div className="empty-state"><span>◇</span><h3>{title}</h3><p>{text}</p></div> }

export default App
