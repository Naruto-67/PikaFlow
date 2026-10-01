export default function Header({ isOwner, onLogout }) {
  return (
    <header className="border-b border-pika-700 bg-pika-800/60 backdrop-blur sticky top-0 z-50">
      <div className="max-w-5xl mx-auto flex items-center justify-between px-6 py-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl">🌸</span>
          <div>
            <h1 className="text-lg font-bold text-white leading-none">PikaFlow</h1>
            <p className="text-xs text-gray-400">Dev Panel</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {isOwner ? (
            <>
              <span className="badge-green">
                <span className="w-1.5 h-1.5 rounded-full bg-pika-green animate-pulse-slow" />
                Owner
              </span>
              <button onClick={onLogout} className="btn-ghost text-sm">
                Lock
              </button>
            </>
          ) : (
            <span className="badge-yellow">
              <span className="w-1.5 h-1.5 rounded-full bg-pika-yellow" />
              Read-only
            </span>
          )}
        </div>
      </div>
    </header>
  )
}

