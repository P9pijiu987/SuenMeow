import { useEffect, useState } from 'react'
import { api, type Data } from './api'

const fields: [string, string, number, number][] = [
  ['merge_seconds', '合并等待（秒）', 8, 30], ['max_batch', '每批新发言上限', 1, 20],
  ['context_posts', '背景发言数', 2, 20], ['input_bytes', '单批资料上限（字节）', 4000, 60000],
  ['ordinary_chars', '普通回复字数上限', 100, 2000], ['write_interval', '所有动作最短间隔（秒）', 5, 300],
  ['reaction_percent', '表情目标比例（%）', 0, 100], ['reaction_window', '统计最近多少条发言', 20, 500],
  ['reaction_daily', '表情每日上限', 0, 1000], ['reaction_topic_hourly', '同帖每小时表情上限', 0, 200],
  ['reaction_user_hourly', '同用户每小时表情上限', 0, 100],
  ['unmentioned_percent', '未被邀请的文字参与比例（%）', 0, 100],
  ['reply_daily', '文字每日上限', 0, 1000], ['reply_topic_hourly', '同帖每小时文字上限', 0, 100],
  ['reply_user_hourly', '同用户每小时文字上限', 0, 100],
]

export function InteractionSettings({ value, defaults, modules, onChange }: { value: Data | null, defaults: Data, modules: Data[], onChange: (value: Data | null) => void }) {
  const set = (key: string, v: any) => onChange({ ...value, [key]: v })
  return <section className="panel"><h3>自然接话与表情</h3><p>同帖的新发言短时合并，一次判断逐条表情与最多一条普通回复。旧配置保留原流程；此处保存为草稿，发布后生效。不会打开自动运行。</p>
    <label className="toggle-row"><span><strong>使用新版互动流程</strong><small>普通回复不再逐次调用记忆模型；人工审核模式下表情同样需要审核。</small></span><input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked ? { ...defaults } : null)}/></label>
    {value && <><div className="field-grid"><label className="field"><span>接话倾向</span><select value={value.reply_inclination} onChange={e => set('reply_inclination', e.target.value)}><option value="quiet">安静：主要回应邀请</option><option value="balanced">适中：有话可接才参与</option><option value="chatty">活泼：可补充时多参与一点</option></select></label>
      {fields.map(([key, label, min, max]) => <label className="field" key={key}><span>{label}</span><input type="number" required min={min} max={max} value={value[key]} onChange={e => set(key, Number(e.target.value))}/></label>)}
      {(['categories', 'topics'] as const).map(key => <label className="field" key={key}><span>{key === 'categories' ? '观察分类 ID' : '观察主题 ID'}</span><input value={value[key].join(', ')} onChange={e => set(key, e.target.value.split(/[,，]/).map(v => Number(v.trim())).filter(v => v > 0))}/><small>逗号分隔；留空不限该项。私密内容仍只在原对话中处理。</small></label>)}
      <label className="field"><span>互动工作规则</span><select required value={value.rule_module} onChange={e => set('rule_module', e.target.value)}><option value="">请选择工作规则</option>{modules.map(m => <option value={m.id} key={m.id}>{m.title}</option>)}</select><small>在「人格与提示词」编辑所选规则。原有回复人格和风格仍保留。</small></label>
    </div><label className="toggle-row"><span><strong>未被点名也可以接话</strong></span><input type="checkbox" checked={value.unmentioned} onChange={e => set('unmentioned', e.target.checked)}/></label>
    <label className="toggle-row"><span><strong>复杂问题可以另写长回答</strong><small>确需展开时增加一次正文调用；普通接话仍只有一次。</small></span><input type="checkbox" checked={value.extended_replies} onChange={e => set('extended_replies', e.target.checked)}/></label>
    <label className="toggle-row"><span><strong>自动记住明确自述的事实</strong><small>在现有判断中提取，必须有本人原句和来源；不额外调用记忆模型。</small></span><input type="checkbox" checked={value.automatic_memory !== false} onChange={e => set('automatic_memory', e.target.checked)}/></label>
    <label className="toggle-row"><span><strong>允许论坛原生表情</strong><small>按内容适合程度、比例与限额选择；不强行凑够三分之一。</small></span><input type="checkbox" checked={value.reactions} onChange={e => set('reactions', e.target.checked)}/></label></>}
  </section>
}

const labels: Data = { approval: '待审核', ready: '等待写入', sent: '已确认', unknown: '待核实', expired: '已过期', rejected: '已拒绝', cancelled: '已取消', sending: '写入中' }
type Action = (fn: () => Promise<any>, message?: string) => Promise<boolean>
export function InteractionQueue({ act }: { act: Action }) {
  const [data, setData] = useState<Data | null>(null), [error, setError] = useState(''), [show, setShow] = useState(false)
  const load = async () => { try { setData(await api('/interactions')); setError('') } catch (e) { setError((e as Error).message) } }
  useEffect(() => { void load() }, [])
  const decide = (id: string, action: string) => act(() => api(`/reactions/${id}/decision`, 'POST', { action }).then(load), '动作状态已更新；仍需通过共享写入门')
  return <section className="panel"><div className="section-head"><div><h3>逐帖阅读与表情审核</h3><p>同一批的文字与表情独立审核。结果不明确时不会自动再点一次。</p></div><button onClick={() => void load()}>刷新</button></div>
    {error && <p className="error" role="alert">{error}</p>}
    {data && <><p>已观察 {Object.values(data.counts).reduce((a: number, v) => a + Number(v), 0)} 条 · 已判断 {data.counts.evaluated || 0} 条 · 未评估 {data.counts.unevaluated || 0} 条 · 今日全部模型请求 {data.calls_today || 0} 次</p>
      <p>今日普通互动：{data.batch_calls_today || 0} 次请求 · {data.batch_tokens_today || 0} token · 每百条有效发言 {data.batch_tokens_per_100 || 0} token（不含 Agent 研究，失败请求保守记账）</p>
      <p>最近有效发言 {data.eligible_recent || 0} 条 · 已确认表情 {data.reacted_recent || 0} 条 · 实际比例 {data.eligible_recent ? (100 * data.reacted_recent / data.eligible_recent).toFixed(1) : '0'}%</p>
      {!data.reactions.length && <p className="muted">还没有表情动作。新版流程发布且运行后才会生成；只读状态不调用模型。</p>}
      {data.reactions.map((r: Data) => <article className="reaction-item" key={r.id}><div className="section-head"><strong>{r.private ? '私密对话' : '公开主题'} #{r.topic_id} · {r.number} 楼 · :{r.emoji}:</strong><span className="badge">{labels[r.state] || r.state}</span></div>
        <p className="reply-text">{r.text}</p><p>{r.reason} · 批次 {r.batch.slice(0, 8)}</p>
        <div className="form-actions">{data.site && <a href={`${data.site}/t/${r.topic_id}/${r.number}`} target="_blank" rel="noreferrer">查看原帖</a>}{r.state === 'approval' && <><button onClick={() => void decide(r.id, 'reject')}>拒绝</button><button className="primary" onClick={() => void decide(r.id, 'approve')}>批准这个表情</button></>}{r.state === 'unknown' && <><button onClick={() => void act(() => api(`/reactions/${r.id}/check`, 'POST', {}).then(load), '已只读核对，未执行新表情')}>只读核对一次</button><button onClick={() => void decide(r.id, 'resolve_unsent')}>已核实未生效，结束动作</button><button onClick={() => void decide(r.id, 'resolve_sent')}>已在论坛核实生效</button></>}</div>
      </article>)}
      <button onClick={() => setShow(!show)}>{show ? '收起逐帖原因' : '查看逐帖原因'}</button>{show && <div className="table-scroll"><table><thead><tr><th>主题 / 楼层</th><th>用户</th><th>状态</th><th>原因</th></tr></thead><tbody>{data.posts.map((p: Data) => <tr key={p.id}><td>#{p.topic_id} / {p.number} {p.private && '· 私密'}</td><td>{p.username}</td><td>{p.state}</td><td>{p.reason}</td></tr>)}</tbody></table></div>}
    </>}
  </section>
}
