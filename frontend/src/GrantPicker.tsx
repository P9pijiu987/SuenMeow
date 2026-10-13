import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api, type Data } from './api'

export function GrantPicker({ accounts: initial, grants, onChange }: { accounts: Data[], grants: string[], onChange: (ids: string[]) => void }) {
  const [accounts, setAccounts] = useState(initial), [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  useEffect(() => { setAccounts(initial) }, [initial])
  const refresh = async () => {
    if (busy) return
    setBusy(true); setError('')
    try { setAccounts((await api('/accounts')).filter((a: Data) => a.role === 'editor')) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  const eligible = accounts.filter(a => a.active || grants.includes(a.id))
  const visible = eligible.filter(a => [a.display_name, a.forum_username, a.username, a.forum_user_id].join(' ').toLowerCase().includes(search.trim().toLowerCase()))
  const missing = grants.filter(id => !accounts.some(a => a.id === id))
  return <details className="prompt-grants" onToggle={e => { if (e.currentTarget.open) void refresh() }}>
    <summary>编辑授权 · {grants.length} 个账户</summary>
    <p>显示可用编辑者的论坛身份。停用账户默认隐藏；已有授权仍可撤销。管理员无需另行授权，被授权者不能转授权限。</p>
    <div className="grant-toolbar"><input aria-label="搜索授权账户" placeholder="搜索昵称、论坛用户名或用户 ID" value={search} onChange={e => setSearch(e.target.value)}/><button type="button" disabled={busy} onClick={() => void refresh()}><RefreshCw size={14}/>{busy ? '刷新中' : '刷新名单'}</button></div>
    {error && <p role="alert" className="error">名单刷新失败：{error}。当前选择已保留，请重试。</p>}
    <div className="grant-accounts">{visible.map(a => <label className="grant-account" key={a.id}>
      <input type="checkbox" disabled={!a.active && !grants.includes(a.id)} checked={grants.includes(a.id)} onChange={e => onChange(e.target.checked ? [...grants, a.id] : grants.filter(id => id !== a.id))}/>
      <span className="account-avatar">{(a.display_name || a.username)[0].toUpperCase()}{a.avatar_url && <img src={a.avatar_url} alt="" onError={e => { e.currentTarget.style.display = 'none' }}/>}</span>
      <span><strong>{a.display_name || a.username}{!a.active && '（已停用）'}</strong><small>{a.forum_user_id ? `@${a.forum_username} · 论坛用户 ID ${a.forum_user_id}` : a.forum_username ? `未验证关联：@${a.forum_username}` : '未绑定论坛身份'}</small></span>
    </label>)}{missing.map(id => <label className="grant-account" key={id}><input type="checkbox" checked onChange={() => onChange(grants.filter(aid => aid !== id))}/><span>不可用账户 · {id.slice(0, 8)}<small>可取消此授权</small></span></label>)}</div>
    {!visible.length && !missing.length && <p className="muted">{eligible.length ? '没有匹配的编辑者' : '还没有可授权的编辑者'}</p>}
  </details>
}
