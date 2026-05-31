import { useState } from 'react'
import { getSettings, saveSettings } from '@/lib/notifications'
import type { NotificationSettings as Settings } from '@/lib/notifications'

const C = {
  bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  accent: 'var(--accent)',
}

/**
 * Notification preferences UI — triggers (what to notify about) and
 * delivery channels (email / SMS / Slack). Self-contained: reads/writes
 * to localStorage via lib/notifications.ts. Used inside the Setup page
 * so all configuration lives in one place.
 *
 * Saves on every change (no explicit Save button needed for local prefs).
 */
export function NotificationSettings() {
  const [settings, setSettings] = useState<Settings>(getSettings())
  const [savedAt, setSavedAt] = useState<number | null>(null)

  function update(patch: (s: Settings) => Settings) {
    setSettings(prev => {
      const next = patch(prev)
      saveSettings(next)
      setSavedAt(Date.now())
      return next
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* What to notify about */}
      <div>
        <SubsectionHeader>What to notify about</SubsectionHeader>
        <div style={{
          background: C.bg2, border: `1px solid ${C.border}`,
          borderRadius: 8, padding: 8,
        }}>
          <TriggerToggle
            label="Spike alerts"
            description="When a narrative gets 3×+ more articles in 24h than its baseline"
            on={settings.triggers.spike_alerts}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, spike_alerts: v } }))}
          />
          <TriggerToggle
            label="Viral narratives"
            description="When both article volume AND voter search are spiking on the same narrative"
            on={settings.triggers.viral_narratives}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, viral_narratives: v } }))}
          />
          <TriggerToggle
            label="Opponent attacks needing response"
            description="High-urgency defensive postures (opponent attacking, immediate response recommended)"
            on={settings.triggers.opponent_attacks}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, opponent_attacks: v } }))}
          />
          <TriggerToggle
            label="Review queue backed up"
            description="When 10+ articles are waiting to be triaged"
            on={settings.triggers.review_queue_full}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, review_queue_full: v } }))}
          />
          <TriggerToggle
            label="Proposed narratives pending"
            description="When the AI has proposed new narratives waiting to be promoted or dismissed"
            on={settings.triggers.proposed_narratives_pending}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, proposed_narratives_pending: v } }))}
          />
          <TriggerToggle
            label="Daily briefing ready"
            description="Send a notification when the morning briefing memo is regenerated"
            on={settings.triggers.daily_briefing}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, daily_briefing: v } }))}
          />
          <TriggerToggle
            label="Feed health alerts"
            description="When a source's body content collapses or a feed goes silent for 24h+"
            on={settings.triggers.ingestion_quality}
            onChange={v => update(s => ({ ...s, triggers: { ...s.triggers, ingestion_quality: v } }))}
          />
        </div>
      </div>

      {/* Delivery channels */}
      <div>
        <SubsectionHeader>Where to send them</SubsectionHeader>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ChannelCard
            label="Email"
            placeholder="you@campaign.com"
            value={settings.channels.email.address}
            enabled={settings.channels.email.enabled}
            onValueChange={v => update(s => ({ ...s, channels: { ...s.channels, email: { ...s.channels.email, address: v } } }))}
            onEnabledChange={v => update(s => ({ ...s, channels: { ...s.channels, email: { ...s.channels.email, enabled: v } } }))}
            hint="Daily digest + immediate alerts to your inbox"
          />
          <ChannelCard
            label="SMS / Text"
            placeholder="+1 555 123 4567"
            value={settings.channels.sms.phone}
            enabled={settings.channels.sms.enabled}
            onValueChange={v => update(s => ({ ...s, channels: { ...s.channels, sms: { ...s.channels.sms, phone: v } } }))}
            onEnabledChange={v => update(s => ({ ...s, channels: { ...s.channels, sms: { ...s.channels.sms, enabled: v } } }))}
            hint="Best for high-urgency only — opponent attacks, breaking spikes"
          />
          <ChannelCard
            label="Slack"
            placeholder="https://hooks.slack.com/services/..."
            value={settings.channels.slack.webhook_url}
            enabled={settings.channels.slack.enabled}
            onValueChange={v => update(s => ({ ...s, channels: { ...s.channels, slack: { ...s.channels.slack, webhook_url: v } } }))}
            onEnabledChange={v => update(s => ({ ...s, channels: { ...s.channels, slack: { ...s.channels.slack, enabled: v } } }))}
            hint="Posts to a channel via an incoming webhook URL"
          />
        </div>
      </div>

      {/* Backend-wiring note — honest about scope */}
      <div style={{
        padding: 14, background: 'rgba(255,191,0,0.08)',
        border: `1px solid rgba(255,191,0,0.3)`, borderRadius: 8,
        fontSize: 12, lineHeight: 1.5, color: C.text2,
      }}>
        <strong style={{ color: C.accent }}>Heads up:</strong> Your
        preferences save locally and the in-app notification feed works
        today. Actual outbound delivery (email send, SMS via Twilio, Slack
        webhook POST) needs a one-time backend setup — once those services
        are configured, the preferences here flip those channels on
        without any UI change.
      </div>

      {savedAt && (
        <div style={{ fontSize: 11, color: C.text3, textAlign: 'right' }}>
          Saved {formatRelative(new Date(savedAt).toISOString())}
        </div>
      )}
    </div>
  )
}

// ─────────────────────── Subcomponents ───────────────────────

function SubsectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <h3 style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
      textTransform: 'uppercase', color: C.text3,
      margin: '0 0 8px',
    }}>
      {children}
    </h3>
  )
}

function TriggerToggle({ label, description, on, onChange }: {
  label: string; description: string; on: boolean; onChange: (v: boolean) => void
}) {
  return (
    <label style={{
      display: 'flex', alignItems: 'flex-start', gap: 12,
      padding: '10px 8px',
      cursor: 'pointer', borderRadius: 6,
    }}>
      <input
        type="checkbox"
        checked={on}
        onChange={e => onChange(e.target.checked)}
        style={{
          marginTop: 3, width: 16, height: 16,
          accentColor: 'var(--accent)', cursor: 'pointer',
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: C.text1 }}>{label}</div>
        <div style={{ fontSize: 12, color: C.text3, marginTop: 2, lineHeight: 1.45 }}>
          {description}
        </div>
      </div>
    </label>
  )
}

function ChannelCard({ label, placeholder, value, enabled, onValueChange, onEnabledChange, hint }: {
  label: string
  placeholder: string
  value: string
  enabled: boolean
  onValueChange: (v: string) => void
  onEnabledChange: (v: boolean) => void
  hint: string
}) {
  return (
    <div style={{
      padding: 14, background: C.bg2,
      border: `1px solid ${C.border}`, borderRadius: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: C.text1, flex: 1 }}>{label}</span>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 12, color: C.text2 }}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={e => onEnabledChange(e.target.checked)}
            style={{ width: 14, height: 14, accentColor: 'var(--accent)' }}
          />
          Enabled
        </label>
      </div>
      <input
        type="text"
        className="input"
        value={value}
        onChange={e => onValueChange(e.target.value)}
        placeholder={placeholder}
        style={{ fontSize: 13 }}
      />
      <div style={{ fontSize: 11, color: C.text3, marginTop: 6 }}>{hint}</div>
    </div>
  )
}

function formatRelative(iso: string): string {
  const diffMin = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const h = Math.floor(diffMin / 60)
  if (h < 24) return `${h}h ago`
  return new Date(iso).toLocaleDateString()
}
