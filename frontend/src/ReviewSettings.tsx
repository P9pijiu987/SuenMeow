import { useEffect, useState } from 'react'
import { api, type Data } from './api'

export function ReviewSettings({ admin, disabled, onChange }: { admin: boolean, disabled: boolean, onChange: (required: boolean) => void }) {
  const [settings, setSettings] = useState<Data | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const accept = (value: Data) => { setSettings(value); onChange(value.require_review) }
  useEffect(() => { void api('/topic-pipeline-settings').then(accept).catch(e => setError((e as Error).message)) }, [])
  const toggle = async (required: boolean) => {
    if (!settings || busy || disabled) return
    setBusy(true); setError('')
    try { accept(await api('/topic-pipeline-settings', 'PUT', { require_review: required, version: settings.version })) }
    catch (e) { setError((e as Error).message); try { accept(await api('/topic-pipeline-settings')) } catch {} }
    finally { setBusy(false) }
  }
  return <div className="panel review-settings"><label className="toggle-row"><span><strong>人格与个人贴编排需要管理员审核</strong><small>{settings?.require_review ? '已开启：保存为待审核草稿，管理员发布后生效。' : '默认关闭：人格与本人个人贴编排保存后生效。'} 开关变化保留已生效版本，不自动发布待审核修改。</small></span>{admin ? <input aria-label="开启人格与编排审核" type="checkbox" disabled={!settings || busy || disabled} checked={!!settings?.require_review} onChange={e => void toggle(e.target.checked)}/> : <span className="badge">{settings ? settings.require_review ? '审核已开启' : '保存后生效' : '读取中'}</span>}</label>{error && <p className="error" role="alert">{error}</p>}</div>
}
