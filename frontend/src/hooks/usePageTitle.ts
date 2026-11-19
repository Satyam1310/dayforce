import { useEffect } from 'react'

/** Sets the browser tab title, e.g. "Review queue – Northwind Retail". */
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} – Northwind Retail`
  }, [title])
}
