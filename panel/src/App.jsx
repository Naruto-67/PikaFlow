import { useAuth }          from './hooks/useAuth'
import { useReleases }      from './hooks/useReleases'
import Header               from './components/Header'
import LoginCard            from './components/LoginCard'
import RunTrigger           from './components/RunTrigger'
import RunHistory           from './components/RunHistory'
import PipelineStatus       from './components/PipelineStatus'

export default function App() {
  const { token, isOwner, login, logout } = useAuth()
  const { releases, loading, error, refresh } = useReleases()

  return (
    <div className="min-h-screen">
      <Header isOwner={isOwner} onLogout={logout} />

      <main className="max-w-5xl mx-auto px-4 py-8 space-y-6">

        {/* Owner-only: login prompt or controls */}
        {!isOwner ? (
          <LoginCard onLogin={login} />
        ) : (
          <RunTrigger token={token} />
        )}

        {/* Always visible: pipeline status + run history */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <PipelineStatus />
          <div className="space-y-4">
            <div className="card p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Total Runs</p>
              <p className="text-3xl font-bold text-white">{releases.length}</p>
            </div>
            <div className="card p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wider mb-1">Latest Run</p>
              <p className="text-sm font-mono text-pika-glow">
                {releases[0]?.tag_name || '—'}
              </p>
            </div>
          </div>
        </div>

        <RunHistory
          releases={releases}
          loading={loading}
          error={error}
          onRefresh={refresh}
        />

      </main>

      <footer className="text-center text-gray-600 text-xs py-8">
        🌸 PikaFlow Dev Panel — public view · owner actions require token
      </footer>
    </div>
  )
}

