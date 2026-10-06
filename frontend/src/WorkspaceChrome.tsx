import { ReactNode, useEffect, useState } from 'react'

type Theme = 'dark' | 'light' | 'rust'
const initialTheme = readTheme()

if (typeof document !== 'undefined') document.documentElement?.setAttribute('data-theme', initialTheme)

export function Header({ connection }: { connection: 'connected' | 'connecting' | 'unavailable' }) {
  return <header className="site-header">
    <a className="brand" href="#top" aria-label="Lazy Clipper home">
      <span className="brand-mark" aria-hidden="true"><BrandMark /></span>
      <span>Lazy Clipper</span>
    </a>
    <nav className="primary-nav" aria-label="Page navigation">
      <a href="#new-project">New project</a>
      <a href="#recent-projects">Recent projects</a>
    </nav>
    <div className="header-tools">
      <div className={`api-indicator ${connection}`} role="status" aria-label={`API ${connection}`} title="Connection based on the latest project API requests">
        <span className="status-dot" /><span>API</span>
        <strong>{connection === 'connected' ? 'Connected' : connection === 'connecting' ? 'Connecting' : 'Unavailable'}</strong>
      </div>
      <ThemeSwitcher />
    </div>
  </header>
}

function ThemeSwitcher() {
  const [theme, setTheme] = useState<Theme>(initialTheme)

  useEffect(() => {
    if (typeof document !== 'undefined') document.documentElement?.setAttribute('data-theme', theme)
    try {
      window.localStorage?.setItem('lazy-clipper-theme', theme)
    } catch {
      // Theme switching still works when the browser blocks persistence.
    }
  }, [theme])

  return <label className="theme-switcher">
    <span className="sr-only">Color theme</span>
    <IconPalette />
    <select aria-label="Color theme" value={theme} onChange={(event) => setTheme(event.target.value as Theme)}>
      <option value="dark">Dark</option>
      <option value="light">Light</option>
      <option value="rust">Rust</option>
    </select>
  </label>
}

export function Hero() {
  return <section className="hero" aria-labelledby="hero-heading">
    <div className="hero-copy">
      <p className="eyebrow">VIDEO WORKSPACE</p>
      <h1 id="hero-heading">Lazy <em>Clipper</em></h1>
      <p className="hero-tagline">Find the moments worth keeping.</p>
      <p className="hero-description">Paste a YouTube video and let Lazy Clipper transcribe, analyze, and surface the moments worth clipping.</p>
      <div className="capabilities" aria-label="Available capabilities">
        <span><IconWaveform /> Timestamped transcript</span>
        <span><IconSpark /> Smart moments</span>
        <span><IconScissors /> Exportable clips</span>
      </div>
    </div>
    <div className="hero-visual" aria-hidden="true">
      <div className="hero-window">
        <div className="window-toolbar"><span /><span /><span /><small>VIDEO / WORKSPACE</small></div>
        <div className="hero-frame"><span className="hero-play"><IconPlay /></span><span className="frame-label">SOURCE VIDEO</span></div>
        <div className="hero-timeline">
          <div className="waveform">{Array.from({ length: 32 }, (_, index) => <i key={index} />)}</div>
          <span className="timeline-marker marker-one" /><span className="timeline-marker marker-two" />
          <div className="timeline-caption"><span>FULL-SOURCE ANALYSIS</span><strong>VIDEO → MOMENTS</strong></div>
        </div>
      </div>
      <div className="hero-note"><IconWaveform /> Timestamped words <strong>PRECISE CLIPS</strong></div>
    </div>
  </section>
}

export function EmptyProjects() {
  return <div className="empty-projects">
    <span className="empty-project-icon" aria-hidden="true"><IconFilm /></span>
    <div><strong>No projects yet</strong><p>Create your first project and Lazy Clipper will start finding the moments worth keeping.</p></div>
  </div>
}

export function EmptyWorkspace() {
  return <div className="welcome-empty panel">
    <div className="workspace-art" aria-hidden="true"><span /><span /><span /><i /></div>
    <div><p className="eyebrow">READY WHEN YOU ARE</p><h2>Your workspace is ready.</h2><p className="muted">Create a project to inspect the video, transcript, and detected moments.</p></div>
  </div>
}

export function Footer() {
  return <footer className="site-footer">
    <div><a className="footer-brand" href="#top"><span className="brand-mark" aria-hidden="true"><BrandMark /></span> Lazy Clipper</a><p>Find the moments worth keeping.</p></div>
    <div className="footer-meta"><span>Built by Neeraj Kumar</span><a href="https://github.com/drunktrader" target="_blank" rel="noreferrer"><IconGithub /> GitHub <span aria-hidden="true">↗</span></a></div>
  </footer>
}

function readTheme(): Theme {
  if (typeof window === 'undefined') return 'dark'
  try {
    const saved = window.localStorage?.getItem('lazy-clipper-theme')
    if (saved === 'dark' || saved === 'light' || saved === 'rust') return saved
    if (window.matchMedia?.('(prefers-color-scheme: light)').matches) return 'light'
  } catch {
    // Use the stable dark default when browser preference APIs are unavailable.
  }
  return 'dark'
}

function Icon({ children }: { children: ReactNode }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
}

function BrandMark() { return <Icon><path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z" /><path d="m8 9.75 4-2.25 4 2.25v4.5l-4 2.25-4-2.25z" /></Icon> }
export function IconLink() { return <Icon><path d="M10 13a5 5 0 0 0 7.07.07l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15" /><path d="M14 11a5 5 0 0 0-7.07-.07l-2 2A5 5 0 0 0 12 20l1.15-1.15" /></Icon> }
export function IconX() { return <Icon><path d="m7 7 10 10M17 7 7 17" /></Icon> }
export function IconArrow() { return <Icon><path d="M5 12h13M13 6l6 6-6 6" /></Icon> }
export function IconFilm() { return <Icon><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 5v14M17 5v14M3 9h4M17 9h4M3 15h4M17 15h4" /></Icon> }
function IconPlay() { return <Icon><path d="m9 7 8 5-8 5z" /></Icon> }
function IconPalette() { return <Icon><path d="M12 3a9 9 0 0 0 0 18h1.5a2 2 0 0 0 0-4H12a2 2 0 0 1 0-4h1.5A7.5 7.5 0 0 0 12 3Z" /><path d="M7 10h.01M9 7h.01M14 7h.01M17 10h.01" /></Icon> }
function IconGithub() { return <Icon><path d="M15 22v-3.5c0-1-.35-1.75-1-2.5 3.3-.35 6.75-1.6 6.75-7.25a5.65 5.65 0 0 0-1.5-3.95A5.25 5.25 0 0 0 19.1 1S17.85.6 15 2.55a13.2 13.2 0 0 0-6 0C6.15.6 4.9 1 4.9 1a5.25 5.25 0 0 0-.15 3.8 5.65 5.65 0 0 0-1.5 3.95C3.25 14.4 6.7 15.65 10 16c-.65.75-1 1.5-1 2.5V22" /><path d="M9 19c-3 .95-3-1.5-4.25-1.5" /></Icon> }
function IconWaveform() { return <Icon><path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4M21 12h0" /></Icon> }
function IconSpark() { return <Icon><path d="m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4zM19 16v5M16.5 18.5h5" /></Icon> }
function IconScissors() { return <Icon><circle cx="6" cy="6" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="m8 7.5 12 8M8 16.5l12-8" /></Icon> }
