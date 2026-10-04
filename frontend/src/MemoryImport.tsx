import { useEffect, useState } from 'react'
import { BookOpen, Check, RefreshCw } from 'lucide-react'
import { api, type Data } from './api'

export function MemoryImport({ onSaved, admin }: { onSaved: () => void, admin: boolean }) {
  const [topic, setTopic] = useState(''), [budget, setBudget] = useState(12000)
  const [jobs, setJobs] = useState<Data[]>([]), [error, setError] = useState('')
  const [busy, setBusy] = useState(false), [selected, setSelected] = useState<number[]>([])
  const [detected, setDetected] = useState<Data | null>(null)
  const job = jobs[0], active = job && ['preparing', 'preview', 'queued', 'running', 'awaiting_save'].includes(job.state)
  const reload = async () => { try { setJobs(await api('/memory-imports')) } catch (e) { setError((e as Error).message) } }
  useEffect(() => { void reload(); const timer = setInterval(reload, 3000); return () => clearInterval(timer) }, [])
  useEffect(() => { let stopped = false; void api('/memory-imports/detect').then(result => { if (!stopped) setDetected(result) }).catch(() => { if (!stopped) setDetected({ candidates: [], message: '自动查找暂时不可用，请粘贴个人贴链接' }) }); return () => { stopped = true } }, [])
  useEffect(() => { setSelected((job?.result?.facts || []).map((_: Data, i: number) => i)) }, [job?.id, job?.state])
  const run = async (path: string, body?: Data) => {
    setBusy(true); setError('')
    try { await api(path, 'POST', body); await reload(); if (path.endsWith('/save')) onSaved() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const labels: Data = { preparing: '正在读取', preview: '等待确认作者', queued: '等待提取', running: '提取中', awaiting_save: '等待选择事实', saved: '已保存', empty: '没有新事实', failed: '未完成', cancelled: '已取消', interrupted: '重启后停止', expired: '已过期' }
  return <section className="panel memory-import">
    <div className="section-head"><div><h3><BookOpen size={18}/> {admin ? '从个人贴建立记忆' : '让猫从你的个人贴认识你'}</h3><p className="muted">{admin ? '只读帖主自己的公开发言。先核对作者与预算，再提取、选择保存。' : '仅限你本人创建的公开主题。先预览，再选择猫可以记住的事实。'}</p></div><button className="icon-button" aria-label="刷新导入进度" onClick={reload}><RefreshCw size={16}/></button></div>
    {!admin && <p className="muted">每次最多 12,000 token；每日最多提取 3 批、20,000 token，仍受全站预算限制。查找和预览不调用模型。</p>}
    {!active && <div className="import-discovery"><p className="muted">{detected?.message || '正在查找你创建的公开主题…'}</p>{detected?.candidates?.map((candidate: Data) => <button key={candidate.topic_id} disabled={busy} onClick={() => { setTopic(String(candidate.topic_id)); void run('/memory-imports', { topic_id: candidate.topic_id, max_tokens: budget }) }}>{candidate.title || `主题 #${candidate.topic_id}`} · 读取预览</button>)}</div>}
    {!active && <form onSubmit={e => { e.preventDefault(); const source = topic.trim(); void run('/memory-imports', { ...(/^\d+$/.test(source) ? { topic_id: Number(source) } : { topic_url: source }), max_tokens: budget }) }}>
      <div className="field-grid"><label className="field">个人贴链接或 ID<input required value={topic} onChange={e => setTopic(e.target.value)} placeholder="粘贴论坛链接，或输入 11957"/></label><label className="field">单次 token 预算<input type="number" min="3000" max={admin ? 30000 : 12000} required value={budget} onChange={e => setBudget(Number(e.target.value))}/></label></div>
      <button className="primary" disabled={busy || !topic}>读取预览（不调用模型）</button>
    </form>}
    {job && <div className="import-progress">
      <div className="list-row"><strong>{labels[job.state] || job.state}</strong><small>本批模型用量 {job.tokens.toLocaleString()} token</small></div>
      {job.config?.username && <><p><strong>@{job.config.username}</strong> · 用户 ID {job.config.user_id}<br/><a href={job.config.url} target="_blank" rel="noreferrer">{job.config.title || `个人贴 #${job.topic_id}`}</a></p>
        <p className="muted">本批扫描 {job.config.scanned} 楼，其中作者原帖 {job.config.author_posts} 条；剩余 {job.config.remaining} 楼。{job.config.truncated > 0 && ` ${job.config.truncated} 条长帖只读取了开头，未覆盖全文。`}</p></>}
      {job.state === 'preview' && <><p className="info-note">确认这是此人的个人贴。最多一次模型调用，保守预留 {job.config.reservation.toLocaleString()} token（含输出）；实际按提供方用量结算。不会给论坛发消息。</p><div className="form-actions"><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>取消</button><button className="primary" disabled={busy} onClick={() => run(`/memory-imports/${job.id}/extract`)}>确认作者并提取</button></div></>}
      {job.state === 'awaiting_save' && <><div className="import-facts">{job.result.facts.map((fact: Data, index: number) => <label className="import-fact" key={index}><input type="checkbox" checked={selected.includes(index)} onChange={e => setSelected(e.target.checked ? [...selected, index] : selected.filter(i => i !== index))}/><span><strong>{fact.text}</strong><blockquote>{fact.quote}</blockquote><small>来源帖子 #{fact.source_post_id}</small></span></label>)}</div><p className="muted">这些是模型候选，请排除玩笑、过时和有矛盾的内容。未选择的事实不会保存。</p><div className="form-actions"><button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>丢弃此批候选</button><button className="primary" disabled={busy} onClick={() => run(`/memory-imports/${job.id}/save`, { digest: job.result.digest, selected })}><Check size={16}/>保存 {selected.length} 条</button></div></>}
      {['preparing', 'queued', 'running'].includes(job.state) && <p className="muted">正在处理这一批，失败不会自动重跑。<button disabled={busy} onClick={() => run(`/memory-imports/${job.id}/cancel`)}>停止</button></p>}
      {job.reason && <p className="muted">{job.reason}</p>}
      {!active && job.config?.remaining > 0 && <button disabled={busy} onClick={() => { setTopic(String(job.topic_id)); void run('/memory-imports', { topic_id: job.topic_id, max_tokens: budget }) }}>继续读取下一批</button>}
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>
}
