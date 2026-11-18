import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <section className="state">
      <h1>Page not found</h1>
      <Link to="/exceptions">Go to exceptions</Link>
    </section>
  )
}
