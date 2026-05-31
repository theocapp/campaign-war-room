import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

const THEME_KEY = 'cwr-theme'
export type Theme = 'dark' | 'light'

/**
 * Reactive read of the current theme attribute on <html>. Used by pages
 * that need the value at render time (e.g. Leaflet map needs a different
 * tile URL per theme — CSS variables can't reach inside the tile <img>).
 *
 * Subscribes to `MutationObserver` so any change to <html data-theme>
 * triggers a re-render — works regardless of who flipped it.
 */
export function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(() => {
    const v = document.documentElement.getAttribute('data-theme')
    return v === 'light' ? 'light' : 'dark'
  })
  useEffect(() => {
    const obs = new MutationObserver(() => {
      const v = document.documentElement.getAttribute('data-theme')
      setTheme(v === 'light' ? 'light' : 'dark')
    })
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => obs.disconnect()
  }, [])
  return theme
}

function readInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch { /* ignore */ }
  // Default is light so first-time visitors land on a softer surface; the
  // user can flip via the header toggle and that choice persists in
  // localStorage. We don't auto-pick from prefers-color-scheme — explicit
  // choice is safer.
  return 'light'
}

/**
 * Sun / moon toggle. Flips `data-theme` on <html>, persisted in localStorage.
 * Lives in the top header. Pages that use CSS variables (`--bg-1`, etc.)
 * will repaint immediately; pages still using raw hex stay dark.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readInitialTheme)

  // Apply the theme to <html> on mount AND on every change so the very
  // first paint matches the stored preference.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem(THEME_KEY, theme) } catch { /* ignore */ }
  }, [theme])

  const next: Theme = theme === 'dark' ? 'light' : 'dark'

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: 32, height: 32, borderRadius: 8,
        background: 'transparent',
        border: '1px solid var(--border)',
        color: 'var(--text-2)',
        cursor: 'pointer',
        transition: 'background 0.1s ease, color 0.1s ease',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.background = 'var(--bg-3)'
        e.currentTarget.style.color = 'var(--text-1)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.color = 'var(--text-2)'
      }}
    >
      {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  )
}
