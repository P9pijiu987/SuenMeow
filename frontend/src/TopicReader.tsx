import { useEffect, useRef, useState } from 'react'
import { BookOpen, Download, FileText, MessageSquare, RefreshCw, Square } from 'lucide-react'
import { api, type Data } from './api'

const active = (job?: Data) => !!job && ['queued', 'running'].includes(job.state)
const status: Data = { queued: '等待读取', running: '正在处理', completed: '已完成', failed: '未完成', cancelled: '已停止', interrupted: '已中断' }
const phase: Data = { reading: '读取全文', analysing: '研究各段内容', merging: '汇总与点评', done: '已完成' }
const number = (n: number) => (n || 0).toLocaleString('zh-CN')

export function TopicReader({ user, act }: { user: Data, act: (fn: () => Promise<any>, message?: string) => Promise<boolean> }) {
  const [topic, setTopic] = useState(''), [focus, setFocus] = useState(''), [limit, setLimit] = useState(200000)
  const [jobs, setJobs] = useState<Data[]>([]), [selected, setSelected] = useState(''), [detail, setDetail] = useState<Data | null>(null)
  const [error, setError] = useState(''), [resultError, setResultError] = useState(''), [busy, setBusy] = useState(false)
  const autoDownload = useRef(''), alive = useRef(true)
  const job = jobs.find(row => row.id === selected), allowed = user.role === 'admin' || user.forum_user_id
  const reload = async () => {
    try { const rows = await api<Data[]>('/topic-reviews'); if (alive.current) { setJobs(rows); setError('') } }
    catch (e) { if (alive.current) setError((e as Error).message) }
  }
  useEffect(() => { alive.current = true; void reload(); const timer = setInterval(reload, 5000); return () => { alive.current = false; clearInterval(timer) } }, [])
  useEffect(() => {
    let current = true
    setDetail(null); setResultError('')
    if (selected && job?.state === 'completed' && job.config.mode === 'review') {
      api<Data>('/topic-reviews/' + selected).then(row => { if (current) setDetail(row) }).catch(e => { if (current) setResultError(e.message) })
    }
    return () => { current = false }
  }, [selected, job?.state])
  const download = async (row: Data, format: 'markdown' | 'json') => {
    const response = await fetch(`/api/topic-reviews/${row.id}/export?format=${format}`, { credentials: 'same-origin' })
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || '导出失败，请稍后重试') }
    const text = await response.text()
    const complete = format === 'json' ? JSON.parse(text).complete === true : text.endsWith(`导出结束，共 ${row.config.posts} 条普通发言。\n`)
    if (!complete) throw new Error('下载中断或主题权限发生变化，未生成完整文件；请重新读取')
    const url = URL.createObjectURL(new Blob([text], { type: format === 'json' ? 'application/json' : 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = `topic-${row.topic_id}.${format === 'json' ? 'json' : 'md'}`
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  useEffect(() => {
    const row = jobs.find(row => row.id === autoDownload.current)
    if (row?.state === 'completed') { autoDownload.current = ''; void act(() => download(row, 'markdown'), '整帖 Markdown 已导出') }
    else if (row && !active(row)) autoDownload.current = ''
  }, [jobs])
  const start = async (mode: 'export' | 'review') => {
    if (!topic.trim() || busy) return
    setBusy(true)
    try { await act(async () => {
      const row = await api<Data>('/topic-reviews', 'POST', { topic: topic.trim(), mode, focus: mode === 'review' ? focus : '', max_tokens: limit })
      setSelected(row.id); setJobs(rows => [row, ...rows]); if (mode === 'export') autoDownload.current = row.id
    }, mode === 'export' ? '开始读取，完成后自动下载' : '已开始全文总结与点评') } finally { setBusy(false) }
  }
  const result = detail?.result
  return <div className="topic-reader">
    <section className="panel reader-intro"><div className="reader-heading"><BookOpen size={28}/><div><h2>让猫读完整个帖子</h2><p>把一场长谈带走，或听听 SuenMeow 的总结与看法。</p></div></div>
      <form onSubmit={e => { e.preventDefault(); void start('review') }}>
        <label className="field"><span>公开主题网址或 ID</span><input required maxLength={1000} value={topic} onChange={e => setTopic(e.target.value)} placeholder="https://forum.rdfzer.com/t/11957" disabled={busy}/></label>
        <label className="field"><span>想让猫重点关注什么？（可选）</span><textarea rows={2} maxLength={1000} value={focus} onChange={e => setFocus(e.target.value)} placeholder="例如：梳理观点变化，也聊聊这串讨论最有意思的地方。"/></label>
        <details className="reader-options"><summary>处理额度与范围</summary><label className="field"><span>本次模型用量上限（token）</span><input type="number" min={1000} max={2000000} step={1000} value={limit} onChange={e => setLimit(Number(e.target.value))}/><small>这是上限，不是预收费。实际用量计入全站每日预算。长帖完整分段研究，额度不足会停止并保留进度。</small></label><p>读取所有参与者的可见普通发言，不按时间截断。附件保留链接，不下载文件；隐藏、删除、系统楼层与登录验证资料不导出。保存七天，最多保留 30 个任务。</p></details>
        {!allowed && <p className="warning">请先通过论坛私信登录，核验本人身份后使用。</p>}
        <div className="reader-actions"><button type="button" disabled={!allowed || busy || !topic.trim() || jobs.some(active)} onClick={() => void start('export')}><Download size={17}/>一键导出全文<small>不调用模型</small></button><button type="submit" className="primary" disabled={!allowed || busy || !topic.trim() || jobs.some(active)}><MessageSquare size={17}/>总结并点评</button></div>
      </form><p className="muted reader-boundary">结果在这里展示，不会自动回帖，也不会写入记忆书架。</p>
    </section>
    <div className="section-head"><h3>我的整帖阅读</h3><button onClick={() => void act(reload)}><RefreshCw size={15}/>刷新</button></div>
    {error && <p role="alert" className="warning">{error} · 连接恢复后继续检查进度。</p>}
    {!jobs.length ? <div className="empty"><FileText size={28}/><h3>还没有读过整帖</h3><p>粘贴一个公开主题，全文导出和总结都会保存在这里。</p></div> : <div className="reader-layout"><div className="reader-history">{jobs.map(row => <button key={row.id} className={'panel reader-history-item ' + (selected === row.id ? 'selected' : '')} onClick={() => setSelected(row.id)}><strong>{row.config.title || `主题 #${row.topic_id}`}</strong><small>{row.config.mode === 'export' ? '全文导出' : '总结与点评'} · {status[row.state] || row.state}</small><small>{new Date(row.created * 1000).toLocaleString('zh-CN')}</small></button>)}</div>
      {job ? <section className="panel reader-result"><div className="section-head"><div><span className={'badge ' + (job.state === 'completed' ? 'green' : '')}>{status[job.state]}</span><h3>{job.config.title}</h3><a href={`${job.config.site}/t/${job.topic_id}`} target="_blank" rel="noreferrer">查看原帖 ↗</a></div>{active(job) && <button onClick={() => void act(() => api(`/topic-reviews/${job.id}/cancel`, 'POST').then(reload), '已停止任务')}><Square size={14}/>停止</button>}</div>
        <div className="reader-metrics"><span>{phase[job.config.phase]}<small>读取 {number(job.config.offset)} / {number(job.config.total)} 楼</small></span><span>{number(job.config.posts)} 条发言<small>{number(job.config.parts_done)} 段已整理</small></span><span>{number(job.tokens)} token<small>{number(job.calls)} 次模型调用</small></span></div>
        {active(job) && <div className="progress"><i style={{ width: Math.max(2, job.config.offset / Math.max(1, job.config.total) * 100) + '%' }}/></div>}
        {job.reason && <p role="alert" className="warning">{job.reason}</p>}
        {job.config.excluded > 0 && <p className="muted">{number(job.config.excluded)} 个隐藏、删除或系统楼层等未返回内容没有纳入。</p>}
        {job.config.plain_text_posts > 0 && <p className="muted">{number(job.config.plain_text_posts)} 条仅返回纯文本，可能缺少原始格式和附件引用。</p>}
        {['failed', 'cancelled', 'interrupted'].includes(job.state) && <div className="reader-resume"><p>继续会使用剩余额度调用模型。连接或人格编排变化时，请新建任务。</p><button onClick={() => void act(() => api(`/topic-reviews/${job.id}/resume`, 'POST').then(reload), '已继续任务')}>手动继续</button></div>}
        {job.config.read_complete && <div className="reader-downloads"><button onClick={() => void act(() => download(job, 'markdown'), 'Markdown 已导出')}><Download size={16}/>下载全文 Markdown</button><button onClick={() => void act(() => download(job, 'json'), 'JSON 已导出')}>下载 JSON</button></div>}
        {resultError && <p className="warning" role="alert">{resultError}<button onClick={() => void act(async () => { setDetail(await api('/topic-reviews/' + selected)); setResultError('') })}>重新核验并查看</button></p>}
        {result && <><div className="reader-prose"><h3>整帖总结</h3><p>{result.summary}</p></div><div className="reader-prose reader-commentary"><h3>SuenMeow 的点评</h3><p>{result.commentary}</p></div><div className="reader-sources"><strong>参考楼层</strong>{result.sources.map((source: Data) => <a key={source.post_id} href={source.url} target="_blank" rel="noreferrer">#{source.number} · @{source.username}</a>)}</div></>}
        {job.state === 'completed' && job.config.mode === 'review' && !result && !resultError && <p className="muted">正在复核主题公开性并读取结果…</p>}
      </section> : <section className="panel empty"><p>选择一条阅读记录。</p></section>}
    </div>}
  </div>
}
