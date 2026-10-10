import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Cat, Copy, FileText, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { api, type Data } from './api'
import { SaveDock, useDraftSafety } from './DraftSafety'
import { ReviewSettings } from './ReviewSettings'

const routes: Record<string, string> = { planner: '参与规划', replyer: '回复生成', memory: '记忆整理', summary: '主题摘要', agent: '主动研究' }
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const persona = (m: Data) => !!(m.is_persona ?? m.data.persona)
const values = (m: Data) => ({ title: m.title, data: m.data, grants: m.grants })
type Action = (fn: () => Promise<any>, message?: string) => Promise<boolean>

export function PromptWorkspace({ user, act }: { user: Data, act: Action }) {
  const [base, setBase] = useState<Data | null>(null), [modules, setModules] = useState<Data[]>([])
  const [pipeline, setPipeline] = useState<Data | null>(null), [selected, setSelected] = useState('')
  const [search, setSearch] = useState(''), [filter, setFilter] = useState('all'), [error, setError] = useState('')
  const [busy, setBusy] = useState(false), [confirmReload, setConfirmReload] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<Data | null>(null), [deleted, setDeleted] = useState<Data[]>([])
  const [route, setRoute] = useState('replyer'), form = useRef<HTMLFormElement>(null)
  const admin = user.role === 'admin'
  const accept = (data: Data, selection = '') => {
    setBase(data); setModules(data.modules); setPipeline(data.pipeline)
    const id = data.id_mapping?.[selection] || selection
    setSelected(data.modules.some((m: Data) => m.id === id) ? id : data.modules[0]?.id || '')
    setError(''); setDeleted([])
  }
  const load = async () => { setBusy(true); try { accept(await api('/prompts/workspace'), selected) } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  useEffect(() => { void load() }, [])
  const changed = modules.filter(m => {
    const original = base?.modules.find((b: Data) => b.id === m.id)
    return !original || !equal(values(m), values(original))
  })
  const pipelineDirty = !!base && !equal(pipeline, base.pipeline)
  const dirty = changed.length > 0 || pipelineDirty || deleted.length > 0
  const current = modules.find(m => m.id === selected)
  const update = (patch: Data) => setModules(modules.map(m => m.id === selected ? { ...m, ...patch, is_persona: patch.data ? !!(m.legacy_persona || patch.data.persona) : m.is_persona } : m))
  const save = async (): Promise<boolean> => {
    if (busy || !dirty) return !busy
    if (changed.some(m => !m.title.trim())) { setError('请为每个修改过的模块填写名称'); form.current?.reportValidity(); return false }
    setBusy(true); setError('')
    const payload: Data = { modules: changed.map(m => ({ id: m.id, ...values(m), version: m.version })), deleted }
    if (pipelineDirty) { payload.pipeline = pipeline; payload.pipeline_version = base!.pipeline_version }
    let failure = ''
    const ok = await act(async () => {
      try { accept(await api('/prompts/workspace/save', 'POST', payload), selected) }
      catch (e) { failure = (e as Error).message; throw e }
    }, base?.require_review ? '修改已保存为草稿，等待管理员发布' : '人格修改已生效；系统提示词与全局编排保留草稿')
    if (!ok) setError(failure || '保存未完成，修改仍保留，请重试')
    setBusy(false)
    return ok
  }
  useDraftSafety(dirty, save, busy)
  const create = (source?: Data) => {
    const module = { id: 'new-' + crypto.randomUUID(), owner: user.id, editable: true, title: source ? source.title + '（副本）' : '新提示词',
      data: source ? { ...structuredClone(source.data), persona: persona(source) } : { content: '', description: '', persona: false }, grants: [], version: 1 }
    setModules([...modules, module]); setSelected(module.id)
  }
  const move = (key: string, index: number, delta: number) => {
    const ids = [...pipeline![key]]; [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]]
    setPipeline({ ...pipeline, [key]: ids })
  }
  const visible = modules.filter(m => (m.title + ' ' + m.data.description).toLowerCase().includes(search.toLowerCase()) &&
    (filter === 'all' || filter === 'mine' && m.owner === user.id || filter === 'persona' && persona(m) || filter === 'system' && !persona(m)))
  if (!base) return <div className="empty">{error || '正在读取提示词工作区…'}{error && <button onClick={load}>重试</button>}</div>
  return <div className={'prompt-workspace ' + (!admin ? 'editor-workspace' : '')}>
    <ReviewSettings admin={admin} disabled={busy || dirty} onChange={required => setBase(previous => previous ? { ...previous, require_review: required } : previous)}/>
    <div className="workspace-intro"><div><h2>一边写，一边编排。</h2><p>{admin ? `全局配置 v${base.active_snapshot || '未发布'} · 系统提示词和全局编排仍需管理员发布。` : '全部人格和全局编排可只读查看；仅自己的或获授权模块可编辑。个人贴编排在侧栏单独管理。'}</p></div>
      <button disabled={busy} onClick={() => dirty ? setConfirmReload(true) : void load()}><RefreshCw size={15}/>重新读取</button></div>
    <div className="prompt-columns">
      <aside className="prompt-library panel"><div className="section-head"><h3>模块书架 <small>{modules.length}</small></h3><button className="icon-button" aria-label="新建模块" disabled={busy} onClick={() => create()}><Plus size={19}/></button></div>
        <input aria-label="搜索模块" placeholder="搜索名称或说明" value={search} onChange={e => setSearch(e.target.value)}/>
        <select aria-label="筛选模块" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部模块</option><option value="persona">人格模块</option><option value="system">系统提示词</option><option value="mine">我的模块</option></select>
        <div className="prompt-module-list">{visible.map(m => <button className={selected === m.id ? 'selected' : ''} key={m.id} onClick={() => setSelected(m.id)}>
          {persona(m) ? <Cat size={17}/> : <FileText size={17}/>}<span><strong>{m.title}</strong><small>{m.id.startsWith('new-') ? '新建草稿' : `${m.owner === user.id ? '我的模块' : admin ? '共享模块' : m.editable ? '已获授权' : '只读共享'} · v${m.version}`}</small></span>
          {changed.some(c => c.id === m.id) && <i className="dirty-dot" aria-label="未保存"/>}</button>)}</div>
        {!visible.length && <p className="muted">{modules.length ? '没有匹配的模块' : '从新建一块提示词开始。'}</p>}
        <button className="library-create" disabled={busy} onClick={() => create()}><Plus size={15}/>新建模块</button>
      </aside>
      <section className="prompt-editor panel">{current ? <><div className="section-head"><div><span className="badge">{persona(current) ? '人格' : '系统提示词'} · {current.id.startsWith('new-') ? '新草稿' : `v${current.version}`}</span></div>
        <div className="editor-tools">{admin && persona(current) && base.require_review && !current.id.startsWith('new-') && <button disabled={busy || dirty} onClick={async () => { setBusy(true); try { accept(await api(`/personas/${current.id}/publish`, 'POST', { id: current.id, version: current.version }), current.id) } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }}>审核并发布人格</button>}<button disabled={busy} onClick={() => create(current)}><Copy size={14}/>复制模块</button>{!current.id.startsWith('new-') && (admin || current.owner === user.id) && <button className="icon-button" disabled={busy} aria-label="删除模块" onClick={() => setDeleteTarget(current)}><Trash2 size={15}/></button>}</div></div>
        {persona(current) && <p className="muted">{current.persona_published_version ? `可使用 v${current.persona_published_version}` : '尚未审核'}{base.require_review && current.version !== current.persona_published_version ? ' · 当前修改待审核' : ''}</p>}
        <form ref={form} onSubmit={e => { e.preventDefault(); void save() }}><fieldset disabled={busy || current.editable === false}>
          <label className="field"><span>模块名称</span><input required maxLength={200} value={current.title} onChange={e => update({ title: e.target.value })}/></label>
          <label className="field"><span>用途说明</span><input maxLength={500} value={current.data.description} onChange={e => update({ data: { ...current.data, description: e.target.value } })}/></label>
          <label className="toggle-row"><span><strong>这是一个人格模块</strong><small>人格向所有登录用户只读共享；旧版角色保留原始标记</small></span><input type="checkbox" disabled={!!current.legacy_persona} checked={persona(current)} onChange={e => update({ data: { ...current.data, persona: e.target.checked } })}/></label>
          <label className="field prompt-content"><span>提示词内容 <small>{current.data.content.length.toLocaleString()} / 50,000</small></span><textarea className="code-editor" rows={20} maxLength={50000} value={current.data.content} onChange={e => update({ data: { ...current.data, content: e.target.value } })}/><small>支持 Markdown。切换模块不会丢失尚未保存的内容。</small></label>
          {admin && <details className="prompt-grants"><summary>编辑授权 · {current.grants.length} 个账户</summary><p>被授权者可修改此模块，不能转授权限。人格是否需要审核由上方开关控制；系统提示词仍需管理员发布。</p><div className="grants">{base.accounts.map((a: Data) => <label key={a.id}><input type="checkbox" disabled={!a.active && !current.grants.includes(a.id)} checked={current.grants.includes(a.id)} onChange={e => update({ grants: e.target.checked ? [...current.grants, a.id] : current.grants.filter((id: string) => id !== a.id) })}/>{a.username}{!a.active && '（已停用）'}</label>)}{!base.accounts.length && <small>还没有编辑者账户</small>}</div></details>}
          {current.id.startsWith('new-') && <button type="button" onClick={() => {
            setModules(modules.filter(m => m.id !== current.id)); setSelected(modules.find(m => m.id !== current.id)?.id || '')
            if (pipeline) setPipeline(Object.fromEntries(Object.entries(pipeline).map(([key, ids]) => [key, ids.filter((id: string) => id !== current.id)])))
          }}>放弃这个新模块</button>}
        </fieldset></form></> : <div className="empty"><Cat/><h3>给猫写一点新灵感</h3><p>从书架选择模块，或新建自己的提示词。</p><button className="primary" onClick={() => create()}>新建模块</button></div>}</section>
      {pipeline && <aside className="prompt-routing panel"><h3>路由编排</h3><p>{admin ? '从上到下拼接。点击模块即可在左侧编辑。' : '全局草稿按从上到下的顺序拼接。点击模块查看内容。'}</p>
        <label className="field"><span>工作路由</span><select value={route} onChange={e => setRoute(e.target.value)}>{Object.entries(routes).map(([key, title]) => <option key={key} value={key}>{title}</option>)}</select></label>
        <div className="route-overview">{Object.entries(routes).map(([key, title]) => <button className={key === route ? 'active' : ''} key={key} onClick={() => setRoute(key)}><span>{title}</span><small>{pipeline[key].length}</small></button>)}</div>
        <h4>{routes[route]} · {pipeline[route].length} 个模块</h4>
        <div className="route-module-list">{pipeline[route].map((id: string, index: number) => <div className={id === selected ? 'route-module selected' : 'route-module'} key={id}><button className="route-module-title" onClick={() => setSelected(id)}><span className="step">{index + 1}</span><span>{modules.find(m => m.id === id)?.title || '模块不存在'}</span></button><div className="route-module-actions">{admin && <><button className="icon-button" disabled={busy || index === 0} aria-label={`上移模块 ${index + 1}`} onClick={() => move(route, index, -1)}><ArrowUp size={14}/></button><button className="icon-button" disabled={busy || index === pipeline[route].length - 1} aria-label={`下移模块 ${index + 1}`} onClick={() => move(route, index, 1)}><ArrowDown size={14}/></button><button className="icon-button" disabled={busy} aria-label={`移除模块 ${index + 1}`} onClick={() => setPipeline({ ...pipeline, [route]: pipeline[route].filter((mid: string) => mid !== id) })}><X size={14}/></button></>}</div></div>)}</div>
        {!pipeline[route].length && <p className="muted">还没有模块，添加后开始编排。</p>}
        {admin && <select aria-label="添加编排模块" disabled={busy || pipeline[route].length >= 40} value="" onChange={e => e.target.value && setPipeline({ ...pipeline, [route]: [...pipeline[route], e.target.value] })}><option value="">＋ 添加模块</option>{modules.filter(m => !pipeline[route].includes(m.id)).map(m => <option key={m.id} value={m.id}>{m.title}</option>)}</select>}
        <p className="route-footnote">{admin ? '模块编辑与编排一次保存。前往回复策略页发布完整配置。' : '全局编排只读。到「个人贴编排」选择专属人格，系统工作规则仍保留。'}</p>
      </aside>}
    </div>
    <SaveDock dirty={dirty} busy={busy} save={() => void save()} error={error} label={base.require_review ? '保存草稿' : '保存修改'} detail={dirty ? `${changed.length} 个模块${deleted.length ? ` · 删除 ${deleted.length} 个` : ''}${pipelineDirty ? ' · 编排已修改' : ''} · ⌘ / Ctrl + S` : base.require_review ? '人格修改等待审核；系统提示词和全局编排需管理员发布' : '人格保存后生效；系统提示词和全局编排保存为草稿'}/>
    {deleteTarget && <div className="backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="删除提示词模块"><h2>删除「{deleteTarget.title}」？</h2><p>保存时删除草稿模块{admin ? '，并移除草稿编排中的引用' : '。已被全局编排引用的模块需管理员先移除引用'}。已发布快照仍然保留。</p><div className="form-actions"><button onClick={() => setDeleteTarget(null)}>取消</button><button className="danger" onClick={() => {
      setDeleted([...deleted, { id: deleteTarget.id, version: deleteTarget.version }]); setModules(modules.filter(m => m.id !== deleteTarget.id)); setSelected(modules.find(m => m.id !== deleteTarget.id)?.id || '')
      if (pipeline) setPipeline(Object.fromEntries(Object.entries(pipeline).map(([key, ids]) => [key, ids.filter((id: string) => id !== deleteTarget.id)])))
      setDeleteTarget(null)
    }}>标记删除</button></div></section></div>}
    {confirmReload && <div className="backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="重新读取提示词"><h2>有未保存的修改</h2><p>重新读取会放弃当前所有未保存的模块与编排。遇到冲突时，可先复制内容，再读取最新版本合并。</p><div className="form-actions"><button onClick={() => setConfirmReload(false)}>继续编辑</button><button className="danger" onClick={() => { setConfirmReload(false); void load() }}>放弃修改并重新读取</button></div></section></div>}
  </div>
}
