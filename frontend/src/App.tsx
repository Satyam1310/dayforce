import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { AuditLogPage } from './pages/AuditLogPage'
import { ExceptionDetailPage } from './pages/ExceptionDetailPage'
import { ExceptionsListPage } from './pages/ExceptionsListPage'
import { NotFoundPage } from './pages/NotFoundPage'

// Routes only. The router itself is in main.tsx (BrowserRouter) and in
// tests (MemoryRouter), so tests can start on any page.
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/exceptions" replace />} />
        <Route path="exceptions" element={<ExceptionsListPage />} />
        <Route path="exceptions/:id" element={<ExceptionDetailPage />} />
        <Route path="audit" element={<AuditLogPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
