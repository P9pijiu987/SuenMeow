import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { ChevronRight, Shield } from 'lucide-react'
import { api, type Data } from './api'
import { ForumLogin } from './ForumLogin'

export function AuthPage({ onLogin, story }: { onLogin: (user: Data) => void, story: ReactNode }) {
  const [mode, setMode] = useState('forum'), [enabled, setEnabled] = useState<boolean | null>(null), [forumEnabled, setForumEnabled] = useState(false)
  const [name, setName] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('')
  const [code, setCode] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => { void api('/auth/registration').then(data => setEnabled(data.enabled)).catch(() => setEnabled(false)) }, [])
  useEffect(() => { void api('/auth/forum').then(data => { setForumEnabled(data.enabled); if (!data.enabled) setMode('login') }).catch(() => setMode('login')) }, [])
  const register = mode === 'register'
  const switchMode = (next: string) => { setMode(next); setPassword(''); setConfirm(''); setCode(''); setError('') }
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError('')
    if (register && password !== confirm) { setError('两次输入的密码不一致'); return }
    setBusy(true)
    try { onLogin(await api(register ? '/auth/register' : '/auth/login', 'POST', register ? { username: name, password } : { username: name, password, code })) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  if (mode === 'forum') return <main className="login-page">{story}<ForumLogin onLogin={onLogin} localLogin={() => switchMode('login')}/></main>
  return <main className="login-page">{story}<form className="login-card" onSubmit={submit}>
    <div className="tabs auth-tabs"><button type="button" disabled={busy} className={!register ? 'active' : ''} onClick={() => switchMode('login')}>登录</button>{enabled && <button type="button" disabled={busy} className={register ? 'active' : ''} onClick={() => switchMode('register')}>注册编辑者</button>}</div>
    <span className="badge green"><Shield size={13}/> {register ? '受限编辑者账户' : '私密控制室'}</span>
    <h2>{register ? '一起写一点灵感。' : '欢迎回来'}</h2>
    <p>{register ? '注册后立即可用。创建自己的提示词，或协作编辑管理员授权的模块。' : '登录后照看你的 SuenMeow。'}</p>
    <fieldset disabled={busy}>
      <label className="field"><span>用户名</span><input required autoComplete="username" minLength={register ? 2 : 1} maxLength={80} value={name} onChange={e => setName(e.target.value)}/>{register && <small>2–80 个字符，可用字母、汉字、数字、下划线、点和短横线</small>}</label>
      <label className="field"><span>密码</span><input required type="password" minLength={register ? 12 : 1} maxLength={256} autoComplete={register ? 'new-password' : 'current-password'} value={password} onChange={e => setPassword(e.target.value)}/>{register && <small>至少 12 个字符，建议使用独立的长密码</small>}</label>
      {register ? <label className="field"><span>确认密码</span><input required type="password" minLength={12} maxLength={256} autoComplete="new-password" value={confirm} onChange={e => setConfirm(e.target.value)}/></label> : <label className="field"><span>两步验证码</span><input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={e => setCode(e.target.value)}/><small>已开启两步验证时填写</small></label>}
    </fieldset>
    {error && <p role="alert" className="error">{error}</p>}
    <button className="primary" disabled={busy}>{busy ? '正在验证…' : register ? '注册并进入工作区' : '进入控制室'}<ChevronRight size={16}/></button>
    <small className="muted">{register ? '全局编排、发布、模型连接及论坛身份绑定由管理员管理。' : enabled === false ? '注册暂时关闭，请联系管理员。' : '编辑者的保存只形成草稿，发布由管理员完成。'}</small>
    {forumEnabled && <button type="button" disabled={busy} onClick={() => switchMode('forum')}>使用论坛私信登录</button>}
  </form></main>
}
