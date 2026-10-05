import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Cat, Plus, RefreshCw, X } from 'lucide-react'
import { api, type Data } from './api'
import { SaveDock, useDraftSafety } from './DraftSafety'
import { ReviewSettings } from './ReviewSettings'

const routes: Record<string, string> = { planner: '参与规划', replyer: '回复生成', memory: '记忆整理', summary: '主题摘要', agent: '主动研究' }
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export function TopicPipelines({ user }: { user: Data }) {
  const admin = user.role === 'admin'
  const [rows, setRows] = useState<Data[]>([]), [workspace, setWorkspace] = useState<Data | null>(null)
  const [base, setBase] = useState<Data | null>(null), [draft, setDraft] = useState<Data | null>(null)
  const [candidates, setCandidates] = useState<Data[]>([]), [route, setRoute] = useState('replyer')
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [showPublished, setShowPublished] = useState(false)
  const [review, setReview] = useState<boolean | null>(null)
  const choose = (row: Data) => { setBase(structuredClone(row)); setDraft(structuredClone(row)); setShowPublished(false); setError('') }
  const dirty = !!draft && (!base || !same([draft.title, draft.topic_id, draft.personas], [base.title, base.topic_id, base.personas]))
  const load = async () => {
    setBusy(true)
    try { const [list, shelf] = await Promise.all([api('/topic-pipelines'), api('/prompts/workspace')]); setRows(list); setWorkspace(shelf); if (draft?.id) { const fresh = list.find((r: Data) => r.id === draft.id); if (fresh) choose(fresh) } else if (!draft && list.length) choose(list[0]); setError('') }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  useEffect(() => { void load(); void api('/memory-imports/detect').then(r => setCandidates(r.candidates || [])).catch(() => {}) }, [])
  const save = async () => {
    if (!draft || busy || !dirty) return !busy
    if (!draft.title.trim() || !Number.isInteger(draft.topic_id) || draft.topic_id < 1) { setError('请填写编排名称和本人个人贴的主题 ID'); return false }
    setBusy(true); setError('')
    try {
      const value = await api('/topic-pipelines' + (draft.id ? '/' + draft.id : ''), draft.id ? 'PUT' : 'POST',
        { title: draft.title, topic_id: draft.topic_id, personas: draft.personas, version: draft.version || 1 })
      setRows([value, ...rows.filter(r => r.id !== value.id)]); choose(value); return true
    } catch (e) { setError((e as Error).message); return false } finally { setBusy(false) }
  }
  useDraftSafety(dirty, save, busy)
  const select = (row: Data) => { if (dirty) { setError('请先保存当前修改，再切换编排'); return } choose(row) }
  const create = () => {
    if (dirty) { setError('请先保存当前修改，再新建编排'); return }
    const personas = Object.fromEntries(Object.keys(routes).map(key => [key, (workspace?.pipeline[key] || []).filter((id: string) => workspace?.modules.find((m: Data) => m.id === id)?.is_persona)]))
    setBase(null); setDraft({ title: '我的个人贴编排', topic_id: candidates[0]?.topic_id || 0, personas, version: 1 }); setShowPublished(false); setError('')
  }
  const action = async (name: string) => {
    if (!draft?.id || dirty || busy) return
    setBusy(true); setError('')
    try { const updated = await api(`/topic-pipelines/${draft.id}/${name}`, 'POST', { version: draft.version }); choose(updated); setRows(rows.map(r => r.id === updated.id ? updated : r)) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const order = showPublished ? draft?.published?.personas : draft?.personas
  const modules = workspace?.modules || []
  const title = (id: string) => (showPublished ? draft?.published?.modules[id]?.title : modules.find((m: Data) => m.id === id)?.title) || '人格已删除'
  const move = (index: number, delta: number) => { const list = [...draft!.personas[route]]; [list[index], list[index + delta]] = [list[index + delta], list[index]]; setDraft({ ...draft, personas: { ...draft!.personas, [route]: list } }) }
  const readonly = busy || showPublished
  return <>
    <div className="workspace-intro"><div><h2>这篇个人贴里的猫，由你编排。</h2><p>{admin ? '查看所有用户的编排和生效版本。' : '选择全部共享人格并排序。'} {review ? '保存草稿后由管理员发布。' : '保存后生效。'} 编排只作用于绑定的本人公开个人贴。</p></div><button disabled={busy || dirty} onClick={load}><RefreshCw size={15}/>刷新</button></div>
    <ReviewSettings admin={admin} disabled={busy || dirty} onChange={setReview}/>
    <div className="topic-pipeline-grid"><aside className="panel topic-pipeline-list"><h3>{admin ? '所有用户的编排' : '我的编排'} <small>{rows.length}</small></h3><button disabled={busy || !workspace} onClick={create}><Plus size={16}/>新建个人贴编排</button>{rows.map(r => <button key={r.id} disabled={busy} className={draft?.id === r.id ? 'selected' : ''} onClick={() => select(r)}><strong>{r.title}</strong><small>@{r.owner_name} · #{r.topic_id}</small><small>{r.enabled ? `生效 v${r.published_version}` : '未启用'}{r.version !== r.published_version && ' · 有待发布草稿'}</small></button>)}</aside>
      <section className="panel">{draft ? <><div className="section-head"><h3>{showPublished ? draft.published?.title || draft.title : draft.title}</h3><span className="badge">{draft.enabled ? `生效 v${draft.published_version}` : '未启用'} · 草稿 v{draft.version}</span></div>
        <div className="tabs"><button className={!showPublished ? 'active' : ''} onClick={() => setShowPublished(false)}>{review ? '编辑草稿' : '编辑编排'}</button><button disabled={!draft.published_version} className={showPublished ? 'active' : ''} onClick={() => setShowPublished(true)}>只读生效版本</button></div>
        <fieldset disabled={readonly}><label className="field">编排名称<input maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })}/></label>
          <label className="field">本人个人贴主题 ID<input type="number" min="1" max="2147483647" disabled={!!draft.id} value={draft.topic_id || ''} onChange={e => setDraft({ ...draft, topic_id: Number(e.target.value) })}/><small>网址 /t/11957 中的 11957；保存时会核验分类、公开性与首帖作者。</small></label>
          {!draft.id && candidates.length > 0 && <label className="field">已找到的本人个人贴<select value={draft.topic_id} onChange={e => setDraft({ ...draft, topic_id: Number(e.target.value) })}>{candidates.map(c => <option key={c.topic_id} value={c.topic_id}>{c.title} · #{c.topic_id}</option>)}</select></label>}
        </fieldset>{draft.url && <p><a href={draft.url} target="_blank" rel="noreferrer">查看绑定的个人贴 ↗</a> · @{draft.owner_name}</p>}
        <div className="route-overview">{Object.entries(routes).map(([key, name]) => <button key={key} className={route === key ? 'active' : ''} onClick={() => setRoute(key)}>{name}<small>{order?.[key]?.length || 0} 个人格</small></button>)}</div>
        <h4>{routes[route]} · 人格顺序</h4><p className="muted">从上到下拼接，随后保留当前全局工作规则。空列表表示这条路由不附加人格。</p>
        {(order?.[route] || []).map((id: string, index: number) => <div className="route-module" key={id}><span className="step">{index + 1}</span><strong>{title(id)}</strong>{!showPublished && <div className="route-module-actions"><button className="icon-button" disabled={readonly || index === 0} aria-label={`上移人格 ${index + 1}`} onClick={() => move(index, -1)}><ArrowUp size={15}/></button><button className="icon-button" disabled={readonly || index === order[route].length - 1} aria-label={`下移人格 ${index + 1}`} onClick={() => move(index, 1)}><ArrowDown size={15}/></button><button className="icon-button" disabled={readonly} aria-label={`移除人格 ${index + 1}`} onClick={() => setDraft({ ...draft, personas: { ...draft.personas, [route]: draft.personas[route].filter((mid: string) => mid !== id) } })}><X size={15}/></button></div>}</div>)}
        {!showPublished && <select aria-label="添加人格" disabled={busy || order[route].length >= 40} value="" onChange={e => e.target.value && setDraft({ ...draft, personas: { ...draft.personas, [route]: [...draft.personas[route], e.target.value] } })}><option value="">＋ 从全部人格中添加</option>{modules.filter((m: Data) => (m.is_persona ?? m.data.persona) && !order[route].includes(m.id)).map((m: Data) => <option key={m.id} value={m.id}>{m.title}{review && !m.persona_published_version ? '（待审核）' : ''}</option>)}</select>}
        <details><summary>保留的全局工作规则（只读）</summary>{(workspace?.pipeline[route] || []).filter((id: string) => !modules.find((m: Data) => m.id === id)?.is_persona).map((id: string) => <p key={id}>{modules.find((m: Data) => m.id === id)?.title}</p>)}<p className="muted">这里展示当前全局草稿的工作模块；实际运行使用管理员已发布快照。</p></details>
        <div className="form-actions">{(admin || review === false && (!draft.enabled || draft.version !== draft.published_version)) && <button className="primary" disabled={dirty || busy || !draft.id} onClick={() => action('publish')}>{review ? '审核并发布此编排' : '让已保存编排生效'}</button>}{draft.enabled && <button disabled={dirty || busy} onClick={() => action('disable')}>停用，恢复全局编排</button>}</div>
      </> : <div className="empty"><Cat size={32}/><h3>为你的个人贴选一只不同的猫</h3><p>全部人格都可复用。新编排不会改变其他主题、私信或管理员聊天。</p><button disabled={busy || !workspace} onClick={create}>新建个人贴编排</button></div>}</section></div>
    <SaveDock dirty={dirty} busy={busy || review === null} save={() => void save()} error={error} label={review ? '保存草稿' : '保存并生效'} detail={review ? '保存为草稿；管理员发布后，仅绑定的本人公开个人贴生效' : '核验身份和来源后直接生效，仅用于绑定的本人公开个人贴'} idleLabel={draft ? draft.enabled ? '编排已保存，生效版本可查看' : '编排已保存，当前未启用' : '尚未选择编排'}/>
  </>
}
