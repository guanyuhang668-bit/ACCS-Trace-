import { Navigate, Route, Routes } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import './App.css'
import { AppShell } from './components/AppShell'
import { PageLoading } from './components/QueryState'

const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const ProjectPage = lazy(() => import('./pages/ProjectPage').then((module) => ({ default: module.ProjectPage })))
const RunPage = lazy(() => import('./pages/RunPage').then((module) => ({ default: module.RunPage })))
const ModelsPage = lazy(() => import('./pages/ModelsPage').then((module) => ({ default: module.ModelsPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((module) => ({ default: module.NotFoundPage })))

export function AppRoutes() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate replace to="/projects" />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="projects/:projectId" element={<ProjectPage />} />
          <Route path="runs/:runId" element={<RunPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

export default AppRoutes
