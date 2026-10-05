import { useEffect, useRef, useState } from 'react'
import { BookOpen, RefreshCw } from 'lucide-react'
import { api, type Data } from './api'
import { SaveDock, useDraftSafety } from './DraftSafety'

export function MemoryImport({ onSaved, admin }: { onSaved: () => void, admin: boolean }) {
  const [topic, setTopic] = useState(''), [jobs, setJobs] = useState<Data[]>([])
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const [detected, setDetected] = useState<Data | null>(null), [category, setCategory] = useState(22)
  const [savedCategory, setSavedCategory] = useState(22), [settingsError, setSettingsError] = useState('')
  const [settingsBusy, setSettingsBusy] = useState(false)
  const savedId = useRef(''), job = jobs[0]
  const active = job && ['preparing', 'preview', 'queued', 'running', 'awaiting_save'].includes(job.state)
  const discover = async () => { setDetected(null); try { setDetected(await api('/memory-imports/detect')) } catch { setDetected({ candidates: [], message: '自动查找暂时不可用，请粘贴个人贴链接' }) } }
  const reload = async () => { try { setJobs(await api('/memory-imports')) } catch (e) { setError((e as Error).message) } }
  useEffect(() => { void reload(); void discover(); void api('/memory-imports/settings').then(result => { setCategory(result.category_id); setSavedCategory(result.category_id) }).catch(() => {}); const timer = setInterval(reload, 3000); return () => clearInterval(timer) }, [])
  useEffect(() => { if (job?.state === 'saved' && savedId.current !== job.id) { savedId.current = job.id; onSaved() } }, [job?.id, job?.state])
  const run = async (path: string, body?: Data) => {
    setBusy(true); setError('')
    try { await api(path, 'POST', body); await reload() }
    catch (e) { setError((e as Error).message); await reload() }
    finally { setBusy(false) }
  }
  const start = (source: string) => run('/memory-imports/start', { ...(/^\d+$/.test(source) ? { topic_id: Number(source) } : { topic_url: source }) })
  const dirty = admin && category !== savedCategory
  const saveSettings = async () => {
    if (!dirty) return true
    if (settingsBusy || !Number.isInteger(category) || category < 1 || category > 2147483647) return false
    setSettingsBusy(true); setSettingsError('')
    try { await api('/memory-imports/settings', 'PUT', { category_id: category }); setSavedCategory(category); await discover(); return true }
    catch (e) { setSettingsError((e as Error).message); return false }
    finally { setSettingsBusy(false) }
  }
  useDraftSafety(dirty, saveSettings, settingsBusy)
  const labels: Data = { preparing: '正在读取近期发言', preview: '上次导入尚未确认', queued: '等待整理', running: '正在整理并保存', awaiting_save: '上次候选等待保存', saved: '记忆已放入书架', empty: '没有新增记忆', failed: '导入未完成', cancelled: '已停止', interrupted: '重启后停止', expired: '已过期' }
  return <section className="panel memory-import">
    <div className="section-head"><div><h3><BookOpen size={18}/> {admin ? '从个人贴导入记忆' : '让猫从你的个人贴认识你'}</h3><p className="muted">选择个人贴，一键整理近期记忆。完成后可在下面查看、删除。</p></div><button className="icon-button" aria-label="刷新个人贴和导入进度" onClick={() => { void reload(); void discover() }}><RefreshCw size={16}/></button></div>
    <p className="muted">仅限「個人帖」分类（#{savedCategory}）的公开主题。优先最新发言，跳过旧内容；一次最多 12,000 token{!admin && '，每日最多导入 3 次、20,000 token'}，仍受全站预算限制。查找不调用模型。</p>
    {!active && <><div className="import-discovery"><p className="muted">{detected?.message || '正在查找你的个人贴…'}</p>{detected?.candidates?.map((candidate: Data) => <button key={candidate.topic_id} disabled={busy} onClick={() => { setTopic(String(candidate.topic_id)); void start(String(candidate.topic_id)) }}>{candidate.title || `主题 #${candidate.topic_id}`} · 一键导入</button>)}</div>
      <details className="import-link" open={!detected?.candidates?.length}><summary>没有找到？使用个人贴链接</summary><form onSubmit={e => { e.preventDefault(); void start(topic.trim()) }}><label className="field">没有找到？粘贴个人贴链接<input required value={topic} onChange={e => setTopic(e.target.value)} placeholder="个人贴网址或主题 ID"/></label><button className="primary" disabled={busy || !topic.trim()}>一键导入近期记忆</button></form></details></>}
    {job && <div className="import-progress"><div className="list-row"><strong>{labels[job.state] || job.state}</strong><small>本次已用 {job.tokens.toLocaleString()} token</small></div>
      {job.config?.username && <><p><strong>@{job.config.username}</strong> · <a href={job.config.url} target="_blank" rel="noreferrer">{job.config.title || `个人贴 #${job.topic_id}`}</a></p><p className="muted">检查 {job.config.scanned} 楼，整理本人发言 {job.config.author_posts} 条。{job.config.recent && ` ${job.config.older_omitted} 条较早内容未纳入此次整理。`}{job.config.truncated > 0 && ` ${job.config.truncated} 条长发言读取了预算内开头。`}</p></>}
      {['preparing', 'queued', 'running'].includes(job.state) && <p className="muted">完成后自动保存。AI 提取可能不准确，请在书架核对。<button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>停止</button></p>}
      {job.state === 'preview' && <div className="form-actions"><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>放弃上次导入</button><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/extract`)}>完成上次提取</button></div>}
      {job.state === 'awaiting_save' && <div className="form-actions"><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>丢弃上次候选</button><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/save`, { digest: job.result.digest, selected: job.result.facts.map((_: Data, i: number) => i) })}>保存上次候选到书架</button></div>}
      {job.reason && <p className="muted">{job.reason}</p>}
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
    {admin && <><details className="import-settings"><summary>个人贴来源设置</summary><label className="field">论坛分类 ID<input type="number" min="1" max="2147483647" value={category} onChange={e => setCategory(Number(e.target.value))}/></label></details><SaveDock dirty={dirty} busy={settingsBusy} error={settingsError} save={saveSettings} label="保存来源分类" idleLabel="来源分类已保存" detail="保存后立即用于查找与新导入"/></>}
  </section>
}
