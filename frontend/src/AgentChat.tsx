import { useEffect, useRef, useState, type FormEvent } from 'react'
import { BookOpen, Check, MessageSquare, Plus, Save, Search, Settings2, Square, X } from 'lucide-react'
import { api, type Data } from './api'

type Props = { act: (fn: () => Promise<any>, message?: string) => Promise<boolean> }
const statuses: Data = { queued: '等待研究', running: '正在研究', completed: '任务完成', awaiting_confirmation: '草稿待确认', failed: '研究失败', cancelled: '已停止', expired: '已过期', interrupted: '已中断' }
const tools: Data = { forum_search: '论坛搜索', forum_read_topic: '读取主题', forum_user_activity: '用户活动', memory_lookup: '检索记忆', release_describe: '发布信息', draft_reply: '生成草稿', target: '核验目标', result: '研究结果', send_authorization: '发送授权' }
const terminal = new Set(['completed', 'awaiting_confirmation', 'failed', 'cancelled', 'expired', 'interrupted'])

export function AgentChat({ act }: Props) {
  const [sessions, setSessions] = useState<Data[]>([]), [sessionId, setSessionId] = useState(''), [chat, setChat] = useState<Data | null>(null)
  const [taskId, setTaskId] = useState(''), [task, setTask] = useState<Data | null>(null), [instruction, setInstruction] = useState('')
  const [kind, setKind] = useState('research'), [target, setTarget] = useState(''), [replyTo, setReplyTo] = useState(''), [privateTask, setPrivate] = useState(false)
  const [maxChars, setMaxChars] = useState(3000), [direct, setDirect] = useState(false), [text, setText] = useState(''), [confirm, setConfirm] = useState(false)
  const [policy, setPolicy] = useState<Data | null>(null), [settingsOpen, setSettings] = useState(false), [error, setError] = useState(''), [connection, setConnection] = useState('')
  const cursor = useRef(0), currentState = useRef('')
  const refreshSessions = async () => setSessions(await api('/agent/sessions'))
  const refreshChat = async (id = sessionId) => { if (id) setChat(await api('/agent/sessions/' + id)) }
  const refreshTask = async (id = taskId) => {
    if (!id) return
    const result = await api('/agent/tasks/' + id)
    setTask(result)
    setError('')
  }
  useEffect(() => {
    void Promise.all([refreshSessions(), api('/agent/settings').then(setPolicy)]).catch(e => setError(e.message))
  }, [])
  useEffect(() => {
    setChat(null); setTask(null); setTaskId(''); setConfirm(false)
    if (sessionId) void api('/agent/sessions/' + sessionId).then(result => {
      setChat(result)
      if (result.tasks[0]) setTaskId(result.tasks[0].id)
    }).catch(e => setError(e.message))
  }, [sessionId])
  useEffect(() => {
    if (!taskId) return
    cursor.current = 0; currentState.current = ''
    let disposed = false, timer: ReturnType<typeof setTimeout> | undefined
    const update = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => { if (!disposed) void refreshTask(taskId).catch(e => setError(e.message)) }, 150)
    }
    void refreshTask(taskId).catch(e => setError(e.message))
    const stream = new EventSource('/api/agent/tasks/' + taskId + '/stream', { withCredentials: true })
    stream.onopen = () => setConnection('进度已连接')
    stream.addEventListener('step', (event: MessageEvent) => { cursor.current = Number(event.lastEventId) || cursor.current; update() })
    stream.addEventListener('status', (event: MessageEvent) => {
      const value = JSON.parse(event.data)
      if (currentState.current !== value.state) { currentState.current = value.state; update() }
    })
    stream.addEventListener('done', () => { stream.close(); setConnection('研究进度已保存'); update(); void refreshChat(sessionId) })
    stream.onerror = () => setConnection('进度连接中断，正在重连')
    const poll = setInterval(() => { if (!disposed) void refreshTask(taskId).catch(e => setError(e.message)) }, 5000)
    return () => { disposed = true; stream.close(); clearInterval(poll); if (timer) clearTimeout(timer) }
  }, [taskId])
  useEffect(() => { if (task?.draft) setText(task.draft.text) }, [task?.draft?.id, task?.draft?.version])
  const newChat = () => act(async () => { const session = await api('/agent/sessions', 'POST'); await refreshSessions(); setSessionId(session.id) }, '管理会话已创建')
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    await act(async () => {
      let id = sessionId
      if (!id) { id = (await api('/agent/sessions', 'POST')).id; setSessionId(id) }
      const result = await api(`/agent/sessions/${id}/messages`, 'POST', {
        text: instruction, kind, target_topic: Number(target) || 0, reply_to: Number(replyTo) || 0,
        private: privateTask, max_chars: maxChars, allow_send: direct && kind === 'reply',
      })
      setTaskId(result.task_id); setInstruction(''); await refreshChat(id); await refreshSessions()
    }, direct ? '指令已提交，本次允许向指定目标回复' : '开始研究，结果先预览')
  }
  const busy = task && !terminal.has(task.state)
  const draft = task?.draft, changed = draft && text !== draft.text
  const expired = task && task.expires <= Date.now() / 1000
  const saveDraft = () => act(async () => {
    await api('/agent/drafts/' + draft.id, 'PUT', { text, target_topic: draft.target_topic, version: draft.version })
    await refreshTask()
  }, '草稿已保存，原批准已失效')
  const confirmDraft = () => act(async () => {
    await api('/agent/drafts/' + draft.id + '/confirm', 'POST', { digest: draft.digest })
    setConfirm(false); await refreshTask()
  }, '已批准，等待发送条件校验')
  return <>
    <div className="toolbar"><div><h2>让猫先去了解，再好好回答。</h2><p className="muted">研究、整理或写一篇回复。每个任务独立授权，默认先给你预览。</p></div><button onClick={() => setSettings(true)}><Settings2 size={16}/>研究策略</button></div>
    {error && <p role="alert" className="error">{error}</p>}
    <div className="agent-layout">
      <aside className="panel agent-sessions"><button className="primary" onClick={newChat}><Plus size={16}/>新会话</button>
        <div className="agent-session-list">{sessions.map(s => <button className={sessionId === s.id ? 'active' : ''} key={s.id} onClick={() => setSessionId(s.id)}><MessageSquare size={15}/><span>{s.title}</span></button>)}</div>
        {!sessions.length && <p className="muted">交代第一件事吧。</p>}
        <small>会话仅自己可见。私密研究需指定原对话；上一次任务的私人内容不会自动加入下一次任务。</small>
      </aside>
      <section className="panel agent-conversation">
        <div className="agent-messages">{chat?.messages?.length ? chat.messages.map((m: Data) => <article key={m.id} className={'agent-message ' + m.role}>
          <header><strong>{m.role === 'user' ? '你' : 'SuenMeow'}</strong><span className="badge">{m.meta.private ? '私密' : '公开资料'}</span></header><p>{m.text}</p>
          {m.meta.task_id && <button onClick={() => setTaskId(m.meta.task_id)}>查看这次任务</button>}
        </article>) : <div className="empty"><Search size={28}/><h3>有事情想交给猫？</h3><p>“查一下大家对猫窝的建议，给我来源和改进清单。”<br/>“介绍 SuenMeow 2.0，在主题 #1234 写一篇长回复，先给我看看。”</p></div>}</div>
        <form className="agent-compose" onSubmit={submit}>
          <label className="field"><span>这次要做什么</span><textarea required rows={4} maxLength={12000} value={instruction} onChange={e => setInstruction(e.target.value)} placeholder="交代任务、范围和希望得到的结果…"/></label>
          <div className="field-grid"><label className="field"><span>任务类型</span><select value={kind} onChange={e => { setKind(e.target.value); setDirect(false) }}><option value="research">研究与整理</option><option value="reply">写一篇回复</option></select></label>
            <label className="field"><span>既有主题 ID {(kind === 'reply' || privateTask) ? '（必填）' : '（可选）'}</span><input type="number" min="1" required={kind === 'reply' || privateTask} value={target} onChange={e => setTarget(e.target.value)}/></label>
            {kind === 'reply' && <><label className="field"><span>回复楼层（可选）</span><input type="number" min="1" value={replyTo} onChange={e => setReplyTo(e.target.value)}/></label><label className="field"><span>最长字符数</span><input type="number" min="100" max={policy?.max_chars || 8000} value={maxChars} onChange={e => setMaxChars(Number(e.target.value))}/><small>还受论坛、模型输出和预算限制</small></label></>}
          </div>
          <label className="agent-check"><input type="checkbox" checked={privateTask} onChange={e => setPrivate(e.target.checked)}/>这是指定私信或受限主题中的私密任务</label>
          {kind === 'reply' && <label className="agent-check"><input type="checkbox" checked={direct} onChange={e => setDirect(e.target.checked)}/>仅本次：研究完成后，可直接向所选目标回复一条</label>}
          <div className="form-actions"><small className="muted">{busy ? '当前任务正在进行，可以先停止再提交新要求。' : direct ? '本次指令授权实际发帖，仍受暂停、冷却和水位控制。' : '暂停时也可以研究，发送需另行确认。'}</small><button className="primary" disabled={!!busy || !instruction.trim() || policy?.enabled === false}><Search size={16}/>{kind === 'reply' ? direct ? '开始并允许本次回复' : '研究并写草稿' : '开始研究'}</button></div>
        </form>
      </section>
      <aside className="agent-evidence" aria-label="任务结果">
        {task ? <><section className="panel agent-progress"><div className="section-head"><h3>{draft && expired && !draft.reply_id ? '草稿已过期' : statuses[task.state] || task.state}</h3>{!task.cancelled && !['sent', 'sending', 'unknown'].includes(draft?.reply_state) && <button onClick={() => act(() => api('/agent/tasks/' + task.id + '/cancel', 'POST').then(() => refreshTask()), '任务已停止')}><Square size={14}/>停止</button>}</div><p>{task.reason}</p><small className="muted">配置 v{task.snapshot_id} · {connection} · {Number(task.tokens).toLocaleString()} tokens</small>
          <div className="agent-steps" aria-live="polite">{task.steps.map((s: Data) => <details key={s.id}><summary><span className={'dot ' + s.state}/>{tools[s.tool] || s.tool}<small>{s.state === 'running' ? '开始' : s.state === 'completed' ? '完成' : '停止或失败'}</small></summary><pre>{JSON.stringify(s.detail, null, 2)}</pre></details>)}</div>
        </section>
          {draft && <section className="panel agent-draft"><h3>回复草稿</h3><p>{task.constraints.private ? '私密目标' : '公开主题'} #{draft.target_topic}{task.constraints.reply_to ? ' / 楼层 ' + task.constraints.reply_to : ''}</p><textarea aria-label="回复草稿" rows={12} value={text} maxLength={task.constraints.max_chars} onChange={e => setText(e.target.value)} disabled={expired || task.cancelled || ['sending', 'sent', 'unknown'].includes(draft.reply_state)}/><small>{text.length} / {task.constraints.max_chars} 字符 · v{draft.version}</small><p>{task.send_block_reason || draft.send_reason || '预览不等于发送；修改后需要重新确认。'}</p><small>有效期至 {new Date(task.expires * 1000).toLocaleString('zh-CN')}</small><div className="form-actions"><button disabled={!changed || expired || task.cancelled || ['sending', 'sent', 'unknown'].includes(draft.reply_state)} onClick={saveDraft}><Save size={14}/>保存修改</button><button className="primary" disabled={changed || expired || !!task.send_block_reason || draft.confirmed || task.cancelled || !!draft.reply_id} onClick={() => setConfirm(true)}><Check size={14}/>确认发送</button></div>{draft.reply_state === 'unknown' && <p className="error">请到回复审核页核实；不会自动重发。</p>}</section>}
          {!draft && task.result && <section className="panel"><h3>研究结果</h3><p className="reply-text">{task.result}</p></section>}
          <section className="panel agent-sources"><h3><BookOpen size={17}/>读取的来源</h3>{task.sources.map((s: Data) => <article className="agent-source" key={s.id}><a href={s.url} target="_blank" rel="noreferrer">{s.title || '主题'} #{s.topic_id} / {s.post_number}</a><small>{s.public ? '公开' : '私密'} · {s.username}</small><p>{s.text}</p></article>)}{!task.sources.length && <p className="muted">还没有读取来源。</p>}</section>
        </> : <section className="panel"><Search size={24}/><h3>研究进度和证据</h3><p className="muted">开始任务后，这里展示读取工具、真实来源和草稿。</p></section>}
      </aside>
    </div>
    {confirm && draft && <div className="backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="确认发送这篇回复"><header><h2>确认发送这篇回复</h2><button aria-label="关闭" onClick={() => setConfirm(false)}><X size={18}/></button></header><p>向{task?.constraints.private ? '私密对话' : '公开主题'} #{draft.target_topic} 发送一条 {text.length} 字符的回复。批准绑定当前正文；发送前还会检查权限、有效期与冷却。</p><p className="reply-text agent-confirm-text">{text}</p><div className="form-actions"><button onClick={() => setConfirm(false)}>继续预览</button><button className="primary" disabled={expired || !!task?.send_block_reason} onClick={confirmDraft}>批准这一版</button></div></section></div>}
    {settingsOpen && policy && <div className="backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="研究策略"><header><h2>研究策略</h2><button aria-label="关闭" onClick={() => setSettings(false)}><X size={18}/></button></header><form onSubmit={e => { e.preventDefault(); void act(() => api('/agent/settings', 'PUT', policy), '研究策略已保存').then(ok => { if (ok) setSettings(false) }) }}><label className="agent-check"><input type="checkbox" checked={policy.enabled} onChange={e => setPolicy({ ...policy, enabled: e.target.checked })}/>启用管理员 Agent</label><div className="field-grid">{[['max_steps', '工具步骤上限', 1, 16], ['max_topics', '不同主题上限', 1, 8], ['max_seconds', '研究时间（秒）', 20, 300], ['max_tokens', '任务 token 预算', 1000, 100000], ['max_chars', '长回复字符上限', 500, 15000], ['max_parallel', '系统并发任务', 1, 2]].map(([k, label, min, max]) => <label className="field" key={k}><span>{label}</span><input required type="number" min={min} max={max} value={policy[k]} onChange={e => setPolicy({ ...policy, [k]: Number(e.target.value) })}/></label>)}</div><label className="agent-check"><input type="checkbox" checked={policy.auto_research} onChange={e => setPolicy({ ...policy, auto_research: e.target.checked })}/>普通新消息按需主动研究（默认关闭）</label><h3>允许的读取工具</h3>{Object.entries(tools).filter(([k]) => ['forum_search', 'forum_read_topic', 'forum_user_activity', 'memory_lookup', 'release_describe'].includes(k)).map(([k, label]) => <label className="agent-check" key={k}><input type="checkbox" checked={policy.allowed_tools.includes(k)} onChange={e => setPolicy({ ...policy, allowed_tools: e.target.checked ? [...policy.allowed_tools, k] : policy.allowed_tools.filter((x: string) => x !== k) })}/>{label}</label>)}<p className="muted">每天和单主题总预算同时生效。需要在连接与模型页配置支持工具调用的 Agent 模型。</p><div className="form-actions"><button type="button" onClick={() => setSettings(false)}>取消</button><button className="primary">保存研究策略</button></div></form></section></div>}
  </>
}
