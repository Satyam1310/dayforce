import { useCallback, useEffect, useState } from 'react'

interface Result<T> {
  key: string
  depsKey: string
  data?: T
  error?: Error
}

export interface AsyncState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  reload: () => void
}

/**
 * Runs an async function when `deps` change and tracks loading / error / data.
 * `reload()` runs it again (e.g. after approving a proposal).
 * While reloading, the previous data stays visible to avoid flicker.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [tick, setTick] = useState(0)
  const [result, setResult] = useState<Result<T> | null>(null)
  const depsKey = JSON.stringify(deps)
  const key = `${depsKey}#${tick}`

  useEffect(() => {
    let cancelled = false
    fn().then(
      (data) => {
        if (!cancelled) setResult({ key, depsKey, data })
      },
      (err: unknown) => {
        if (!cancelled)
          setResult({ key, depsKey, error: err instanceof Error ? err : new Error(String(err)) })
      },
    )
    return () => {
      cancelled = true
    }
    // `fn` changes every render; `key` captures everything it depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  const fresh = result?.key === key
  const sameDeps = result?.depsKey === depsKey

  return {
    // keep showing old data during a reload(), but never data for other deps
    data: sameDeps ? result?.data : undefined,
    error: fresh ? result?.error : undefined,
    loading: !fresh,
    reload,
  }
}
